from __future__ import annotations

import asyncio
import difflib
import json
import re
import shutil
import time as _time
from datetime import UTC, datetime
from pathlib import Path

from textual import on, work
from textual.app import App
from textual.css.query import NoMatches
from textual.events import Resize
from textual.widgets import Button, DirectoryTree, Input

from cereal_killer.engine import LLMEngine
from cereal_killer.knowledge_base import KnowledgeBase
from cereal_killer.observer import (
    ClipboardImageDetected,
    ClipboardImageWatcher,
    ascii_preview_for_image,
    clear_clipboard_buffer,
    observe_history_events,
)
from mentor.engine.commands import CommandResult, dispatch as dispatch_command
from mentor.engine.methodology import audit_command as audit_methodology
from mentor.kb.query import retrieve_solution_for_machine
from mentor.ui.phase import detect_phase
from mentor.ui.startup import run_boot_sequence

from .screens import MainDashboard, SolutionModal
from .widgets import PulsingEasyButton


_AUTO_COACH_COOLDOWN_SECS = 10
CODE_BLOCK_PATTERN = re.compile(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", re.DOTALL)
VISION_BUFFER_PATH = Path("data/temp/clipboard_obs.png")
VISION_PROMPT = (
    "Zero Cool, I've just pasted a screenshot. "
    "Look at the error/output and tell me where I'm failing."
)
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


class CerealKillerApp(App[None]):
    CSS_PATH = Path(__file__).with_name("styles.tcss")
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+b", "pulse_easy_button", "Easy Button"),
        ("u", "toggle_upload_tree", "Toggle Upload Tree"),
    ]

    def __init__(self, engine: LLMEngine, kb: KnowledgeBase) -> None:
        super().__init__()
        self.engine = engine
        self.kb = kb
        self.title = "CEREAL KILLER"
        self.sub_title = "TARGET: NONE"
        self.history_context: list[str] = []
        self.observer_task: asyncio.Task[None] | None = None
        self.clipboard_task: asyncio.Task[None] | None = None
        self.clipboard_watcher = ClipboardImageWatcher(output_path=VISION_BUFFER_PATH)
        self.last_code_block = ""
        self.pathetic_meter = 0
        self.easy_usage_count = 0
        self.successful_command_count = 0
        self.chat_transcript: list[dict[str, str]] = []
        self.current_target: str = ""
        self._pruning_in_flight = False
        self._analysis_jobs = 0
        self._uploaded_image_path: Path | None = None

    def _dashboard(self) -> MainDashboard:
        screen = self.screen
        if not isinstance(screen, MainDashboard):
            raise RuntimeError("MainDashboard is not active")
        return screen

    async def on_mount(self) -> None:
        await self.push_screen(MainDashboard())
        dashboard = self._dashboard()
        dashboard.apply_responsive_layout(self.size.width)
        dashboard.set_phase("[IDLE]")
        dashboard.set_upload_root(Path.cwd())
        dashboard.set_loading(False)
        self.set_interval(0.7, self._pulse_easy_button)
        self.set_interval(300, self._schedule_persist_mental_state)
        self.set_interval(60, self._schedule_context_prune)
        self.observer_task = asyncio.create_task(self._observe())
        self.clipboard_task = asyncio.create_task(self._watch_clipboard())
        asyncio.create_task(self._run_boot_sequence())
        if hasattr(self.engine, "set_web_search_callback"):
            self.engine.set_web_search_callback(self._on_web_search_state)

    async def on_unmount(self) -> None:
        if self.observer_task:
            self.observer_task.cancel()
        if self.clipboard_task:
            self.clipboard_task.cancel()
        if hasattr(self.engine, "persist_mental_state"):
            await self.engine.persist_mental_state(self.history_context)
        self._save_session_snapshot("app-close")

    def on_resize(self, event: Resize) -> None:
        try:
            self._dashboard().apply_responsive_layout(event.size.width)
        except Exception:
            return

    @on(Input.Submitted, "#command_input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt:
            return
        event.input.value = ""
        dashboard = self._dashboard()
        dashboard.append_user(prompt)
        self._append_chat("user", prompt)
        if prompt.startswith("/"):
            dashboard.set_active_tool("CommandProcessor")
            asyncio.create_task(self._handle_command(prompt))
        else:
            self._run_chat_worker(prompt)

    @on(DirectoryTree.FileSelected, "#upload_tree")
    def on_upload_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        path = Path(event.path)
        if not self._is_image_file(path):
            self.notify("Select an image file to analyze", title="Upload", severity="warning")
            return
        self._prime_uploaded_image(path, source="DirectoryTree")
        self._run_vision_worker(str(path), source_label="DirectoryTree")

    def action_toggle_upload_tree(self) -> None:
        self._dashboard().toggle_upload_tree()

    async def _handle_command(self, prompt: str) -> None:
        dashboard = self._dashboard()
        result = await dispatch_command(prompt, self.engine, self.kb.settings)
        if result is None:
            self._run_chat_worker(prompt)
            return

        await self._apply_command_result(result)
        if result.session_prefix == "__exit__":
            self.exit()
            return
        if result.session_prefix == "__loot__":
            self._run_loot_worker()
            return
        if result.session_prefix == "__vision__":
            self._run_vision_worker(str(VISION_BUFFER_PATH), source_label="Clipboard")
            return
        if result.session_prefix == "__upload__":
            if result.upload_image_path:
                upload_path = Path(result.upload_image_path)
                self._prime_uploaded_image(upload_path, source="/upload")
                self._run_vision_worker(str(upload_path), source_label="/upload")
            else:
                dashboard.append_system("Upload command did not provide a path.", style="red")
            return

        dashboard.set_active_tool("Idle")

    @work(exclusive=True, thread=False, group="llm")
    async def _run_chat_worker(self, prompt: str) -> None:
        dashboard = self._dashboard()
        self._analysis_busy(True)
        dashboard.set_active_tool("Brain")
        try:
            response = await self.engine.chat(
                prompt,
                self.history_context,
                pathetic_meter=self.pathetic_meter,
            )
            await self._consume_llm_response(response.answer, response.reasoning_content or response.thought)
            phase = detect_phase(self.history_context)
            dashboard.set_phase(phase)
            if "pwned" in prompt.lower() or "owned" in prompt.lower():
                self._save_session_snapshot("pwned-manual")
        except Exception as exc:
            dashboard.append_system(f"LLM error: {exc}", style="red")
        finally:
            dashboard.set_active_tool("Idle")
            self._analysis_busy(False)

    @work(exclusive=True, thread=False, group="llm")
    async def _run_loot_worker(self) -> None:
        dashboard = self._dashboard()
        machine_name = Path.cwd().name
        self._analysis_busy(True)
        dashboard.set_active_tool("Loot")
        dashboard.append_system(f"Generating loot report for {machine_name}...", style="bold green")
        try:
            response = await self.engine.generate_loot_report(
                history_commands=self.history_context,
                pathetic_meter=self.pathetic_meter,
            )
            await self._consume_llm_response(response.answer, response.reasoning_content or response.thought)
        except Exception as exc:
            dashboard.append_system(f"Loot report error: {exc}", style="red")
        finally:
            dashboard.set_active_tool("Idle")
            self._analysis_busy(False)

    @work(exclusive=True, thread=False, group="llm")
    async def _run_vision_worker(self, image_path: str, source_label: str = "Clipboard") -> None:
        dashboard = self._dashboard()
        image_file = Path(image_path)
        if not image_file.exists():
            dashboard.append_system(f"Vision input missing: {image_file}", style="yellow")
            return

        self._analysis_busy(True)
        dashboard.set_active_tool("Vision")
        dashboard.append_system(f"Image Uploaded: {image_file.name} ({source_label})", style="bold cyan")
        dashboard.append_system(f"Zero Cool is analyzing {image_file.name}...", style="bold green")
        try:
            response = await self.engine.chat_with_image(
                user_prompt=VISION_PROMPT,
                image_path=str(image_file),
                history_commands=self.history_context,
                pathetic_meter=self.pathetic_meter,
            )
            await self._consume_llm_response(response.answer, response.reasoning_content or response.thought)
        except Exception as exc:
            dashboard.append_system(f"Vision analysis error: {exc}", style="red")
        finally:
            dashboard.set_active_tool("Idle")
            self._analysis_busy(False)

    @work(exclusive=True, thread=False, group="llm")
    async def _run_autocoach_worker(self, command: str) -> None:
        dashboard = self._dashboard()
        self._analysis_busy(True)
        dashboard.set_active_tool("Brain")
        try:
            response = await self.engine.react_to_command(
                command,
                self.history_context,
                pathetic_meter=self.pathetic_meter,
            )
            await self._consume_llm_response(response.answer, response.reasoning_content or response.thought)
        except Exception as exc:
            dashboard.append_system(f"Auto-coach error: {exc}", style="red")
        finally:
            dashboard.set_active_tool("Idle")
            self._analysis_busy(False)

    async def _consume_llm_response(self, answer: str, thought: str) -> None:
        dashboard = self._dashboard()
        try:
            await self._safe_stream_thought(thought)
            self._track_code_block(answer)
            self._warn_if_repetitive_response(answer)
            self._append_chat("assistant", answer)
            dashboard.append_assistant(answer)
        except Exception as exc:
            dashboard.append_system(f"UI post-processing error: {exc}", style="red")

    def _analysis_busy(self, active: bool) -> None:
        if active:
            self._analysis_jobs += 1
        else:
            self._analysis_jobs = max(0, self._analysis_jobs - 1)
        self._dashboard().set_loading(self._analysis_jobs > 0)

    async def _run_boot_sequence(self) -> None:
        dashboard = self._dashboard()
        if not hasattr(self.engine, "settings"):
            return
        async for result in run_boot_sequence(self.engine.settings):
            dashboard.append_system(result.message, style="dim")
            await asyncio.sleep(0)
        greeting = await self.engine.returning_greeting()
        if greeting:
            dashboard.append_assistant(greeting)
            self._append_chat("assistant", greeting)

    async def _observe(self) -> None:
        cwd = str(Path.cwd())
        dashboard = self._dashboard()
        last_auto_coach_time: float = 0.0

        async for event in observe_history_events(cwd):
            if event.json_hint:
                self._append_chat("assistant", event.json_hint)
                dashboard.append_assistant(event.json_hint)
                continue

            if not event.command:
                continue

            self.history_context = event.context_commands
            phase = detect_phase(self.history_context)
            dashboard.set_phase(phase)
            self.engine.record_phase_change(phase)
            self.engine.record_command_progress()

            audit_warning = audit_methodology(event.command, self.history_context)
            if audit_warning:
                dashboard.append_system(audit_warning, style="bold red")
                self._append_chat("assistant", audit_warning)

            inferred_target = event.cd_target or event.host_target
            if inferred_target and inferred_target != self.current_target:
                auto_cmd = f"/box {inferred_target}"
                cmd_result = await dispatch_command(auto_cmd, self.engine, self.kb.settings)
                if cmd_result is not None:
                    await self._apply_command_result(cmd_result)

            if not event.trigger_brain:
                continue

            now = _time.monotonic()
            if now - last_auto_coach_time < _AUTO_COACH_COOLDOWN_SECS:
                continue
            last_auto_coach_time = now
            self._run_autocoach_worker(event.command)

    async def _watch_clipboard(self) -> None:
        async for detected in self.clipboard_watcher.watch():
            self.post_message(detected)

    def on_clipboard_image_detected(self, message: ClipboardImageDetected) -> None:
        snapshot = message.snapshot
        description = f"{snapshot.image_path.name}"
        self._dashboard().set_visual_buffer(description, snapshot.preview)
        self._uploaded_image_path = snapshot.image_path
        self.notify(
            f"Clipboard image buffered as {snapshot.image_path.name}",
            title="Visual Buffer",
            severity="information",
        )

    @on(Button.Pressed, "#clear_visual_buffer")
    def clear_visual_buffer(self) -> None:
        ok = clear_clipboard_buffer(VISION_BUFFER_PATH)
        self._dashboard().clear_visual_buffer()
        self._uploaded_image_path = None
        if ok:
            self.notify("Visual buffer cleared", title="Visual Buffer", severity="information")
        else:
            self.notify("Could not clear visual buffer", title="Visual Buffer", severity="warning")

    def _prime_uploaded_image(self, path: Path, source: str) -> None:
        resolved = path.expanduser().resolve()
        self._uploaded_image_path = resolved

        VISION_BUFFER_PATH.parent.mkdir(parents=True, exist_ok=True)
        if resolved != VISION_BUFFER_PATH:
            try:
                shutil.copyfile(resolved, VISION_BUFFER_PATH)
            except Exception:
                pass

        preview = ascii_preview_for_image(resolved)
        self._dashboard().set_visual_buffer(f"{resolved.name} ({source})", preview)
        self._dashboard().append_system(f"Image Uploaded: {resolved.name}", style="bold cyan")

    @staticmethod
    def _is_image_file(path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES

    def _on_web_search_state(self, active: bool) -> None:
        dashboard = self._dashboard()
        dashboard.set_active_tool("Web Search" if active else "Idle")

    @on(Button.Pressed, "#easy_button")
    def show_walkthrough(self) -> None:
        self._record_easy_usage()
        machine_name = Path.cwd().name
        solution_markdown = retrieve_solution_for_machine(self.kb.settings, machine_name)
        self.push_screen(SolutionModal(solution_markdown))

    def action_pulse_easy_button(self) -> None:
        easy_button = self._get_easy_button()
        if easy_button is None:
            return
        easy_button.pulse_once()

    def _pulse_easy_button(self) -> None:
        easy_button = self._get_easy_button()
        if easy_button is None:
            return
        easy_button.pulse_once()

    def _get_easy_button(self) -> PulsingEasyButton | None:
        """Return the easy button when the dashboard is active, else None."""
        try:
            return self._dashboard().query_one("#easy_button", PulsingEasyButton)
        except (RuntimeError, NoMatches):
            return None

    async def _apply_command_result(self, result: CommandResult) -> None:
        dashboard = self._dashboard()
        dashboard.append_system(result.message, style="cyan")
        self._append_chat("assistant", result.message)

        if result.system_prompt_addendum is not None:
            self.engine.set_system_prompt_addendum(result.system_prompt_addendum)

        if result.new_target:
            self.current_target = result.new_target
            self._update_header_target(result.new_target)
            dashboard.set_upload_root(Path.cwd())
            self.notify(
                f"Context switched -> {result.new_target.upper()}",
                title="Target Loaded",
                severity="information",
            )
            self.engine.record_phase_change("[IDLE]")

        if result.reset_phase:
            dashboard.set_phase("[IDLE]")

    def _schedule_persist_mental_state(self) -> None:
        if hasattr(self.engine, "persist_mental_state"):
            asyncio.create_task(self.engine.persist_mental_state(self.history_context))

    def _schedule_context_prune(self) -> None:
        if not self._pruning_in_flight:
            asyncio.create_task(self._maybe_prune_transcript())

    async def _maybe_prune_transcript(self) -> None:
        if self._pruning_in_flight:
            return
        total_chars = sum(len(e.get("text", "")) for e in self.chat_transcript)
        threshold = self.engine.prune_threshold()
        if total_chars <= threshold:
            return
        self._pruning_in_flight = True
        try:
            target = self.engine.prune_target()
            chars_to_drop = total_chars - target
            entries_to_summarize: list[dict[str, str]] = []
            dropped = 0
            for entry in self.chat_transcript:
                if dropped >= chars_to_drop:
                    break
                entries_to_summarize.append(entry)
                dropped += len(entry.get("text", ""))
            if not entries_to_summarize:
                return
            blob = "\n".join(
                f"{e.get('role', 'unknown')}: {e.get('text', '')}" for e in entries_to_summarize
            )
            summary = await self.engine.summarize_session(blob)
            summary_entry = {
                "role": "summary",
                "text": summary,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            remaining = self.chat_transcript[len(entries_to_summarize):]
            self.chat_transcript = [summary_entry, *remaining]
        finally:
            self._pruning_in_flight = False

    def _append_chat(self, role: str, text: str) -> None:
        self.chat_transcript.append(
            {
                "role": role,
                "text": text,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

    def _warn_if_repetitive_response(self, new_response: str) -> None:
        last_assistant = ""
        for entry in reversed(self.chat_transcript):
            if entry.get("role") == "assistant":
                last_assistant = str(entry.get("text", ""))
                break
        if not last_assistant or not new_response:
            return
        ratio = difflib.SequenceMatcher(None, last_assistant, new_response).ratio()
        if ratio >= 0.90:
            self.notify(
                "[System] Zero Cool is repeating himself. Try providing more specific tool output.",
                title="Repetition Warning",
                severity="warning",
            )

    async def _safe_stream_thought(self, thought: str) -> None:
        """Best-effort thought streaming with backward-compatible fallbacks."""
        dashboard = self._dashboard()
        stream_method = getattr(dashboard, "stream_thought", None)
        if callable(stream_method):
            await stream_method(thought)
            return
        thought_box_method = getattr(dashboard, "thought_box", None)
        if callable(thought_box_method):
            thought_box = thought_box_method()
            if thought_box is not None and hasattr(thought_box, "stream_thought"):
                await thought_box.stream_thought(thought)

    def _track_code_block(self, response_text: str) -> None:
        matches = CODE_BLOCK_PATTERN.findall(response_text)
        if matches:
            self.last_code_block = matches[-1].strip()

    def _adjust_pathetic_meter(self) -> None:
        total = self.easy_usage_count + self.successful_command_count
        if total <= 0:
            self.pathetic_meter = 0
        else:
            ratio = self.easy_usage_count / total
            self.pathetic_meter = max(0, min(10, round(ratio * 10)))
        self._dashboard().set_pathetic_meter(self.pathetic_meter)

    def _record_easy_usage(self, weight: int = 1) -> None:
        self.easy_usage_count += max(1, weight)
        self._adjust_pathetic_meter()

    def _update_header_target(self, target: str | None = None) -> None:
        active_target = (target or self.current_target or "NONE").upper()
        self.title = "CEREAL KILLER"
        self.sub_title = f"TARGET: {active_target}"

    def _save_session_snapshot(self, reason: str) -> None:
        session_dir = Path("data/sessions")
        session_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        payload = {
            "reason": reason,
            "timestamp": datetime.now(UTC).isoformat(),
            "cwd": str(Path.cwd()),
            "phase": detect_phase(self.history_context),
            "pathetic_meter": self.pathetic_meter,
            "history_context": self.history_context,
            "last_code_block": self.last_code_block,
            "chat": self.chat_transcript,
        }
        target = session_dir / f"zero-cool-session-{timestamp}.json"
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

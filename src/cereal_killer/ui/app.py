from __future__ import annotations

import asyncio
import json
import re
import time as _time
from datetime import UTC, datetime
from pathlib import Path

from textual import on
from textual.app import App
from textual.css.query import NoMatches
from textual.events import Resize
from textual.widgets import Button, Input

from cereal_killer.engine import LLMEngine
from cereal_killer.knowledge_base import KnowledgeBase
from cereal_killer.observer import observe_history_events
from mentor.engine.commands import CommandResult, dispatch as dispatch_command
from mentor.engine.methodology import audit_command as audit_methodology
from mentor.kb.query import retrieve_solution_for_machine
from mentor.ui.phase import detect_phase
from mentor.ui.startup import run_boot_sequence

from .screens import MainDashboard, SolutionModal
from .widgets import PulsingEasyButton


_AUTO_COACH_COOLDOWN_SECS = 10
CODE_BLOCK_PATTERN = re.compile(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", re.DOTALL)


class CerealKillerApp(App[None]):
    CSS_PATH = Path(__file__).with_name("styles.tcss")
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+t", "toggle_thinking", "Toggle Thinking"),
        ("ctrl+b", "pulse_easy_button", "Easy Button"),
    ]

    def __init__(self, engine: LLMEngine, kb: KnowledgeBase) -> None:
        super().__init__()
        self.engine = engine
        self.kb = kb
        self.title = "CEREAL KILLER"
        self.sub_title = "TARGET: NONE"
        self.history_context: list[str] = []
        self.observer_task: asyncio.Task[None] | None = None
        self.last_code_block = ""
        self.pathetic_meter = 0
        self.easy_usage_count = 0
        self.successful_command_count = 0
        self.chat_transcript: list[dict[str, str]] = []
        self.current_target: str = ""
        self._pruning_in_flight = False

    def _dashboard(self) -> MainDashboard:
        screen = self.screen
        if not isinstance(screen, MainDashboard):
            raise RuntimeError("MainDashboard is not active")
        return screen

    async def on_mount(self) -> None:
        await self.push_screen(MainDashboard())
        self._dashboard().apply_responsive_layout(self.size.width)
        self._dashboard().set_phase("[IDLE]")
        self.set_interval(0.7, self._pulse_easy_button)
        self.set_interval(300, self._schedule_persist_mental_state)
        self.set_interval(60, self._schedule_context_prune)
        self.observer_task = asyncio.create_task(self._observe())
        asyncio.create_task(self._run_boot_sequence())
        if hasattr(self.engine, "set_web_search_callback"):
            self.engine.set_web_search_callback(self._on_web_search_state)

    async def on_unmount(self) -> None:
        if self.observer_task:
            self.observer_task.cancel()
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
            dashboard.set_active_tool("Brain")
            asyncio.create_task(self._handle_brain_prompt(prompt))

    async def _handle_command(self, prompt: str) -> None:
        dashboard = self._dashboard()
        result = await dispatch_command(prompt, self.engine, self.kb.settings)
        if result is None:
            await self._handle_brain_prompt(prompt)
            return
        await self._apply_command_result(result)
        if result.session_prefix == "__loot__":
            await self._handle_loot_report()
        dashboard.set_active_tool("Idle")

    async def _handle_brain_prompt(self, prompt: str) -> None:
        dashboard = self._dashboard()
        try:
            response = await self.engine.chat(
                prompt,
                self.history_context,
                pathetic_meter=self.pathetic_meter,
            )
        except Exception as exc:
            dashboard.append_system(f"LLM error: {exc}", style="red")
            dashboard.set_active_tool("Idle")
            return
        await dashboard.thought_box().stream_thought(response.reasoning_content or response.thought)
        self._track_code_block(response.answer)
        self._append_chat("assistant", response.answer)
        dashboard.append_assistant(response.answer)
        phase = detect_phase(self.history_context)
        dashboard.set_phase(phase)
        if "pwned" in prompt.lower() or "owned" in prompt.lower():
            self._save_session_snapshot("pwned-manual")
        dashboard.set_active_tool("Idle")

    async def _handle_loot_report(self) -> None:
        dashboard = self._dashboard()
        machine_name = Path.cwd().name
        dashboard.append_system(f"Generating loot report for {machine_name}...", style="bold green")
        try:
            response = await self.engine.generate_loot_report(
                history_commands=self.history_context,
                pathetic_meter=self.pathetic_meter,
            )
        except Exception as exc:
            dashboard.append_system(f"Loot report error: {exc}", style="red")
            return
        await dashboard.thought_box().stream_thought(response.reasoning_content or response.thought)
        dashboard.append_assistant(response.answer)
        self._append_chat("assistant", response.answer)

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
        _last_auto_coach_time: float = 0.0

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

            if event.cd_target and event.cd_target != self.current_target:
                auto_cmd = f"/box {event.cd_target}"
                cmd_result = await dispatch_command(auto_cmd, self.engine, self.kb.settings)
                if cmd_result is not None:
                    await self._apply_command_result(cmd_result)

            if not event.trigger_brain:
                continue

            now = _time.monotonic()
            if now - _last_auto_coach_time < _AUTO_COACH_COOLDOWN_SECS:
                continue
            _last_auto_coach_time = now

            dashboard.set_active_tool("Brain")
            try:
                response = await self.engine.react_to_command(
                    event.command,
                    self.history_context,
                    pathetic_meter=self.pathetic_meter,
                )
            except Exception as exc:
                dashboard.append_system(f"Auto-coach error: {exc}", style="red")
                dashboard.set_active_tool("Idle")
                continue

            await dashboard.thought_box().stream_thought(response.reasoning_content or response.thought)
            self._append_chat("assistant", response.answer)
            self._track_code_block(response.answer)
            dashboard.append_assistant(response.answer)
            dashboard.set_active_tool("Idle")

    def _on_web_search_state(self, active: bool) -> None:
        dashboard = self._dashboard()
        dashboard.set_active_tool("Web Search" if active else "Idle")

    @on(Button.Pressed, "#easy_button")
    def show_walkthrough(self) -> None:
        self._record_easy_usage()
        machine_name = Path.cwd().name
        solution_markdown = retrieve_solution_for_machine(self.kb.settings, machine_name)
        self.push_screen(SolutionModal(solution_markdown))

    def action_toggle_thinking(self) -> None:
        thought_box = self._dashboard().thought_box()
        thought_box.collapsed = not thought_box.collapsed

    def action_pulse_easy_button(self) -> None:
        easy_button = self._dashboard().query_one("#easy_button", PulsingEasyButton)
        easy_button.pulse_once()

    def _pulse_easy_button(self) -> None:
        try:
            easy_button = self._dashboard().query_one("#easy_button", PulsingEasyButton)
        except NoMatches:
            return
        easy_button.pulse_once()

    async def _apply_command_result(self, result: CommandResult) -> None:
        dashboard = self._dashboard()
        dashboard.append_system(result.message, style="cyan")
        self._append_chat("assistant", result.message)

        if result.system_prompt_addendum is not None:
            self.engine.set_system_prompt_addendum(result.system_prompt_addendum)

        if result.new_target:
            self.current_target = result.new_target
            self._update_header_target(result.new_target)
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

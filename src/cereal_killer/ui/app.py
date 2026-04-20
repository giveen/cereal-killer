from __future__ import annotations

import asyncio
import difflib
import json
import re
import shutil
import subprocess
import time as _time
from datetime import UTC, datetime
from pathlib import Path

from textual import on, work
from textual.app import App
from textual.css.query import NoMatches
from textual.events import Resize
from textual.widgets import Button, DirectoryTree, Input, Markdown, OptionList

from cereal_killer.engine import LLMEngine
from cereal_killer.ingest_logic import build_document_prompt, is_document_path, is_image_path
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
from mentor.engine.search_orchestrator import tiered_search
from mentor.kb.library_ingest import fetch_sync_status
from mentor.kb.query import retrieve_solution_for_machine
from mentor.ui.phase import detect_phase
from mentor.ui.startup import run_boot_sequence

from .screens import IngestModal, IngestSelection, MainDashboard, SolutionModal
from .widgets import PulsingEasyButton


_AUTO_COACH_COOLDOWN_SECS = 10
CODE_BLOCK_PATTERN = re.compile(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", re.DOTALL)
SEARCH_SOURCE_FILTER_RE = re.compile(r"\bonly\s+(ippsec|gtfobins|lolbas|hacktricks|payloads)\b", re.IGNORECASE)
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
        ("ctrl+o", "show_ops_view", "Ops View"),
        ("ctrl+g", "focus_gibson", "Gibson Search"),
        ("f3", "focus_gibson", "Gibson Search"),
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
        self._gibson_snippets: list[dict] = []

    def _dashboard(self) -> MainDashboard:
        screen = self.screen
        if not isinstance(screen, MainDashboard):
            raise RuntimeError("MainDashboard is not active")
        return screen

    async def on_mount(self) -> None:
        await self.push_screen(MainDashboard())
        dashboard = self._dashboard()
        dashboard.set_active_view("chat")
        dashboard.apply_responsive_layout(self.size.width)
        dashboard.set_phase("[IDLE]")
        dashboard.set_upload_root(Path.cwd())
        dashboard.set_loading(False)
        self.set_interval(0.7, self._pulse_easy_button)
        self.set_interval(300, self._schedule_persist_mental_state)
        self.set_interval(60, self._schedule_context_prune)
        self.set_interval(60, self._refresh_knowledge_sync_status)
        self.set_interval(2.0, self._refresh_system_footer)
        self.observer_task = asyncio.create_task(self._observe())
        self.clipboard_task = asyncio.create_task(self._watch_clipboard())
        asyncio.create_task(self._run_boot_sequence())
        self._refresh_knowledge_sync_status()
        self._refresh_system_footer()
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

    def action_show_ops_view(self) -> None:
        self._dashboard().set_active_view("ops")

    def action_open_image_ingest(self) -> None:
        self._open_ingest_modal("image")

    def action_open_document_ingest(self) -> None:
        self._open_ingest_modal("document")

    def action_focus_gibson(self) -> None:
        dashboard = self._dashboard()
        dashboard.set_active_view("gibson")
        dashboard.focus_gibson_input()

    @on(Input.Submitted, "#gibson_search_input")
    def on_gibson_search_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return
        self._run_gibson_search_worker(query)

    @on(OptionList.OptionSelected, "#gibson_result_list")
    def on_gibson_result_selected(self, event: OptionList.OptionSelected) -> None:
        idx = event.option_index
        if 0 <= idx < len(self._gibson_snippets):
            self._dashboard().show_gibson_snippet(self._gibson_snippets[idx])

    @on(Button.Pressed, "#gibson_synthesize")
    def on_gibson_synthesize_pressed(self) -> None:
        if not self._gibson_snippets:
            self.notify(
                "No results to synthesize — run a search first.",
                title="Gibson",
                severity="warning",
            )
            return
        self._run_gibson_synthesize_worker()

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
        if result.session_prefix == "__search__":
            if result.search_query:
                self._run_search_worker(result.search_query)
            else:
                dashboard.append_system("Search command did not provide a query.", style="red")
            return
        if result.session_prefix == "__sync_all__":
            self.notify("Sync-all launched. Knowledge bar will update after ingest.", title="Sync Status", severity="information")
            self._refresh_knowledge_sync_status()
            return
        if result.session_prefix == "__add_source__":
            if result.search_query:
                self.notify(
                    f"Crawling {result.search_query[:60]}...",
                    title="Add Source",
                    severity="information",
                )
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
    async def _run_vision_worker(
        self,
        image_path: str,
        source_label: str = "Clipboard",
        mark_context: bool = False,
    ) -> None:
        dashboard = self._dashboard()
        image_file = Path(image_path)
        if not image_file.exists():
            dashboard.append_system(f"Vision input missing: {image_file}", style="yellow")
            return

        self._analysis_busy(True)
        dashboard.set_active_tool("Vision")
        if mark_context:
            dashboard.set_upload_progress(25, "IMAGE INGEST")
            dashboard.set_context_chip(image_file.name, ingest_type="image")
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
            if mark_context:
                dashboard.set_upload_progress(100, "IMAGE INGEST")
        except Exception as exc:
            dashboard.append_system(f"Vision analysis error: {exc}", style="red")
            if mark_context:
                dashboard.set_upload_progress(0, "IMAGE INGEST")
        finally:
            dashboard.set_active_tool("Idle")
            self._analysis_busy(False)

    @work(exclusive=True, thread=False, group="llm")
    async def _run_document_ingest_worker(self, file_path: str) -> None:
        dashboard = self._dashboard()
        path = Path(file_path)
        if not is_document_path(path):
            dashboard.append_system(f"Unsupported document type: {path.suffix}", style="yellow")
            return

        self._analysis_busy(True)
        dashboard.set_active_tool("Document Ingest")
        dashboard.set_upload_progress(20, "DOC INGEST")
        dashboard.set_context_chip(path.name, ingest_type="document")
        try:
            payload = build_document_prompt(path)
            dashboard.set_upload_progress(55, "DOC INGEST")
            response = await self.engine.chat(
                payload.prompt,
                self.history_context,
                pathetic_meter=self.pathetic_meter,
            )
            await self._consume_llm_response(response.answer, response.reasoning_content or response.thought)
            dashboard.append_system(f"Document Uploaded: {path.name}", style="bold cyan")
            dashboard.set_upload_progress(100, "DOC INGEST")
        except json.JSONDecodeError as exc:
            dashboard.append_system(f"Invalid JSON document: {exc}", style="red")
            dashboard.set_upload_progress(0, "DOC INGEST")
        except Exception as exc:
            dashboard.append_system(f"Document ingest error: {exc}", style="red")
            dashboard.set_upload_progress(0, "DOC INGEST")
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

    @work(exclusive=True, thread=False, group="llm")
    async def _run_search_worker(self, query: str) -> None:
        dashboard = self._dashboard()
        self._analysis_busy(True)
        dashboard.set_active_tool("Search")
        self.notify(
            f"Searching local Gibson memory for '{query}'...",
            title="Search Status",
            severity="information",
        )
        dashboard.append_system(f"Searching local Gibson memory for '{query}'...", style="bold cyan")
        try:
            source_filters = self._extract_source_filters(query)
            search_result = await tiered_search(
                query=query,
                settings=self.kb.settings,
                history_commands=[],
                target_machine=None,
                allow_web=False,
                force_web=False,
                top_k=6,
                source_filters=source_filters,
            )
            chunks = search_result.vector_snippets
            top_scores = ", ".join(f"{score:.3f}" for score in search_result.top_similarity_scores) or "none"
            dashboard.append_system(f"RAG top-3 similarity: {top_scores}", style="bold cyan")
            dashboard.append_system(
                f"Gibson found {len(chunks)} relevant matches in local memory.",
                style="bold green",
            )
            self.notify(
                f"Gibson found {len(chunks)} relevant matches in local memory.",
                title="Search Status",
                severity="information",
            )

            if chunks:
                response = await self.engine.synthesize_search_results(query, chunks)
                labeled_answer = f"## [SEARCH RESULT]\n\n{response.answer.strip()}"
                await self._consume_llm_response(labeled_answer, response.reasoning_content or response.thought)
            else:
                fallback = (
                    f"My local memory for '{query}' is a void. "
                    "Checking the IppSec and HackTricks datasets again... "
                    "or perhaps you should learn to type."
                )
                dashboard.append_assistant(fallback)
                self._append_chat("assistant", fallback)

            # Populate Gibson tab with raw snippets and switch to it.
            self._gibson_snippets = [
                {"title": s.title, "content": s.content, "source": s.source, "url": s.url}
                for s in chunks
            ]
            dashboard.set_gibson_results(self._gibson_snippets)
            dashboard.set_active_view("gibson")
        except Exception as exc:
            dashboard.append_system(f"Search error: {exc}", style="red")
        finally:
            dashboard.set_active_tool("Idle")
            self._analysis_busy(False)

    @staticmethod
    def _extract_source_filters(query: str) -> list[str] | None:
        match = SEARCH_SOURCE_FILTER_RE.search(query)
        if not match:
            return None
        return [match.group(1).strip().lower()]

    @work(exclusive=False, thread=False, group="search")
    async def _run_gibson_search_worker(self, query: str) -> None:
        """Direct search from the Gibson tab — no LLM synthesis, just raw snippets."""
        dashboard = self._dashboard()
        dashboard.set_gibson_loading(True)
        dashboard.set_active_tool("Gibson")
        try:
            source_filters = self._extract_source_filters(query)
            search_result = await tiered_search(
                query=query,
                settings=self.kb.settings,
                history_commands=[],
                target_machine=None,
                allow_web=False,
                force_web=False,
                top_k=8,
                source_filters=source_filters,
            )
            top_scores = ", ".join(f"{score:.3f}" for score in search_result.top_similarity_scores) or "none"
            dashboard.append_system(f"RAG top-3 similarity: {top_scores}", style="bold cyan")
            dashboard.append_system(
                f"Gibson found {len(search_result.vector_snippets)} relevant matches in local memory.",
                style="bold green",
            )
            self._gibson_snippets = [
                {"title": s.title, "content": s.content, "source": s.source, "url": s.url}
                for s in search_result.vector_snippets
            ]
            dashboard.set_gibson_results(self._gibson_snippets)
        except Exception as exc:
            try:
                dashboard.query_one("#gibson_viewer", Markdown).update(f"_Search error: {exc}_")
            except Exception:
                pass
        finally:
            dashboard.set_active_tool("Idle")
            dashboard.set_gibson_loading(False)

    @work(exclusive=True, thread=False, group="llm")
    async def _run_gibson_synthesize_worker(self) -> None:
        """LLM-synthesize all current Gibson snippets into a master cheat sheet."""
        from mentor.kb.query import RAGSnippet

        dashboard = self._dashboard()
        self._analysis_busy(True)
        dashboard.set_active_tool("Synthesize")
        dashboard.set_gibson_loading(True)
        try:
            query = dashboard.query_one("#gibson_search_input", Input).value or "summarize"
            snippets = [
                RAGSnippet(
                    source=s.get("source", ""),
                    machine="",
                    title=s.get("title", ""),
                    url=s.get("url", ""),
                    content=s.get("content", ""),
                    score=0.0,
                )
                for s in self._gibson_snippets
            ]
            response = await self.engine.synthesize_search_results(query, snippets)
            cheat_sheet = f"# MASTER CHEAT SHEET\n\n{response.answer.strip()}"
            dashboard.query_one("#gibson_viewer", Markdown).update(cheat_sheet)
        except Exception as exc:
            try:
                dashboard.query_one("#gibson_viewer", Markdown).update(f"_Synthesis error: {exc}_")
            except Exception:
                pass
        finally:
            dashboard.set_active_tool("Idle")
            self._analysis_busy(False)
            dashboard.set_gibson_loading(False)

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

    def _refresh_system_footer(self) -> None:
        """Update compact footer stats (RAM + GPU temp) for the Gibson frame."""
        try:
            total_kb = 0
            available_kb = 0
            with Path("/proc/meminfo").open("r", encoding="utf-8") as meminfo:
                for line in meminfo:
                    if line.startswith("MemTotal:"):
                        total_kb = int(line.split()[1])
                    elif line.startswith("MemAvailable:"):
                        available_kb = int(line.split()[1])
            ram_total_gb = total_kb / (1024 * 1024) if total_kb else 0.0
            ram_used_gb = (total_kb - available_kb) / (1024 * 1024) if total_kb else 0.0

            gpu_temp = "N/A"
            try:
                result = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=temperature.gpu",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=1,
                )
                if result.returncode == 0:
                    first_line = (result.stdout or "").strip().splitlines()
                    if first_line:
                        gpu_temp = f"{first_line[0]}"
            except Exception:
                pass

            self._dashboard().set_system_footer(
                f"RAM {ram_used_gb:4.1f}/{ram_total_gb:4.1f}GB | 5090 {gpu_temp}C"
            )
        except Exception:
            return

    async def _run_boot_sequence(self) -> None:
        dashboard = self._dashboard()
        if not hasattr(self.engine, "settings"):
            return
        lines: list[str] = []
        async for result in run_boot_sequence(self.engine.settings):
            lines.append(self._strip_rich_tags(result.message))
            await asyncio.sleep(0)
        dashboard.set_boot_status("\n".join(line for line in lines if line.strip()))
        greeting = await self.engine.returning_greeting()
        if greeting:
            dashboard.append_assistant(greeting)
            self._append_chat("assistant", greeting)

    @staticmethod
    def _strip_rich_tags(text: str) -> str:
        return re.sub(r"\[/?[^\]]+\]", "", text or "")

    async def _observe(self) -> None:
        cwd = str(Path.cwd())
        dashboard = self._dashboard()
        last_auto_coach_time: float = 0.0
        terminal_link_online = False

        try:
            async for event in observe_history_events(cwd):
                # Set terminal link ONLINE on first successful event
                if not terminal_link_online:
                    dashboard.set_terminal_link_online(True)
                    terminal_link_online = True
                
                if event.json_hint:
                    self._append_chat("assistant", event.json_hint)
                    dashboard.append_assistant(event.json_hint)
                    continue

                if not event.command:
                    continue

                # Pulse terminal link to show data is flowing
                await dashboard.pulse_terminal_link()

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
        except RuntimeError as e:
            # Permission error or other startup issue
            dashboard.set_terminal_link_online(False)
            error_msg = f"[red]Terminal Link Failed:[/red] {str(e)}"
            self._append_chat("system", error_msg)
            dashboard.append_system(error_msg, style="bold red")
            raise

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

    def _open_ingest_modal(self, ingest_type: str) -> None:
        if ingest_type == "image" and Path("/screenshots").exists():
            root = Path("/screenshots")
        else:
            root = Path.cwd()
        self.push_screen(IngestModal(ingest_type=ingest_type, root_path=root), self._on_ingest_selection)

    def _on_ingest_selection(self, selection: IngestSelection | None) -> None:
        if selection is None:
            return

        chosen = selection.path.expanduser().resolve()
        dashboard = self._dashboard()
        dashboard.set_upload_progress(10, "UPLOAD")

        if selection.ingest_type == "image":
            if not is_image_path(chosen):
                self.notify("Choose a .png/.jpg/.jpeg file", title="Ingest", severity="warning")
                dashboard.set_upload_progress(0, "UPLOAD")
                return
            self._prime_uploaded_image(chosen, source="Modal")
            self._run_vision_worker(str(chosen), source_label="Modal", mark_context=True)
            return

        if not is_document_path(chosen):
            self.notify("Choose a .log/.txt/.json file", title="Ingest", severity="warning")
            dashboard.set_upload_progress(0, "UPLOAD")
            return
        self._run_document_ingest_worker(str(chosen))

    def _on_web_search_state(self, active: bool) -> None:
        dashboard = self._dashboard()
        dashboard.set_active_tool("Web Search" if active else "Idle")

    def _refresh_knowledge_sync_status(self) -> None:
        try:
            statuses = fetch_sync_status(self.kb.settings, ["ippsec", "gtfobins", "lolbas", "hacktricks", "payloads"])
            self._dashboard().set_knowledge_sync_status(statuses)
        except Exception:
            return

    @on(Button.Pressed, "#easy_button")
    def show_walkthrough(self) -> None:
        self._record_easy_usage()
        machine_name = Path.cwd().name
        solution_markdown = retrieve_solution_for_machine(self.kb.settings, machine_name)
        self.push_screen(SolutionModal(solution_markdown))
        
        # Attempt to open IppSec YouTube link if available
        self._open_ippsec_link(machine_name)

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
        cleaned_message = self._strip_rich_tags(result.message)
        dashboard.append_system(cleaned_message, style="cyan")
        self._append_chat("assistant", cleaned_message)

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

    def _open_ippsec_link(self, machine_name: str) -> None:
        """Open IppSec YouTube video link in default browser."""
        try:
            # Try to find IppSec video metadata from knowledge base
            # Construct ippsec.rocks link: https://ippsec.rocks/?n=MachineName
            machine_safe = machine_name.replace("_", "-").replace(" ", "-").lower()
            url = f"https://ippsec.rocks/?n={machine_safe}"
            
            # Open in default browser using xdg-open (Linux) or open (Mac)
            opener = "xdg-open" if shutil.which("xdg-open") else ("open" if shutil.which("open") else None)
            if opener:
                subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._dashboard().append_system(f"📺 Opened IppSec video: {url}", style="dim cyan")
        except Exception:
            # Silently fail if browser can't be opened
            pass

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

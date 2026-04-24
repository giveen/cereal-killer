from __future__ import annotations

import asyncio
import re
import time as _time
from typing import Any
from pathlib import Path

from textual import on, work
from textual.app import App
from textual.css.query import NoMatches
from textual.events import Resize
from textual.widgets import Button, DirectoryTree, Input, Markdown, OptionList
from cereal_killer.context_manager import ContextManager
from cereal_killer.context_per_box import ContextPerBox
from cereal_killer.engine import LLMEngine
from cereal_killer.knowledge_base import KnowledgeBase
from cereal_killer.observer import (
    ClipboardImageDetected,
    ClipboardImageWatcher,
)
from cereal_killer.ui.commands import CommandHandler
from mentor.engine.commands import CommandResult
from mentor.ui.startup import run_boot_sequence

from .base import resolve_dashboard, require_dashboard
from .screens import InfrastructureCriticalModal, IngestModal, IngestSelection, MainDashboard, SettingsScreen, SolutionModal
from .widgets import PulsingEasyButton
from .widgets import CommandInput
from .workers.vision_workers import VISION_BUFFER_PATH
from cereal_killer.ui.workers.vision_workers import VisionWorkerManager
from cereal_killer.ui.workers.search_workers import SearchWorkerManager
from cereal_killer.ui.workers.ingest_workers import IngestWorkerManager
from cereal_killer.ui.workers.chat_workers import ChatWorkerManager
from cereal_killer.ui.workers.worker_lifecycle import WorkerLifecycleManager
from cereal_killer.ui.workers.misc_workers import MiscWorkerManager
from cereal_killer.ui.context.context_state import ContextStateManager
from cereal_killer.ui.sessions.session_manager import SessionManager
from cereal_killer.ui.observers.terminal_observer import TerminalObserver
from cereal_killer.ui.cve.cve_jit import CVEJIT


class CerealKillerApp(App[None]):
    CSS_PATH = Path(__file__).with_name("styles.tcss")
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("f1", "show_chat_view", "Chat"),
        ("f2", "show_ops_view", "Ops"),
        ("f3", "show_gibson_view", "Gibson"),
        ("s", "action_sync_all", "Sync"),
        ("ctrl+b", "toggle_sidebar", "Sidebar"),
        ("ctrl+u", "action_toggle_upload_tree", "Upload"),
        ("ctrl+t", "toggle_thought_stream", "Thought"),
    ]

    def __init__(
        self,
        engine: LLMEngine,
        kb: KnowledgeBase,
        preflight_hard_fail: bool = False,
        preflight_reason: str = "",
    ) -> None:
        super().__init__()
        self.engine = engine
        self.kb = kb
        self.title = "CEREAL KILLER"
        self.sub_title = "TARGET: NONE"
        self.observer_task: asyncio.Task[None] | None = None
        self.clipboard_task: asyncio.Task[None] | None = None
        self.clipboard_watcher = ClipboardImageWatcher(output_path=VISION_BUFFER_PATH)
        self.last_code_block = ""
        self.pathetic_meter = 0
        self.easy_usage_count = 0
        self.successful_command_count = 0
        self._context_manager = ContextManager()
        self.current_target: str = ""
        self._pruning_in_flight = False
        self._analysis_jobs = 0
        # Worker tracking for cancellation safety
        self._active_workers: dict[str, asyncio.Task[None]] = {}
        self._uploaded_image_path: Path | None = None
        self._gibson_snippets: list[dict] = []
        self._vision_analyzed_sources: set[str] = set()
        self._preflight_hard_fail = preflight_hard_fail
        self._preflight_reason = preflight_reason
        self._background_tasks: set[asyncio.Task[Any]] = set()

        self._vision_manager = VisionWorkerManager(self)
        self._search_manager = SearchWorkerManager(self)
        self._ingest_manager = IngestWorkerManager(self)
        self._chat_manager = ChatWorkerManager(self)
        self._lifecycle_manager = WorkerLifecycleManager(self)
        self._misc_manager = MiscWorkerManager(self)
        self._context_manager_instance = ContextStateManager(self)
        self._session_manager = SessionManager(self)
        self._context_per_box = ContextPerBox(self.engine.settings)
        self._observer = TerminalObserver(self)
        self._cve_jit = CVEJIT(self)
        self._command_handler = CommandHandler(self)

    @property
    def active_history(self) -> list[str]:
        """Get the active machine's command history (per-box isolated)."""
        return self._context_per_box.get_active_history()

    @property
    def active_transcript(self) -> list[dict[str, str]]:
        """Get the active machine's chat transcript (per-box isolated)."""
        return self._context_per_box.get_active_transcript()

    @property
    def context_per_box(self) -> ContextPerBox:
        """Access the context-per-box manager."""
        return self._context_per_box

    _dashboard = require_dashboard

    async def on_mount(self) -> None:
        await self.push_screen(MainDashboard())
        dashboard = self._dashboard()
        dashboard.set_active_view("chat")
        dashboard.apply_responsive_layout(self.size.width)
        dashboard.set_phase("[IDLE]")
        dashboard.set_upload_root(Path.cwd())
        dashboard.set_loading(False)
        dashboard.focus_chat_input()
        self.set_interval(0.7, self._pulse_easy_button)
        self.set_interval(300, self._schedule_persist_mental_state)
        self.set_interval(60, self._schedule_context_prune)
        self.set_interval(60, self._observer.update_knowledge_sync_status)
        self.set_interval(15, self._observer.update_github_api_status)
        self.observer_task = asyncio.create_task(self._observe())
        self.clipboard_task = asyncio.create_task(self._watch_clipboard())
        self._spawn_background_task(self._run_boot_sequence())
        self._observer.update_knowledge_sync_status()
        self._observer.update_github_api_status()
        self._spawn_background_task(self._observer.run_system_readiness_check())
        if self._preflight_hard_fail:
            dashboard.set_active_view("ops")
            self.push_screen(InfrastructureCriticalModal(self._preflight_reason))
        if hasattr(self.engine, "set_web_search_callback"):
            self.engine.set_web_search_callback(self._observer.set_web_search_state)
        self.set_active_machine(Path.cwd().name)
        self._update_context_token_counter()

    async def on_unmount(self) -> None:
        if self.observer_task:
            self.observer_task.cancel()
        if self.clipboard_task:
            self.clipboard_task.cancel()
        for task in list(self._background_tasks):
            task.cancel()
        self._background_tasks.clear()
        # Cancel all pending workers
        self.cancel_all_workers()
        if hasattr(self.engine, "persist_mental_state"):
            await self.engine.persist_mental_state(self.active_history)
        self._save_session_snapshot("app-close")

    def on_resize(self, event: Resize) -> None:
        try:
            self._dashboard().apply_responsive_layout(event.size.width)
        except Exception:
            return

    @on(CommandInput.Submitted, "#command_input")
    def on_input_submitted(self, event: CommandInput.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt:
            return
        dashboard = self._dashboard()
        dashboard.append_user(prompt)
        self._append_chat("user", prompt)
        if prompt.startswith("/"):
            dashboard.set_active_tool("CommandProcessor")
            asyncio.create_task(self._handle_command(prompt))
        elif self.engine.settings.enable_streaming:
            self._run_stream_chat_worker(prompt)
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

    def action_show_chat_view(self) -> None:
        dashboard = self._dashboard()
        dashboard.set_active_view("chat")
        dashboard.focus_chat_input()

    def action_toggle_sidebar(self) -> None:
        dashboard = self._dashboard()
        sidebar = dashboard.query_one("#intel_sidebar")
        if sidebar.styles.display == "none":
            dashboard.set_active_view("ops")
        else:
            dashboard.set_active_view("chat")

    def action_sync_all(self) -> None:
        asyncio.create_task(self._handle_command("/sync-all"))

    def action_open_upload_modal(self) -> None:
        self._open_ingest_modal("document")

    def action_open_image_ingest(self) -> None:
        self._open_ingest_modal("image")

    def action_open_document_ingest(self) -> None:
        self._open_ingest_modal("document")

    def action_focus_gibson(self) -> None:
        dashboard = self._dashboard()
        dashboard.set_active_view("gibson")
        dashboard.focus_gibson_input()
        asyncio.create_task(self._refresh_gibson_thinking_buffer())

    async def _refresh_gibson_thinking_buffer(self) -> None:
        self._misc_manager.refresh_gibson_thinking_buffer()

    @on(Button.Pressed, "#system_readiness_tag")
    def on_system_readiness_tag_pressed(self) -> None:
        dashboard = self._dashboard()
        guide = Path("docs/setup/README.md")
        if not guide.exists():
            self.notify("Setup guide not found at docs/setup/README.md", title="Setup", severity="warning")
            return

        dashboard.set_active_view("gibson")
        try:
            markdown = guide.read_text(encoding="utf-8")
        except Exception as exc:
            self.notify(f"Could not open setup guide: {exc}", title="Setup", severity="warning")
            return
        dashboard.query_one("#gibson_viewer", Markdown).update(markdown)
        self.notify("Opened setup guide in Gibson tab", title="Setup", severity="information")

    @on(Button.Pressed, "#settings_button")
    def on_settings_button_pressed(self) -> None:
        """Open the settings modal when the settings button is pressed."""
        self.push_screen(SettingsScreen(self.kb.settings, self), lambda result: self._on_settings_result(result))

    def _on_settings_result(self, result: bool | None) -> None:
        """Handle the result from the settings modal."""
        if result:
            self.notify("Settings updated successfully.", title="Settings", severity="information")
        elif result is False:
            self.notify("Settings cancelled.", title="Settings", severity="warning")
        else:
            self.notify("Settings modal dismissed.", title="Settings", severity="warning")

    def queue_remote_visual_url(self, url: str) -> None:
        """Handle inline Gibson [VIEW IMAGE] links."""
        cleaned = (url or "").strip()
        if not cleaned:
            return
        self._dashboard().set_remote_image_candidate(cleaned)
        self._run_remote_visual_worker(cleaned)

    @on(Input.Submitted, "#gibson_search_input")
    def on_gibson_search_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return
        self._run_gibson_search_worker(query)

    @on(OptionList.OptionSelected, "#gibson_result_list")
    def on_gibson_result_selected(self, event: OptionList.OptionSelected) -> None:
        snippet = self._dashboard().resolve_gibson_selection(event.option_index)
        if snippet is not None:
            self._dashboard().show_gibson_snippet(snippet)

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

    @on(Button.Pressed, "#visual_view_remote")
    def on_visual_view_remote_pressed(self) -> None:
        url = self._dashboard().get_remote_image_candidate()
        if not url:
            self.notify("No remote diagram is queued.", title="Visual Buffer", severity="warning")
            return
        self._run_remote_visual_worker(url)

    @on(Button.Pressed, "#visual_send_zero_cool")
    def on_visual_send_zero_cool_pressed(self) -> None:
        image_path = self._dashboard().get_visual_buffer_image_path()
        if image_path is None:
            self.notify("Load an image first.", title="Visual Buffer", severity="warning")
            return
        source_key = str(image_path.expanduser().resolve())
        if source_key in self._vision_analyzed_sources:
            self.notify(
                "Zero Cool already analyzed this frame.",
                title="Visual Buffer",
                severity="information",
            )
            return
        self._run_vision_worker(str(image_path), source_label="Visual Buffer")

    async def _handle_command(self, prompt: str) -> None:
        """Route slash commands through the CommandHandler."""
        await self._command_handler.handle_command(prompt)

    @work(exclusive=True, thread=False, group="llm")
    async def _run_chat_worker(self, prompt: str) -> None:
        await self._chat_manager.run_chat_worker(prompt)

    @work(exclusive=True, thread=False, group="llm")
    async def _run_stream_chat_worker(self, prompt: str) -> None:
        await self._chat_manager.run_stream_chat_worker(prompt)

    async def _chat_body(self, prompt: str) -> None:
        await self._chat_manager.chat_body(prompt)

    @work(exclusive=True, thread=False, group="llm")
    async def _run_loot_worker(self) -> None:
        await self._chat_manager.run_loot_worker()

    async def _loot_body(self) -> None:
        await self._chat_manager.loot_body()

    @work(exclusive=True, thread=False, group="llm")
    async def _run_vision_worker(
        self,
        image_path: str,
        source_label: str = "Clipboard",
        mark_context: bool = False,
    ) -> None:
        await self._vision_manager.run_vision_worker(image_path, source_label, mark_context)

    async def _vision_body(self, image_path: str, mark_context: bool) -> None:
        await self._vision_manager.vision_body(image_path, mark_context)

    @work(exclusive=True, thread=False, group="llm")
    async def _run_document_ingest_worker(self, file_path: str) -> None:
        await self._ingest_manager.run_document_ingest_worker(file_path)

    async def _doc_ingest_body(self, path_str: str) -> None:
        await self._ingest_manager.doc_ingest_body(path_str)

    @work(exclusive=True, thread=False, group="coach")
    async def _run_autocoach_worker(self, command: str) -> None:
        await self._misc_manager.run_autocoach_worker(command)

    async def _autocoach_body(self, command: str) -> None:
        await self._misc_manager.autocoach_body(command)

    @work(exclusive=True, thread=False, group="llm")
    async def _run_search_worker(self, query: str) -> None:
        await self._search_manager.run_search_worker(query)

    async def _search_body(self, query: str) -> None:
        await self._search_manager.search_body(query)

    def _extract_source_filters(self, query: str) -> list[str] | None:
        """Delegate to search manager for source filter extraction."""
        return self._search_manager.extract_source_filters(query)

    @work(exclusive=False, thread=False, group="search")
    async def _run_gibson_search_worker(self, query: str) -> None:
        """Direct search from the Gibson tab with grouped results + auto summary."""
        await self._search_manager.run_gibson_search_worker(query)

    async def _gibson_search_body(self, query: str) -> None:
        """Perform tiered search and LLM synthesis for Gibson tab."""
        await self._search_manager.gibson_search_body(query)

    @work(exclusive=True, thread=False, group="llm")
    async def _run_gibson_synthesize_worker(self) -> None:
        """LLM-synthesize all current Gibson snippets into a master cheat sheet."""
        await self._search_manager.run_gibson_synthesize_worker()

    async def _gibson_synthesize_body(self) -> None:
        """Synthesize current Gibson snippets into a master cheat sheet."""
        await self._search_manager.gibson_synthesize_body()

    @work(exclusive=True, thread=False, group="media")
    async def _run_remote_visual_worker(self, url: str) -> None:
        await self._vision_manager.run_remote_visual_worker(url)

    async def _visual_body(self, url: str) -> None:
        await self._vision_manager.visual_body(url)

    def _try_dashboard(self) -> MainDashboard | None:
        """Return MainDashboard from active screen or stack, if present."""
        return resolve_dashboard(self)

    def _update_llm_cache_metrics(self, backend_meta: dict[str, object] | None) -> None:
        self._chat_manager.update_llm_cache_metrics(backend_meta)

    async def _consume_llm_response(self, answer: str, thought: str) -> None:
        # Persist assistant text first so we don't lose it if UI updates fail.
        self._track_code_block(answer)
        self._warn_if_repetitive_response(answer)
        self._append_chat("assistant", answer)

        dashboard = self._try_dashboard()
        if dashboard is None:
            self.notify("Response ready, but dashboard is temporarily unavailable.", title="Zero Cool", severity="warning")
            return

        try:
            await self._safe_stream_thought(thought, dashboard=dashboard)
            dashboard.set_active_view("chat")
            dashboard.append_assistant(answer)
        except Exception as exc:
            try:
                dashboard.append_system(f"UI post-processing error: {exc}", style="red")
            except Exception:
                self.notify(f"Response ready (UI error: {exc})", title="Zero Cool", severity="warning")

    def _analysis_busy(self, active: bool) -> None:
        self._lifecycle_manager.analysis_busy(active)

    async def _run_boot_sequence(self) -> None:
        dashboard = self._try_dashboard()
        if dashboard is None or not hasattr(self.engine, "settings"):
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
        await self._observer.observe()

    async def _watch_clipboard(self) -> None:
        await self._observer.watch_clipboard()

    def on_clipboard_image_detected(self, message: ClipboardImageDetected) -> None:
        self._observer.on_clipboard_detected(message)

    @on(Button.Pressed, "#clear_visual_buffer")
    def clear_visual_buffer(self) -> None:
        self._observer.clear_visual_buffer()

    def _prime_uploaded_image(self, path: Path, source: str) -> None:
        self._vision_manager.prime_uploaded_image(path, source)

    def _is_image_file(self, path: Path) -> bool:
        return self._vision_manager.is_image_file(path)

    def _looks_like_image_url(self, url: str) -> bool:
        return self._vision_manager.looks_like_image_url(url)

    def _extract_visual_candidate_url(self, snippets: list[dict]) -> str | None:
        return self._vision_manager.extract_visual_candidate_url(snippets)

    def _open_ingest_modal(self, ingest_type: str) -> None:
        self._ingest_manager.open_ingest_modal(ingest_type)

    def _on_ingest_selection(self, selection: IngestSelection | None) -> None:
        self._ingest_manager.on_ingest_selection(selection)

    def _on_web_search_state(self, active: bool) -> None:
        self._observer.set_web_search_state(active)

    def _refresh_knowledge_sync_status(self) -> None:
        self._observer.update_knowledge_sync_status()

    def _refresh_github_api_status(self) -> None:
        self._observer.update_github_api_status()

    async def _run_system_readiness_check(self) -> None:
        await self._observer.run_system_readiness_check()

    @work(exclusive=False, thread=False, group="cve-jit")
    async def _run_cve_jit_worker(self, text: str) -> None:
        await self._cve_jit.run_cve_jit_worker(text)

    async def _cve_jit_body(self, text: str, dashboard) -> None:
        await self._cve_jit.cve_jit_body(text, dashboard)

    @on(Button.Pressed, "#easy_button")
    async def show_walkthrough(self) -> None:
        self._record_easy_usage()
        machine_name = Path.cwd().name
        solution_markdown = await retrieve_solution_for_machine(self.kb.settings, machine_name)
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

    def _register_worker(self, name: str, task: asyncio.Task[None]) -> None:
        """Register a worker task for tracking/cancellation."""
        self._lifecycle_manager.register_worker(name, task)

    def _cancel_worker(self, name: str) -> None:
        """Cancel a specific worker by name."""
        self._lifecycle_manager.cancel_worker(name)

    def cancel_all_workers(self) -> None:
        """Cancel all pending workers to prevent race conditions."""
        self._lifecycle_manager.cancel_all_workers()

    def _unregister_worker(self, name: str) -> None:
        """Unregister a completed worker."""
        self._lifecycle_manager.unregister_worker(name)

    async def _with_worker_cancellation(self, coro: Any) -> Any:
        """Cancel all existing workers before running the given coroutine.

        Prevents race conditions when multiple @work workers share
        the active per-box context.
        """
        return await self._lifecycle_manager.with_worker_cancellation(coro)

    async def _apply_command_result(self, result: CommandResult) -> None:
        self._command_handler.apply_command_result(result)

    def _schedule_persist_mental_state(self) -> None:
        self._session_manager.schedule_persist()

    def _schedule_context_prune(self) -> None:
        self._context_manager_instance.schedule_prune()

    async def _maybe_prune_transcript(self) -> None:
        await self._context_manager_instance.maybe_prune_transcript()

    def _append_chat(self, role: str, text: str) -> None:
        self._context_manager_instance.append_chat(role, text)

    def _update_context_token_counter(self) -> None:
        self._context_manager_instance.update_token_counter()

    def _warn_if_repetitive_response(self, new_response: str) -> None:
        self._context_manager_instance.warn_repetitive(new_response)

    async def _safe_stream_thought(self, thought: str, *, dashboard: MainDashboard | None = None) -> None:
        task = self._chat_manager.safe_stream_thought(thought, dashboard=dashboard)
        if task is not None:
            await task

    def _track_code_block(self, response_text: str) -> None:
        self._context_manager_instance.track_code_block(response_text)

    def _adjust_pathetic_meter(self) -> None:
        self._session_manager.adjust_pathetic_meter()

    def _record_easy_usage(self, weight: int = 1) -> None:
        self._session_manager.record_easy_usage(weight)

    def _open_ippsec_link(self, machine_name: str) -> None:
        self._misc_manager.open_ippsec_link(machine_name)

    def _spawn_background_task(self, coro: Any) -> asyncio.Task[Any]:
        """Spawn and track best-effort background tasks for clean shutdown."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def set_active_machine(self, machine: str) -> None:
        """Switch active machine across app, context store, and engine."""
        normalized = (machine or "").strip()
        if not normalized:
            return
        self.current_target = normalized
        self._update_header_target(normalized)
        self.engine.set_active_machine(normalized)
        self._context_per_box.set_active_machine(normalized)

    def set_active_history(self, commands: list[str]) -> None:
        """Set history for the current active machine."""
        self._context_per_box.set_active_history(commands)

    def set_active_transcript(self, entries: list[dict[str, str]]) -> None:
        """Set transcript for the current active machine."""
        self._context_per_box.set_active_transcript(entries)

    def toggle_thought_stream(self) -> None:
        """Toggle visibility of the thought stream panel."""
        dashboard = self._try_dashboard()
        if dashboard is None:
            return
        # Toggle thought panel visibility if it exists
        try:
            panel = dashboard.query_one("#thought_panel", None)
            if panel is not None:
                panel.styles.display = "none" if panel.styles.display != "none" else "block"
        except Exception:
            pass

    def _update_header_target(self, target: str | None = None) -> None:
        active_target = (target or self.current_target or "NONE").upper()
        self.title = "CEREAL KILLER"
        self.sub_title = f"TARGET: {active_target}"

    def _save_session_snapshot(self, reason: str) -> None:
        self._session_manager.save_session_snapshot(reason)

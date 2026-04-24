from __future__ import annotations

import asyncio
import logging
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cereal_killer.ui.app import CerealKillerApp

from textual.widgets import Markdown

from cereal_killer.observer import (
    ClipboardImageDetected,
    clear_clipboard_buffer,
    candidate_history_files,
    observe_history_events,
)
from cereal_killer.ui.screens import MainDashboard
from cereal_killer.ui.tabs.ops import check_system_readiness as check_system_readiness
from mentor.ui.phase import detect_phase

from ..screens import MainDashboard as MainDashboardType
from ..base import resolve_dashboard, require_dashboard
from ..workers.vision_workers import VISION_BUFFER_PATH


class TerminalObserver:
    """Observer that monitors terminal history, clipboard, and boot sequence."""

    def __init__(self, app: "CerealKillerApp") -> None:
        self._app = app

    async def _observe(self) -> None:
        cwd = str(Path.cwd())
        dashboard = require_dashboard(self._app)
        last_auto_coach_time: float = 0.0
        terminal_link_online = False

        # Watch all available history files concurrently
        history_files = candidate_history_files()
        if not history_files:
            logging.getLogger(__name__).error("No history files found to monitor")
            return

        logging.getLogger(__name__).info(
            f"Monitoring {len(history_files)} history files: {[str(f) for f in history_files]}"
        )

        async def observe_single_file(history_file: Path) -> None:
            nonlocal terminal_link_online

            try:
                async for event in observe_history_events(str(history_file), settings=self._app.engine.settings):
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

                    await self._app._run_cve_jit_worker(event.command)
                    if event.feedback_line:
                        await self._app._run_cve_jit_worker(event.feedback_line)

                    self._app.set_active_history(event.context_commands)
                    self._update_context_token_counter()
                    phase = detect_phase(self._app.active_history)
                    dashboard.set_phase(phase)
                    self._app.engine.record_phase_change(phase)
                    self._app.engine.record_command_progress()

                    audit_warning = self._audit_methodology(event.command, self._app.active_history)
                    if audit_warning:
                        dashboard.append_system(audit_warning, style="bold red")
                        self._append_chat("assistant", audit_warning)

                    inferred_target = event.cd_target or event.host_target
                    if inferred_target and inferred_target != self._app.current_target:
                        auto_cmd = f"/box {inferred_target}"
                        cmd_result = await self._dispatch_command(auto_cmd)
                        if cmd_result is not None:
                            await self._apply_command_result(cmd_result)

                    if not event.trigger_brain:
                        continue

                    now = _time.monotonic()
                    if now - last_auto_coach_time < 10:
                        continue
                    last_auto_coach_time = now
                    self._run_autocoach_worker(event.command)
            except RuntimeError as e:
                dashboard.set_terminal_link_online(False)
                error_msg = f"[red]Terminal Link Failed ({history_file.name}):[/red] {str(e)}"
                self._append_chat("system", error_msg)
                dashboard.append_system(error_msg, style="bold red")
                raise

        # Launch concurrent observers for each history file
        tasks = [asyncio.create_task(observe_single_file(f)) for f in history_files]
        await asyncio.gather(*tasks)

    async def _watch_clipboard(self) -> None:
        async for detected in self._app.clipboard_watcher.watch():
            self._app.post_message(detected)

    def on_clipboard_image_detected(self, message: ClipboardImageDetected) -> None:
        snapshot = message.snapshot
        require_dashboard(self._app).set_visual_buffer_image(
            snapshot.image_path, source="Clipboard", preview=snapshot.preview
        )
        self._app._uploaded_image_path = snapshot.image_path
        self._app.notify(
            f"Clipboard image buffered as {snapshot.image_path.name}",
            title="Visual Buffer",
            severity="information",
        )

    def clear_visual_buffer(self) -> None:
        ok = clear_clipboard_buffer(VISION_BUFFER_PATH)
        require_dashboard(self._app).clear_visual_buffer()
        self._app._uploaded_image_path = None
        if ok:
            self._app.notify("Visual buffer cleared", title="Visual Buffer", severity="information")
        else:
            self._app.notify("Could not clear visual buffer", title="Visual Buffer", severity="warning")

    _dashboard = require_dashboard

    def _append_chat(self, role: str, text: str) -> None:
        self._app._append_chat(role, text)
        self._update_context_token_counter()

    def _update_context_token_counter(self) -> None:
        dashboard = self._try_dashboard()
        if dashboard is None:
            return
        current_tokens = self._app._context_manager.estimate_active_context_tokens(
            self._app.active_transcript,
            self._app.active_history,
        )
        max_tokens = int(getattr(self._app.engine.settings, "max_model_len", 0) or 0)
        dashboard.set_context_token_counter(current_tokens, max_tokens)

    def _try_dashboard(self) -> MainDashboardType | None:
        return resolve_dashboard(self._app)

    def _audit_methodology(self, command: str, history_context: list[str]) -> str:
        from mentor.engine.methodology import audit_command as audit_methodology
        return audit_methodology(command, history_context)

    async def _dispatch_command(self, prompt: str) -> Any:
        from mentor.engine.commands import dispatch as dispatch_command
        return await dispatch_command(prompt, self._app.engine, self._app.kb.settings)

    async def _apply_command_result(self, result: Any) -> None:
        dashboard = require_dashboard(self._app)
        cleaned_message = self._app._strip_rich_tags(result.message)
        dashboard.append_system(cleaned_message, style="cyan")
        self._append_chat("assistant", cleaned_message)
        await self._app._run_cve_jit_worker(cleaned_message)

        if result.system_prompt_addendum is not None:
            self._app.engine.set_system_prompt_addendum(result.system_prompt_addendum)

        if result.new_target:
            self._app.set_active_machine(result.new_target)
            dashboard.set_upload_root(Path.cwd())
            self._app.notify(
                f"Context switched -> {result.new_target.upper()}",
                title="Target Loaded",
                severity="information",
            )
            self._app.engine.record_phase_change("[IDLE]")

        if result.reset_phase:
            dashboard.set_phase("[IDLE]")

    def _run_autocoach_worker(self, command: str) -> None:
        self._app._run_autocoach_worker(command)

    async def run_system_readiness_check(self) -> None:
        """Run system readiness check and update dashboard."""
        active = self._app.screen
        if isinstance(active, MainDashboard):
            dashboard = active
        else:
            return
        try:
            dashboard = self._app._dashboard()
        except RuntimeError:
            return

        from cereal_killer.kb.cve_jit import get_rate_snapshot as _get_rate

        if not hasattr(self._app.engine, "settings"):
            return

        result = await check_system_readiness(self._app.kb.settings.llm_base_url)
        try:
            if result.ok:
                dashboard.set_system_readiness(True)
                return
            dashboard.set_system_readiness(False, result.details)
        except Exception:
            return

    def update_github_api_status(self) -> None:
        """Update GitHub API status in dashboard."""
        try:
            dashboard = self._app._dashboard()
        except RuntimeError:
            return

        from cereal_killer.kb.cve_jit import get_rate_snapshot as _get_rate

        snapshot = _get_rate()
        if snapshot is None:
            dashboard.set_github_api_status("unknown")
            return
        reset_in = max(0, int((snapshot.reset_epoch - int(_time.time())) / 60))
        dashboard.set_github_api_status(f"{snapshot.remaining}/{snapshot.limit} (Resets in {reset_in}m)")

    def update_knowledge_sync_status(self) -> None:
        """Update knowledge sync status in dashboard."""
        try:
            dashboard = self._app._dashboard()
        except RuntimeError:
            return

        from mentor.kb.library_ingest import fetch_sync_status

        try:
            statuses = fetch_sync_status(self._app.kb.settings, ["ippsec", "gtfobins", "lolbas", "hacktricks", "payloads"])
            dashboard.set_knowledge_sync_status(statuses)
        except Exception:
            return

    def set_web_search_state(self, active: bool) -> None:
        """Update web search state indicator."""
        try:
            dashboard = self._app._dashboard()
        except RuntimeError:
            return
        dashboard.set_active_tool("Web Search" if active else "Idle")

    # Public aliases for delegation from app.py
    observe = _observe
    watch_clipboard = _watch_clipboard
    on_clipboard_detected = on_clipboard_image_detected
    refresh_status = run_system_readiness_check
    github_status = update_github_api_status
    sync_status = update_knowledge_sync_status
    web_search_state = set_web_search_state


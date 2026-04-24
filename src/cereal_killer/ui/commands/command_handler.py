"""Command routing and application result handling."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from cereal_killer.ui.app import CerealKillerApp

from mentor.engine.commands import CommandResult
from mentor.engine.commands import dispatch as dispatch_command

from ..base import resolve_dashboard

from .command_pipeline import PipelineCommand


class CommandHandler:
    """Handles command routing and result application."""

    def __init__(self, app: "CerealKillerApp") -> None:
        self._app = app
        # Pipeline for command execution
        self._pipeline_manager = None

    @property
    def _pipeline(self):
        """Get the pipeline manager, creating it if needed."""
        if self._pipeline_manager is None:
            from cereal_killer.ui.workers.pipeline_workers import PipelineWorkerManager
            self._pipeline_manager = PipelineWorkerManager(self._app)
        return self._pipeline_manager

    async def handle_command(self, prompt: str) -> None:
        """Route a slash command to the appropriate handler."""
        dashboard = resolve_dashboard(self._app)
        result = await dispatch_command(prompt, self._app.engine, self._app.kb.settings)
        if result is None:
            self._app._run_chat_worker(prompt)
            return

        await self._apply_command_result(result)
        if result.session_prefix == "__exit__":
            self._app.exit()
            return
        if result.session_prefix == "__loot__":
            self._app._run_loot_worker()
            return
        if result.session_prefix == "__vision__":
            dashboard = resolve_dashboard(self._app)
            vision_path = dashboard.get_visual_buffer_image_path()
            if vision_path is None:
                dashboard.append_system("No image in visual buffer. Upload or copy an image first.", style="yellow")
            else:
                self._app._run_vision_worker(str(vision_path), source_label="Clipboard")
            return
        if result.session_prefix == "__upload__":
            if result.upload_image_path:
                upload_path = Path(result.upload_image_path)
                self._app._prime_uploaded_image(upload_path, source="/upload")
                self._app._run_vision_worker(str(upload_path), source_label="/upload")
            else:
                dashboard.append_system("Upload command did not provide a path.", style="red")
            return
        if result.session_prefix == "__search__":
            if result.search_query:
                self._app._run_search_worker(result.search_query)
            else:
                dashboard.append_system("Search command did not provide a query.", style="red")
            return
        if result.session_prefix == "__sync_all__":
            self._app.notify("Sync-all launched. Knowledge bar will update after ingest.", title="Sync Status", severity="information")
            self._app._refresh_knowledge_sync_status()
            return
        if result.session_prefix == "__add_source__":
            if result.search_query:
                self._app.notify(
                    f"Crawling {result.search_query[:60]}...",
                    title="Add Source",
                    severity="information",
                )
                if self._looks_like_image_url(result.search_query):
                    dashboard.set_remote_image_candidate(result.search_query)
                    dashboard.append_system(
                        "Crawl source includes an image. Use [VIEW IMAGE] in the Media Drawer.",
                        style="bold cyan",
                    )
            return

        dashboard.set_active_tool("Idle")

    async def _apply_command_result(self, result: CommandResult) -> None:
        dashboard = resolve_dashboard(self._app)
        cleaned_message = self.strip_rich_tags(result.message)
        dashboard.append_system(cleaned_message, style="cyan")
        self._app._append_chat("assistant", cleaned_message)
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

    def _update_header_target(self, target: str | None = None) -> None:
        active_target = (target or self._app.current_target or "NONE").upper()
        self._app.title = "CEREAL KILLER"
        self._app.sub_title = f"TARGET: {active_target}"

    @staticmethod
    def strip_rich_tags(text: str) -> str:
        return text.replace("[red]", "").replace("[/red]", "").replace("[bold]", "").replace("[/bold]", "").replace("[dim]", "").replace("[/dim]", "")

    @staticmethod
    def _looks_like_image_url(url: str) -> bool:
        from urllib.parse import urlparse
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        if parsed.scheme not in {"http", "https"}:
            return False
        lowered_path = (parsed.path or "").lower()
        return any(lowered_path.endswith(suffix) for suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"})

    async def _handle_run_command(self, args: list[str]) -> CommandResult:
        """Run a shell command through the pipeline."""
        if not args:
            return CommandResult(
                message="[yellow]Usage:[/yellow] /run <command>  (e.g. /run nmap -sV target)"
            )

        command_str = " ".join(args)

        # Start pipeline if needed
        self._pipeline.start_pipeline()

        # Enqueue the command
        self._pipeline.run_command(command_str, timeout=60, feed_to_brain=True)

        return CommandResult(
            message=f"[cyan]Running:[/cyan] `{command_str}`\n[dim]Output will be fed to Zero Cool for analysis...[/dim]"
        )

    _COMMANDS: dict[str, Any] = {
        "run": "_handle_run_command",
    }

    _dashboard = resolve_dashboard

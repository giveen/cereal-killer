"""Pipeline worker that integrates CommandPipeline with the UI system."""
from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING, Any

from ..base import resolve_dashboard
from ..commands.command_pipeline import CommandPipeline, PipelineCommand, PipelineResult

if TYPE_CHECKING:
    from cereal_killer.ui.app import CerealKillerApp


class PipelineWorkerManager:
    """Manages the command execution pipeline.

    Connects the CommandPipeline (created in command_pipeline.py)
    with the rest of the cereal-killer system. Takes PipelineCommand objects,
    runs them through CommandPipeline, and feeds results back to the brain.
    """

    def __init__(self, app: "CerealKillerApp") -> None:
        self._app = app
        self._pipeline = CommandPipeline(app.engine.settings)
        self._pipeline_task: asyncio.Task[None] | None = None

    @property
    def pipeline(self) -> CommandPipeline:
        """Return the underlying CommandPipeline instance."""
        return self._pipeline

    def start_pipeline(self) -> None:
        """Start the pipeline drain loop (called once at startup)."""
        if self._pipeline_task is None or self._pipeline_task.done():
            self._pipeline_task = asyncio.create_task(self._pipeline._drain_loop())

    def stop_pipeline(self) -> None:
        """Stop the pipeline drain loop."""
        if self._pipeline_task:
            self._pipeline_task.cancel()
            self._pipeline_task = None

    def run_command(
        self,
        command_str: str,
        timeout: int = 30,
        feed_to_brain: bool = True,
    ) -> None:
        """Enqueue a command for execution.

        Args:
            command_str: Shell command to execute.
            timeout: Maximum execution time in seconds.
            feed_to_brain: Whether to feed the result to the brain for analysis.
        """
        cmd = PipelineCommand(
            command=command_str,
            timeout=timeout,
            expect_output=True,
            feed_to_brain=feed_to_brain,
        )
        task = self._pipeline.enqueue(cmd)
        # Store the task for later result handling
        asyncio.ensure_future(
            self._handle_pipeline_result(task, command_str, feed_to_brain)
        )

    async def _handle_pipeline_result(
        self,
        result_task: asyncio.Task[PipelineResult],
        command_str: str,
        feed_to_brain: bool,
    ) -> None:
        """Handle the result of a pipeline command."""
        try:
            result = await result_task
        except Exception as exc:
            await self._show_error(f"Command failed: {exc}")
            return

        dashboard = resolve_dashboard(self._app)
        if dashboard is None:
            return

        # Show command status
        status_style = "green" if result.exit_code == 0 else "red"
        dashboard.append_system(
            f"[{status_style}] Command completed: {result.command}",
            style=status_style,
        )

        # Show output if it exists
        if result.stdout and result.stdout.strip():
            dashboard.append_system(
                f"Output ({len(result.stdout.splitlines())} lines):\n{result.stdout[:2000]}",
                style="dim",
            )

        # Feed to brain if requested
        if result.feed_brain and result.stdout.strip():
            await self._feed_to_brain(result, command_str, dashboard)

    async def _feed_to_brain(
        self, result: PipelineResult, command_str: str, dashboard: Any
    ) -> None:
        """Feed command output to the brain for analysis."""
        output_preview = result.stdout[:1000] if result.stdout else "No output"

        try:
            response = await self._app.engine.diagnose_failure(
                feedback_line=result.stderr[:500] if result.stderr else "",
                history_commands=self._app.active_history,
                pathetic_meter=self._app.engine.active_pathetic_meter(),
            )
            # Display response
            dashboard.append_assistant(
                response.answer if hasattr(response, "answer") else str(response)
            )
        except Exception as exc:
            dashboard.append_system(
                f"Brain analysis error: {exc}", style="red"
            )

    async def _show_error(self, message: str) -> None:
        """Show an error message on the dashboard."""
        dashboard = resolve_dashboard(self._app)
        if dashboard:
            dashboard.append_system(message, style="red")

    def run_nmap_scan(self, target: str, ports: str = "default") -> None:
        """Convenience method for running nmap scans.

        Args:
            target: Target host or IP address.
            ports: Port specification. Use 'default' for -sV scan,
                   or a port range like '1-1000'.
        """
        cmd = f"nmap {'-sV' if ports == 'default' else f'-p {ports}'} {target}"
        self.run_command(cmd, timeout=120, feed_to_brain=True)

    def run_feroxbuster(
        self, url: str, wordlist: str | None = None
    ) -> None:
        """Convenience method for running feroxbuster.

        Args:
            url: Target URL to scan.
            wordlist: Path to wordlist file. Uses a default SecLists path if not provided.
        """
        wl = wordlist or "/usr/share/seclists/Discovery/Web-Content/feroxbuster-large.txt"
        cmd = f"feroxbuster -u {url} -w {wl}"
        self.run_command(cmd, timeout=120, feed_to_brain=True)

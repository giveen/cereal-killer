from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from textual.widgets import Markdown
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cereal_killer.ui.app import CerealKillerApp

from ..base import resolve_dashboard, update_llm_cache_metrics as _update_llm_cache_metrics_helper


class MiscWorkerManager:
    """Manages miscellaneous worker methods for autocoaching, Gibson buffer, and IPPSec links.

    Extracted from CerealKillerApp to encapsulate autocoach,
    Gibson thinking buffer, and IPPSec link-opening logic.
    """

    def __init__(self, app: "CerealKillerApp") -> None:
        self._app = app

    def _worker_name(self, method_name: str) -> str:
        return f"{self.__class__.__name__}.{method_name}"

    async def _run_autocoach_worker(self, command: str) -> None:
        """Run the autocoach worker which provides feedback on a command."""
        dashboard = self._dashboard()
        worker_name = self._worker_name("run_autocoach_worker")
        self._app._register_worker(worker_name, asyncio.current_task())
        self._app._analysis_busy(True)
        dashboard.set_active_tool("Brain")
        try:
            await self._app._with_worker_cancellation(self._autocoach_body(command))
        except Exception as exc:
            dashboard.append_system(f"Auto-coach error: {exc}", style="red")
        finally:
            dashboard.set_active_tool("Idle")
            self._app._analysis_busy(False)
            self._app._unregister_worker(worker_name)

    async def _autocoach_body(self, command: str) -> None:
        """Body of the autocoach worker that calls the engine."""
        response = await self._app.engine.react_to_command(
            command,
            self._app.active_history,
            pathetic_meter=self._app.pathetic_meter,
        )
        self._update_llm_cache_metrics(response.backend_meta)
        await self._app._consume_llm_response(response.answer, response.reasoning_content or response.thought)

    async def _refresh_gibson_thinking_buffer(self) -> None:
        """Refresh the Gibson thinking buffer from engine state."""
        if not hasattr(self._app.engine, "get_thinking_buffer"):
            return

        machine_name = self._app.current_target or Path.cwd().name
        try:
            thought_buffer = await self._app.engine.get_thinking_buffer(machine_name, max_chars=8000)
        except Exception:
            return
        if not thought_buffer.strip():
            return

        dashboard = self._dashboard()
        if dashboard is None or self._app._gibson_snippets:
            return

        dashboard.query_one("#gibson_viewer", Markdown).update(
            "# SESSION THOUGHT BUFFER\n\n"
            "(Stored locally in Redis and excluded from normal next-turn prompt context unless requested.)\n\n"
            f"```text\n{thought_buffer.strip()}\n```"
        )

    def _open_ippsec_link(self, machine_name: str) -> None:
        """Open IPPSec YouTube link in default browser."""
        try:
            machine_safe = machine_name.replace("_", "-").replace(" ", "-").lower()
            url = f"https://ippsec.rocks/?n={machine_safe}"

            opener = "xdg-open" if shutil.which("xdg-open") else ("open" if shutil.which("open") else None)
            if opener:
                subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                dashboard = self._dashboard()
                if dashboard is not None:
                    dashboard.append_system(f"📺 Opened IppSec video: {url}", style="dim cyan")
        except Exception:
            pass

    # Public aliases for delegation from app.py
    run_autocoach_worker = _run_autocoach_worker
    autocoach_body = _autocoach_body
    refresh_gibson_thinking_buffer = _refresh_gibson_thinking_buffer

    def _update_llm_cache_metrics(self, backend_meta: dict[str, object] | None) -> None:
        _update_llm_cache_metrics_helper(self._app, backend_meta)

    _dashboard = resolve_dashboard

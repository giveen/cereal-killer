from __future__ import annotations

from typing import Any

from textual.widgets import Markdown
from textual import work

from cereal_killer.kb.cve_jit import extract_cve_ids, fetch_cve


class CVEJIT:
    """CVE JIT detection handler."""

    def __init__(self, app: Any) -> None:
        self._app = app

    @work(exclusive=False, thread=False, group="cve-jit")
    async def _run_cve_jit_worker(self, text: str) -> None:
        cve_ids = extract_cve_ids(text)
        if not cve_ids:
            return

        dashboard = self._app._dashboard()
        try:
            await self._app._with_worker_cancellation(self._cve_jit_body(text, dashboard))
        except Exception:
            pass

    async def _cve_jit_body(self, text: str, dashboard) -> None:
        """Extract CVE IDs and fetch details for each."""
        cve_ids = extract_cve_ids(text)
        if not cve_ids:
            return

        def _warn(message: str) -> None:
            dashboard.append_system(message, style="bold red")

        # Get the findings widget if it exists
        try:
            findings_widget = dashboard.query_one("#findings_widget", None)
        except Exception:
            findings_widget = None

        for cve_id in cve_ids:
            try:
                result = await fetch_cve(self._app.kb.settings, cve_id, warn=_warn)
                if findings_widget is not None and result:
                    desc = str(result.get("description", ""))[:100]
                    findings_widget.add_finding("cve", f"{cve_id}: {desc}", context="Auto-detected")
            except Exception:
                continue

        self._app._refresh_github_api_status()

    # Public aliases for delegation from app.py
    run_cve_jit_worker = _run_cve_jit_worker
    cve_jit_body = _cve_jit_body

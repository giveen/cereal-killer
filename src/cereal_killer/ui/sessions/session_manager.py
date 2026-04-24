"""Session snapshot and mental state persistence."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cereal_killer.ui.screens import MainDashboard
from ..base import resolve_dashboard
from mentor.ui.phase import detect_phase


class SessionManager:
    """Manages session snapshots and mental state persistence."""

    def __init__(self, app: Any) -> None:
        self._app = app

    def schedule_persist_mental_state(self) -> None:
        """Schedule persisting the mental state."""
        if hasattr(self._app.engine, "persist_mental_state"):
            asyncio.create_task(self._app.engine.persist_mental_state(self._app.active_history))

    def _save_session_snapshot(self, reason: str) -> None:
        """Save a session snapshot to disk."""
        session_dir = Path("data/sessions")
        session_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        payload = {
            "reason": reason,
            "timestamp": datetime.now(UTC).isoformat(),
            "cwd": str(Path.cwd()),
            "phase": detect_phase(self._app.active_history),
            "pathetic_meter": self._app.engine.active_pathetic_meter(),
            "history_context": self._app.active_history,
            "last_code_block": self._app.last_code_block,
            "chat": self._app.active_transcript,
        }
        target = session_dir / f"zero-cool-session-{timestamp}.json"
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _try_dashboard(self) -> Any:
        """Return MainDashboard or None."""
        return resolve_dashboard(self._app)

    def adjust_pathetic_meter(self) -> None:
        """Calculate and update the pathetic meter based on usage stats."""
        total = self._app.easy_usage_count + self._app.successful_command_count
        if total <= 0:
            self._app.pathetic_meter = 0
        else:
            ratio = self._app.easy_usage_count / total
            self._app.pathetic_meter = max(0, min(10, round(ratio * 10)))
        # Update dashboard via try_dashboard
        try:
            dashboard = self._try_dashboard()
            if dashboard:
                dashboard.set_pathetic_meter(self._app.pathetic_meter)
        except RuntimeError:
            pass

    def record_easy_usage(self, weight: int = 1) -> None:
        """Record an easy button usage and update meter."""
        self._app.easy_usage_count += max(1, weight)
        self.adjust_pathetic_meter()

    # Public aliases for delegation from app.py
    schedule_persist = schedule_persist_mental_state
    save_session_snapshot = _save_session_snapshot
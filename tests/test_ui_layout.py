from __future__ import annotations

import asyncio
import unittest

from textual.widgets import Input

from cereal_killer.ui.widgets import CommandInput

from cereal_killer.ui import CerealKillerApp
from cereal_killer.ui.screens import MainDashboard


class _DummyEngine:
    def __init__(self) -> None:
        self._web_cb = None
        self.settings = object()
        self._active_machine = ""

    async def persist_mental_state(self, _history_context: list[str]) -> None:
        return

    def set_web_search_callback(self, callback) -> None:
        self._web_cb = callback

    async def returning_greeting(self) -> str:
        return ""

    def record_phase_change(self, _phase: str) -> None:
        return

    def record_command_progress(self) -> None:
        return

    def set_active_machine(self, machine: str) -> None:
        self._active_machine = machine

    def active_pathetic_meter(self) -> int:
        return 0

    def prune_threshold(self) -> int:
        return 999999

    def prune_target(self) -> int:
        return 999000

    async def summarize_session(self, _session_text: str) -> str:
        return "summary"


class _DummyKB:
    class _Settings:
        llm_base_url = "http://localhost:8000/v1"
    settings = _Settings()


class _LayoutTestApp(CerealKillerApp):
    async def _run_boot_sequence(self) -> None:
        return

    async def _observe(self) -> None:
        return

    def _spawn_background_task(self, coro):
        coro.close()
        return asyncio.create_task(asyncio.sleep(0))

    def _save_session_snapshot(self, _reason: str) -> None:
        return


class DashboardLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_required_widget_ids_exist(self) -> None:
        app = _LayoutTestApp(engine=_DummyEngine(), kb=_DummyKB())
        async with app.run_test():
            dashboard = app.screen
            self.assertIsInstance(dashboard, MainDashboard)
            self.assertIsNotNone(dashboard.query_one("#chat_log"))
            self.assertIsNotNone(dashboard.query_one("#response_title"))
            self.assertIsNotNone(dashboard.query_one("#command_input", CommandInput))
            self.assertIsNotNone(dashboard.query_one("#gibson_search_input"))

    async def test_sidebar_responsive_hide_show(self) -> None:
        app = _LayoutTestApp(engine=_DummyEngine(), kb=_DummyKB())
        async with app.run_test():
            dashboard = app.screen
            assert isinstance(dashboard, MainDashboard)
            sidebar = dashboard.query_one("#intel_sidebar")
            dashboard.apply_responsive_layout(100)
            self.assertTrue(sidebar.styles.display == "none")
            dashboard.apply_responsive_layout(160)
            self.assertFalse(sidebar.styles.display == "none")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from textual.widgets import Input, RichLog

from cereal_killer.ui import CerealKillerApp
from cereal_killer.ui.screens import MainDashboard


class _DummyEngine:
    def __init__(self) -> None:
        self._web_cb = None
        self.settings = object()

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

    def prune_threshold(self) -> int:
        return 999999

    def prune_target(self) -> int:
        return 999000

    async def summarize_session(self, _session_text: str) -> str:
        return "summary"


class _DummyKB:
    settings = object()


class _LayoutTestApp(CerealKillerApp):
    async def _run_boot_sequence(self) -> None:
        return

    async def _observe(self) -> None:
        return

    def _save_session_snapshot(self, _reason: str) -> None:
        return


class DashboardLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_required_widget_ids_exist(self) -> None:
        app = _LayoutTestApp(engine=_DummyEngine(), kb=_DummyKB())
        async with app.run_test():
            dashboard = app.screen
            self.assertIsInstance(dashboard, MainDashboard)
            self.assertIsNotNone(dashboard.query_one("#chat_log", RichLog))
            self.assertIsNotNone(dashboard.query_one("#thought_box"))
            self.assertIsNotNone(dashboard.query_one("#command_input", Input))
            self.assertIsNotNone(dashboard.query_one("#easy_button"))

    async def test_sidebar_responsive_hide_show(self) -> None:
        app = _LayoutTestApp(engine=_DummyEngine(), kb=_DummyKB())
        async with app.run_test():
            dashboard = app.screen
            assert isinstance(dashboard, MainDashboard)
            sidebar = dashboard.query_one("#intel_sidebar")
            dashboard.apply_responsive_layout(100)
            self.assertTrue(sidebar.has_class("hidden"))
            dashboard.apply_responsive_layout(160)
            self.assertFalse(sidebar.has_class("hidden"))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from textual.widgets import Button, Collapsible, Input, RichLog

from cereal_killer.ui import MainDashboard


class _DummyEngine:
    def __init__(self) -> None:
        self._web_cb = None

    async def persist_mental_state(self, _history_context: list[str]) -> None:
        return

    def set_web_search_callback(self, callback) -> None:
        self._web_cb = callback

    async def returning_greeting(self) -> str:
        return ""


class _DummyKB:
    pass


class _LayoutTestDashboard(MainDashboard):
    async def _run_boot_sequence(self) -> None:
        return

    async def _observe(self) -> None:
        return


class DashboardLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_required_widget_ids_exist(self) -> None:
        app = _LayoutTestDashboard(engine=_DummyEngine(), kb=_DummyKB())
        async with app.run_test():
            self.assertIsNotNone(app.query_one("#chat_log", RichLog))
            self.assertIsNotNone(app.query_one("#reasoning_box", Collapsible))
            self.assertIsNotNone(app.query_one("#user_prompt", Input))
            self.assertIsNotNone(app.query_one("#terminal_feed", RichLog))
            self.assertIsNotNone(app.query_one("#easy_button", Button))

    async def test_sidebar_responsive_hide_show(self) -> None:
        app = _LayoutTestDashboard(engine=_DummyEngine(), kb=_DummyKB())
        async with app.run_test():
            sidebar = app.query_one("#intel_sidebar")
            app._apply_responsive_layout(100)
            self.assertTrue(sidebar.has_class("hidden"))
            app._apply_responsive_layout(160)
            self.assertFalse(sidebar.has_class("hidden"))


if __name__ == "__main__":
    unittest.main()

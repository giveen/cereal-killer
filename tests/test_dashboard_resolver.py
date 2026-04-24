"""Tests for the centralized dashboard resolution utility."""
import unittest
from unittest.mock import MagicMock


class TestResolveDashboard(unittest.TestCase):
    """Tests for resolve_dashboard() and require_dashboard()."""

    def setUp(self) -> None:
        # Import inside setUp to avoid circular import at module level
        from cereal_killer.ui.base import resolve_dashboard, require_dashboard

        self.resolve_dashboard = resolve_dashboard
        self.require_dashboard = require_dashboard

    def _make_mock_dashboard(self):
        """Create a mock that will pass isinstance checks for MainDashboard."""
        from cereal_killer.ui.screens import MainDashboard

        # We can't instantiate MainDashboard easily, so we mock the type check
        # by patching the import inside resolve_dashboard
        dashboard = MagicMock(spec=[])
        return dashboard

    def test_returns_active_screen_when_it_is_main_dashboard(self) -> None:
        """resolve_dashboard returns the active screen when it's a MainDashboard."""
        from cereal_killer.ui.screens import MainDashboard

        mock_dashboard = MagicMock(spec=MainDashboard)
        mock_app = MagicMock()
        mock_app.screen = mock_dashboard
        mock_app.screen_stack = []

        result = self.resolve_dashboard(mock_app)
        self.assertIs(result, mock_dashboard)

    def test_finds_main_dashboard_in_screen_stack(self) -> None:
        """resolve_dashboard finds MainDashboard in the screen stack."""
        from cereal_killer.ui.screens import MainDashboard

        mock_dashboard = MagicMock(spec=MainDashboard)
        mock_other = MagicMock()
        mock_app = MagicMock()
        mock_app.screen = mock_other  # Active screen is NOT MainDashboard
        mock_app.screen_stack = [mock_other, mock_dashboard]

        result = self.resolve_dashboard(mock_app)
        self.assertIs(result, mock_dashboard)

    def test_finds_main_dashboard_searching_stack_in_reverse(self) -> None:
        """resolve_dashboard searches screen_stack in reverse order."""
        from cereal_killer.ui.screens import MainDashboard

        # Most recent (top of stack) should be found first
        mock_dashboard_recent = MagicMock(spec=MainDashboard)
        mock_dashboard_old = MagicMock(spec=MainDashboard)
        mock_other = MagicMock()
        mock_app = MagicMock()
        mock_app.screen = mock_other
        mock_app.screen_stack = [mock_dashboard_old, mock_dashboard_recent]

        result = self.resolve_dashboard(mock_app)
        # reversed() means the most recent (last in list) is checked first
        self.assertIs(result, mock_dashboard_recent)

    def test_returns_none_when_main_dashboard_not_found(self) -> None:
        """resolve_dashboard returns None when MainDashboard is not in the stack."""
        mock_other = MagicMock()
        mock_app = MagicMock()
        mock_app.screen = mock_other
        mock_app.screen_stack = [mock_other, MagicMock()]

        result = self.resolve_dashboard(mock_app)
        self.assertIsNone(result)

    def test_returns_none_when_screen_stack_is_empty(self) -> None:
        """resolve_dashboard returns None when screen_stack is empty and screen is not MainDashboard."""
        mock_other = MagicMock()
        mock_app = MagicMock()
        mock_app.screen = mock_other
        mock_app.screen_stack = []

        result = self.resolve_dashboard(mock_app)
        self.assertIsNone(result)

    def test_handles_app_without_screen_stack_attribute(self) -> None:
        """resolve_dashboard handles apps that don't have screen_stack attribute."""
        from cereal_killer.ui.screens import MainDashboard

        mock_dashboard = MagicMock(spec=MainDashboard)
        mock_app = MagicMock(spec=["screen"])  # No screen_stack
        mock_app.screen = mock_dashboard

        result = self.resolve_dashboard(mock_app)
        self.assertIs(result, mock_dashboard)

    def test_handles_app_with_no_main_dashboard_and_no_screen_stack(self) -> None:
        """resolve_dashboard returns None when app has no screen_stack and screen is not MainDashboard."""
        mock_other = MagicMock()
        mock_app = MagicMock(spec=["screen"])  # No screen_stack
        mock_app.screen = mock_other

        result = self.resolve_dashboard(mock_app)
        self.assertIsNone(result)


class TestRequireDashboard(unittest.TestCase):
    """Tests for require_dashboard() — the strict variant."""

    def setUp(self) -> None:
        from cereal_killer.ui.base import require_dashboard
        self.require_dashboard = require_dashboard

    def test_returns_dashboard_when_found(self) -> None:
        """require_dashboard returns the dashboard when it's active."""
        from cereal_killer.ui.screens import MainDashboard

        mock_dashboard = MagicMock(spec=MainDashboard)
        mock_app = MagicMock()
        mock_app.screen = mock_dashboard
        mock_app.screen_stack = []

        result = self.require_dashboard(mock_app)
        self.assertIs(result, mock_dashboard)

    def test_raises_runtime_error_when_not_found(self) -> None:
        """require_dashboard raises RuntimeError when MainDashboard is not found."""
        mock_other = MagicMock()
        mock_app = MagicMock()
        mock_app.screen = mock_other
        mock_app.screen_stack = []

        with self.assertRaises(RuntimeError) as ctx:
            self.require_dashboard(mock_app)
        self.assertIn("MainDashboard is not active", str(ctx.exception))

    def test_raises_runtime_error_when_screen_stack_empty(self) -> None:
        """require_dashboard raises RuntimeError when screen_stack is empty and screen is not MainDashboard."""
        mock_other = MagicMock()
        mock_app = MagicMock()
        mock_app.screen = mock_other
        mock_app.screen_stack = []

        with self.assertRaises(RuntimeError):
            self.require_dashboard(mock_app)


if __name__ == "__main__":
    unittest.main()

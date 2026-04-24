"""View manager - extracted from dashboard.py."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DirectoryTree, LoadingIndicator, Markdown, Static

from cereal_killer.ui.widgets import SidebarStatus

if TYPE_CHECKING:
    from cereal_killer.ui.screens.dashboard import MainDashboard


class ViewManager:
    """Manages view switching, tabs, and responsive layout.

    All methods delegate back to the dashboard's widget queries.
    """

    def __init__(self, dashboard: MainDashboard) -> None:
        self._dashboard = dashboard

    def set_active_view(self, view: str) -> None:
        """Switch between chat, ops, and gibson views."""
        explorer = self._dashboard.query_one("#explorer_pane", Vertical)
        left_pane = self._dashboard.query_one("#left_pane", Vertical)
        sidebar = self._dashboard.query_one("#intel_sidebar", SidebarStatus)
        gibson_pane = self._dashboard.query_one("#gibson_pane", Vertical)
        bottom_row = self._dashboard.query_one("#bottom_row", Horizontal)
        tab_chat = self._dashboard.query_one("#tab_chat", Button)
        tab_ops = self._dashboard.query_one("#tab_ops", Button)
        tab_gibson = self._dashboard.query_one("#tab_gibson", Button)
        main_row = self._dashboard.query_one("#main_row", Horizontal)

        if view == "ops":
            self._dashboard._active_view = "ops"
            explorer.styles.display = "none"
            left_pane.styles.display = "none"
            sidebar.styles.display = "block"
            gibson_pane.styles.display = "none"
            bottom_row.styles.display = "block"
            self._set_tab_states(tab_chat, tab_ops, tab_gibson, active="ops")
        elif view == "gibson":
            self._dashboard._active_view = "gibson"
            explorer.styles.display = "none"
            left_pane.styles.display = "none"
            sidebar.styles.display = "none"
            gibson_pane.styles.display = "block"
            bottom_row.styles.display = "none"
            self._set_tab_states(tab_chat, tab_ops, tab_gibson, active="gibson")
        else:
            self._dashboard._active_view = "chat"
            explorer.styles.display = "block"
            left_pane.styles.display = "block"
            sidebar.styles.display = "none"
            gibson_pane.styles.display = "none"
            bottom_row.styles.display = "block"
            self._set_tab_states(tab_chat, tab_ops, tab_gibson, active="chat")

        # Brief tint pulse to mimic CRT refresh when changing views.
        self._dashboard.add_class("crt-refresh")
        self._dashboard.set_timer(0.12, lambda: self._dashboard.remove_class("crt-refresh"))
        main_row.styles.opacity = 0.92
        main_row.styles.animate("opacity", 1.0, duration=0.16)

        # Cancel pending workers from other views to prevent race conditions
        if hasattr(self._dashboard.app, "cancel_all_workers"):
            self._dashboard.app.cancel_all_workers()

    @staticmethod
    def _set_tab_states(
        tab_chat: Button, tab_ops: Button, tab_gibson: Button, *, active: str
    ) -> None:
        """Update tab button states based on active view."""
        tab_map = {"chat": tab_chat, "ops": tab_ops, "gibson": tab_gibson}
        for name, button in tab_map.items():
            button.variant = "default"
            button.remove_class("active-tab")
            button.add_class("inactive-tab")
            if name == active:
                button.add_class("active-tab")
                button.remove_class("inactive-tab")

    def apply_responsive_layout(self, width: int) -> None:
        """Apply responsive layout based on terminal width."""
        from textual.widgets import Markdown

        explorer = self._dashboard.query_one("#explorer_pane", Vertical)
        left_pane = self._dashboard.query_one("#left_pane", Vertical)
        sidebar = self._dashboard.query_one("#intel_sidebar", SidebarStatus)
        response_title = self._dashboard.query_one("#response_title", Static)
        response_markdown = self._dashboard.query_one("#response_markdown", Markdown)
        response_actions = self._dashboard.query_one("#response_actions", Horizontal)
        easy_button = self._dashboard.query_one("#easy_button", Button)

        # Base layout for medium and larger terminals.
        explorer.styles.display = "block"
        left_pane.styles.width = "2fr"
        left_pane.styles.margin_right = 1
        sidebar.styles.display = "block"
        response_title.styles.display = "block"
        response_markdown.styles.display = "block"
        response_actions.styles.display = "block"
        easy_button.styles.display = "block"

        if width < 100:
            explorer.styles.display = "none"
            sidebar.styles.display = "none"
            response_title.styles.display = "none"
            response_markdown.styles.display = "none"
            response_actions.styles.display = "none"
            easy_button.styles.display = "none"
            left_pane.styles.width = "1fr"
            left_pane.styles.margin_right = 0
            self.set_active_view("chat")
            return

        if width < 140:
            explorer.styles.display = "none"
            sidebar.styles.display = "none"
            response_title.styles.display = "none"
            response_markdown.styles.display = "none"
            response_actions.styles.display = "none"
            easy_button.styles.display = "none"
            left_pane.styles.width = "2fr"
            self.set_active_view("chat")
            return

        if width < 180:
            explorer.styles.display = "none"
            response_title.styles.display = "none"
            response_markdown.styles.display = "none"
            response_actions.styles.display = "none"
            easy_button.styles.display = "none"
            left_pane.styles.width = "2fr"
            self.set_active_view("chat")
            sidebar.styles.display = "block"
            return

        if width < 220:
            response_title.styles.display = "none"
            response_markdown.styles.display = "none"
            response_actions.styles.display = "none"
            easy_button.styles.display = "none"

        # Preserve explicit tab selection for larger layouts.
        self.set_active_view(self._dashboard._active_view)

    def toggle_upload_tree(self) -> None:
        """Toggle the file tree open/closed."""
        tree = self._dashboard.query_one("#upload_tree", DirectoryTree)
        explorer = self._dashboard.query_one("#explorer_pane", Vertical)
        if tree.styles.display == "none":
            tree.styles.display = "block"
            explorer.styles.width = "30"
        else:
            tree.styles.display = "none"
            explorer.styles.width = "auto"

    def set_upload_root(self, root_path: Path) -> None:
        """Set the directory tree root path."""
        tree = self._dashboard.query_one("#upload_tree", DirectoryTree)
        target = root_path.expanduser().resolve()
        tree.path = target
        tree.root.label = str(target)
        tree.reload()
        # Keep tree hidden until user explicitly toggles it.
        tree.styles.display = "none"

    def set_loading(self, active: bool) -> None:
        """Show/hide loading indicator."""
        indicator = self._dashboard.query_one("#analysis_loading", LoadingIndicator)
        indicator.display = active

    def set_active_tool(self, tool_name: str) -> None:
        """Update active tool display."""
        self._dashboard.query_one("#active_tool", Static).update(f"TOOL: {tool_name}")

    def show_gibson_view(self) -> None:
        """Show gibson view."""
        self.set_active_view("gibson")

    def chat_log(self) -> VerticalScroll:
        """Return the chat log widget."""
        return self._dashboard.query_one("#chat_log", VerticalScroll)

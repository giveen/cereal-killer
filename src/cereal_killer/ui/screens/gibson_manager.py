"""Gibson search and results manager - extracted from dashboard.py."""
from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import VerticalScroll
from textual.widgets import LoadingIndicator, Markdown, OptionList

if TYPE_CHECKING:
    from cereal_killer.ui.screens.dashboard import MainDashboard


class GibsonManager:
    """Manages Gibson search results, grouping, and display.

    All methods delegate back to the dashboard's widget queries.
    """

    def __init__(self, dashboard: MainDashboard) -> None:
        self._dashboard = dashboard

    def set_gibson_results(self, snippets: list[dict]) -> None:
        """Populate grouped Gibson results with collapsible title groups."""
        list_pane = self._dashboard.query_one("#gibson_list_pane", VerticalScroll)
        viewer_pane = self._dashboard.query_one("#gibson_viewer_pane", VerticalScroll)
        self._dashboard._gibson_all_snippets = list(snippets)
        self._render_gibson_grouped_options()

        if snippets:
            self.show_gibson_snippet(snippets[0])
        else:
            self._dashboard.query_one("#gibson_viewer", Markdown).update(
                "_No results found._"
            )

        # Fade-in animation for new Gibson results.
        list_pane.styles.opacity = 0.0
        viewer_pane.styles.opacity = 0.0
        list_pane.styles.animate("opacity", 1.0, duration=0.24)
        viewer_pane.styles.animate("opacity", 1.0, duration=0.24)

    def _render_gibson_grouped_options(self) -> None:
        """Render the grouped option list for Gibson results."""
        option_list = self._dashboard.query_one(
            "#gibson_result_list", OptionList
        )
        option_list.clear_options()
        self._dashboard._gibson_option_rows = []

        grouped: dict[str, list[dict]] = {}
        for snippet in self._dashboard._gibson_all_snippets:
            title = (snippet.get("title") or "untitled").strip() or "untitled"
            key = title.lower()
            grouped.setdefault(key, []).append(snippet)

        for group_key, members in sorted(
            grouped.items(), key=lambda item: (-len(item[1]), item[0])
        ):
            title = (members[0].get("title") or "untitled").strip() or "untitled"
            collapsed = self._dashboard._gibson_group_collapsed.get(
                group_key, False
            )
            marker = "▶" if collapsed else "▼"
            by_source: dict[str, int] = {}
            for member in members:
                source = str(member.get("source") or "?").strip().lower()
                by_source[source] = by_source.get(source, 0) + 1
            source_mix = ", ".join(
                f"{name}:{count}"
                for name, count in sorted(
                    by_source.items(), key=lambda item: (-item[1], item[0])
                )
            )
            label = f"{marker} {title[:40]} ({len(members)}) [{source_mix}]"
            option_list.add_option(label)
            self._dashboard._gibson_option_rows.append(
                {"kind": "group", "group": group_key}
            )

            if collapsed:
                continue

            for snippet in members:
                source = str(snippet.get("source") or "?")
                source_label = source[:16]
                machine = str(snippet.get("machine") or "").strip()
                display = machine[:40] if machine else title[:40]
                option_list.add_option(f"   • [{source_label}] {display}")
                self._dashboard._gibson_option_rows.append(
                    {"kind": "item", "snippet": snippet, "group": group_key}
                )

    def resolve_gibson_selection(self, option_index: int) -> dict | None:
        """Handle option selection for Gibson results.

        Returns the selected snippet dict, or None for group toggles.
        """
        if option_index < 0 or option_index >= len(
            self._dashboard._gibson_option_rows
        ):
            return None

        row = self._dashboard._gibson_option_rows[option_index]
        kind = str(row.get("kind", ""))

        if kind == "item":
            snippet = row.get("snippet")
            return snippet if isinstance(snippet, dict) else None

        if kind == "group":
            group = str(row.get("group", ""))
            self._dashboard._gibson_group_collapsed[group] = not self._dashboard._gibson_group_collapsed.get(
                group, False
            )
            self._render_gibson_grouped_options()

        return None

    def show_gibson_summary(self, markdown_text: str) -> None:
        """Display markdown summary in the Gibson viewer."""
        self._dashboard.query_one("#gibson_viewer", Markdown).update(markdown_text)

    def show_gibson_snippet(self, snippet: dict) -> None:
        """Display a single snippet in the Gibson viewer."""
        self._dashboard.query_one(
            "#gibson_viewer", Markdown
        ).update(self._build_gibson_markdown(snippet))

    def set_gibson_loading(self, active: bool) -> None:
        """Show/hide the Gibson loading indicator."""
        self._dashboard.query_one(
            "#gibson_loading", LoadingIndicator
        ).display = active

    def focus_gibson_input(self) -> None:
        """Focus the Gibson search input widget."""
        from textual.widgets import Input

        self._dashboard.query_one("#gibson_search_input", Input).focus()

    @staticmethod
    def _build_gibson_markdown(snippet: dict) -> str:
        """Build markdown text for displaying a snippet."""
        parts: list[str] = []
        title = snippet.get("title", "")
        source = snippet.get("source", "")
        url = snippet.get("url", "")
        visual_image_url = snippet.get("visual_image_url", "")
        content = snippet.get("content", "")

        if title:
            parts.append(f"# {title}")
        if source:
            parts.append(f"> **Source:** {source}")
        if url:
            parts.append(f"> **URL:** [{url}]({url})")
        if visual_image_url:
            from urllib.parse import quote

            token = quote(str(visual_image_url), safe="")
            parts.append(f"> [VIEW IMAGE](view-image://{token})")
        parts.append("")
        parts.append(content)
        return "\n".join(parts)

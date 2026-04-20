from __future__ import annotations

import asyncio
import re
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, unquote

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DirectoryTree, Footer, Input, LoadingIndicator, Markdown, OptionList, Rule, Static

from mentor.utils.clipboard import copy_text

from .widgets import ChatMessage, CommandInput, SidebarStatus, VerticalProgressBar


CODE_BLOCK_RE = re.compile(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", re.DOTALL)
PROBABLE_COMMAND_RE = re.compile(
    r"^(?:\$\s*)?(?:sudo\s+)?(?:"
    r"nmap|curl|wget|ffuf|gobuster|feroxbuster|dirb|dirsearch|nikto|sqlmap|"
    r"nc|netcat|python|python3|bash|sh|ssh|ftp|smbclient|rpcclient|redis-cli|"
    r"docker(?:\s+compose)?|git|ls|cat|grep|find|chmod|chown|echo|export|cd|"
    r"cp|mv|awk|sed"
    r")\b"
)


class SolutionModal(ModalScreen[None]):
    """Fullscreen markdown modal for machine-specific walkthrough material."""

    def __init__(self, markdown_text: str) -> None:
        super().__init__()
        self.markdown_text = markdown_text

    def compose(self) -> ComposeResult:
        with Vertical(id="solution_shell"):
            yield Static("Decrypting walkthrough payload...", id="decryption_text")
            yield Markdown("", id="solution_markdown", open_links=False)
            yield Button("Close", id="solution_close", variant="primary")

    async def on_mount(self) -> None:
        animation_widget = self.query_one("#decryption_text", Static)
        markdown_widget = self.query_one("#solution_markdown", Markdown)
        frames = [
            "Decrypting walkthrough payload...",
            "[#####---------------] 25%",
            "[##########----------] 50%",
            "[###############-----] 75%",
            "[####################] 100%",
            "Payload decrypted. Rendering field notes...",
        ]
        for frame in frames:
            animation_widget.update(frame)
            await asyncio.sleep(0.18)
        animation_widget.display = False
        markdown_widget.update(self._inject_copy_links(self.markdown_text))

    @on(Markdown.LinkClicked, "#solution_markdown")
    def copy_markdown_code_block(self, event: Markdown.LinkClicked) -> None:
        href = event.href.strip()
        if href.startswith(("http://", "https://")):
            webbrowser.open(href)
            return
        if not href.startswith("copy://"):
            return
        encoded = href.replace("copy://", "", 1)
        command = unquote(encoded)
        copy_text(command, fallback=self.app.copy_to_clipboard)

    @staticmethod
    def _inject_copy_links(markdown_text: str) -> str:
        def replacer(match: re.Match[str]) -> str:
            body = match.group(1).strip()
            token = quote(body)
            return f"[COPY](copy://{token})\n```\n{body}\n```"

        return CODE_BLOCK_RE.sub(replacer, markdown_text)

    @on(Button.Pressed, "#solution_close")
    def close_modal(self) -> None:
        self.dismiss(None)


class InfrastructureCriticalModal(ModalScreen[None]):
    def __init__(self, detail: str = "") -> None:
        super().__init__()
        self.detail = detail

    def compose(self) -> ComposeResult:
        with Vertical(id="solution_shell"):
            yield Static("SYSTEM CRITICAL: INFRASTRUCTURE OFFLINE. SEE DOCS/SETUP.", id="decryption_text")
            msg = "Fix setup blockers before running workflows."
            if self.detail:
                msg += f"\n\nDetected hard failures: {self.detail}"
            yield Markdown(msg, id="solution_markdown", open_links=False)
            yield Button("Acknowledge", id="solution_close", variant="primary")

    @on(Button.Pressed, "#solution_close")
    def close_modal(self) -> None:
        self.dismiss(None)


@dataclass(slots=True)
class IngestSelection:
    path: Path
    ingest_type: str


class FilteredIngestTree(DirectoryTree):
    def __init__(self, path: str, *, allowed_suffixes: set[str], **kwargs: object) -> None:
        super().__init__(path, **kwargs)
        self.allowed_suffixes = {suffix.lower() for suffix in allowed_suffixes}

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        filtered: list[Path] = []
        for candidate in paths:
            try:
                if candidate.is_dir() or candidate.suffix.lower() in self.allowed_suffixes:
                    filtered.append(candidate)
            except OSError:
                continue
        return filtered


class IngestModal(ModalScreen[IngestSelection | None]):
    IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
    DOCUMENT_SUFFIXES = {".log", ".txt", ".json"}

    def __init__(self, ingest_type: str, root_path: Path) -> None:
        super().__init__()
        self.ingest_type = ingest_type
        self.root_path = root_path
        self._selected_path: Path | None = None

    @property
    def allowed_suffixes(self) -> set[str]:
        return self.IMAGE_SUFFIXES if self.ingest_type == "image" else self.DOCUMENT_SUFFIXES

    def compose(self) -> ComposeResult:
        mode_label = "SCREENSHOT INGEST" if self.ingest_type == "image" else "DOCUMENT INGEST"
        with Vertical(id="ingest_modal_shell"):
            yield Static(f"{mode_label} // Select source file", id="ingest_modal_title")
            yield FilteredIngestTree(
                str(self.root_path),
                id="ingest_modal_tree",
                allowed_suffixes=self.allowed_suffixes,
            )
            suffix_hint = ", ".join(sorted(self.allowed_suffixes))
            yield Static(f"Allowed: {suffix_hint}", id="ingest_modal_hint")
            yield Static("Selected: none", id="ingest_modal_selected")
            with Horizontal(id="ingest_modal_actions"):
                yield Button("[SUBMIT TO GIBSON]", id="ingest_modal_submit", variant="primary")
                yield Button("Cancel", id="ingest_modal_cancel", variant="default")

    @on(DirectoryTree.FileSelected, "#ingest_modal_tree")
    def on_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        chosen = Path(event.path)
        if chosen.suffix.lower() not in self.allowed_suffixes:
            self.notify("File type not allowed in this ingest mode", title="Ingest", severity="warning")
            return
        self._selected_path = chosen
        self.query_one("#ingest_modal_selected", Static).update(f"Selected: {chosen.name}")

    @on(Button.Pressed, "#ingest_modal_submit")
    def submit_ingest(self) -> None:
        if self._selected_path is None:
            self.notify("Select a file first", title="Ingest", severity="warning")
            return
        self.dismiss(IngestSelection(path=self._selected_path, ingest_type=self.ingest_type))

    @on(Button.Pressed, "#ingest_modal_cancel")
    def cancel_ingest(self) -> None:
        self.dismiss(None)


class MainDashboard(Screen[None]):
    def __init__(self) -> None:
        super().__init__()
        self._last_response_raw = ""
        self._last_response_markdown = ""
        self._active_view = "chat"
        self._gibson_group_collapsed: dict[str, bool] = {}
        self._gibson_option_rows: list[dict[str, object]] = []
        self._gibson_all_snippets: list[dict] = []

    async def on_mount(self) -> None:
        """Initialize display state to show chat view."""
        self.set_active_view("chat")

    def compose(self) -> ComposeResult:
        with Vertical(id="dashboard"):
            yield Static("GIBSON MAIN FRAME - AUTHORIZED ACCESS ONLY", id="main_header")
            with Horizontal(id="view_tabs"):
                yield Button("Chat", id="tab_chat", variant="primary")
                yield Button("Ops", id="tab_ops", variant="default")
                yield Button("Gibson", id="tab_gibson", variant="default")
            with Horizontal(id="main_row"):
                with Vertical(id="explorer_pane"):
                    with Vertical(id="ingest_icon_row"):
                        yield Button("📸", id="open_image_ingest", variant="default")
                        yield Button("📄", id="open_document_ingest", variant="default")
                    screenshots_dir = Path("/screenshots") if Path("/screenshots").exists() else Path.cwd()
                    yield DirectoryTree(str(screenshots_dir), id="upload_tree")
                with Vertical(id="left_pane"):
                    yield Static("", id="boot_status_box")
                    with Horizontal(id="context_chip_row"):
                        yield Static("NO ACTIVE FILE CONTEXT", id="context_chip", classes="context-chip muted-chip")
                        yield Static("Active Context: 0 / 0", id="context_token_counter", classes="context-chip muted-chip")
                    yield VerticalScroll(id="chat_log")
                    yield Static("LATEST RESPONSE", id="response_title")
                    yield Markdown("_No response yet._", id="response_markdown", open_links=False)
                    with Horizontal(id="response_actions"):
                        yield Button("Copy Response", id="copy_response", variant="default")
                        yield Button("Copy Code", id="copy_code", variant="primary")
                        yield LoadingIndicator(id="analysis_loading")
                yield SidebarStatus()
                with Vertical(id="gibson_pane"):
                    yield Static("▶ GIBSON // LOCAL KNOWLEDGE BASE", id="gibson_header")
                    yield Input(
                        placeholder="Search local RAG (HackTricks, IppSec, Payloads)…",
                        id="gibson_search_input",
                    )
                    yield Rule(id="gibson_rule")
                    with Horizontal(id="gibson_split"):
                        with VerticalScroll(id="gibson_list_pane"):
                            yield OptionList(id="gibson_result_list")
                        with VerticalScroll(id="gibson_viewer_pane"):
                            yield Markdown(
                                "_Run a search to load content._",
                                id="gibson_viewer",
                                open_links=False,
                            )
                    with Horizontal(id="gibson_actions"):
                        yield Button(
                            "⚡ Synthesize Master Cheat Sheet",
                            id="gibson_synthesize",
                            variant="warning",
                        )
                        yield LoadingIndicator(id="gibson_loading")
            with Horizontal(id="bottom_row"):
                yield CommandInput()
            yield Footer()

    def chat_log(self) -> VerticalScroll:
        return self.query_one("#chat_log", VerticalScroll)

    async def stream_thought(self, thought: str) -> None:
        """Thought panel removed; keep async call sites stable."""
        return

    def append_user(self, text: str) -> None:
        self._append_chat_message("user", text)

    def append_assistant(self, text: str) -> None:
        self._append_chat_message("assistant", text)
        self._update_response_markdown(text)

    def append_system(self, text: str, *, style: str = "yellow") -> None:
        self._append_chat_message("system", text)

    def _append_chat_message(self, role: str, text: str) -> None:
        container = self.chat_log()
        container.mount(ChatMessage(role=role, message=text))
        container.scroll_end(animate=False)

    def set_phase(self, phase: str) -> None:
        phase_widget = self.query_one("#current_phase", Static)
        for cls in (
            "phase-idle",
            "phase-recon",
            "phase-enumeration",
            "phase-exploitation",
            "phase-post",
        ):
            phase_widget.remove_class(cls)
        phase_widget.update(f"PHASE: {phase}")
        if phase == "[RECON]":
            phase_widget.add_class("phase-recon")
        elif phase == "[ENUMERATION]":
            phase_widget.add_class("phase-enumeration")
        elif phase == "[EXPLOITATION]":
            phase_widget.add_class("phase-exploitation")
        elif phase == "[POST-EXPLOITATION]":
            phase_widget.add_class("phase-post")
        else:
            phase_widget.add_class("phase-idle")

    def set_active_tool(self, tool_name: str) -> None:
        self.query_one("#active_tool", Static).update(f"TOOL: {tool_name}")

    def set_visual_buffer(self, description: str, preview: str = "") -> None:
        # Legacy compatibility shim; use set_visual_buffer_image for real image rendering.
        sidebar = self.query_one("#intel_sidebar", SidebarStatus)
        sidebar.set_remote_image_candidate(description if description.startswith(("http://", "https://")) else None)

    def set_visual_buffer_image(self, image_path: Path, *, source: str, preview: str = "") -> None:
        sidebar = self.query_one("#intel_sidebar", SidebarStatus)
        sidebar.set_visual_buffer_image(image_path, source=source, preview=preview)

    def set_remote_image_candidate(self, url: str | None) -> None:
        sidebar = self.query_one("#intel_sidebar", SidebarStatus)
        sidebar.set_remote_image_candidate(url)

    def get_remote_image_candidate(self) -> str | None:
        sidebar = self.query_one("#intel_sidebar", SidebarStatus)
        return sidebar.get_remote_image_candidate()

    def get_visual_buffer_image_path(self) -> Path | None:
        sidebar = self.query_one("#intel_sidebar", SidebarStatus)
        return sidebar.get_visual_buffer_image_path()

    def clear_visual_buffer(self) -> None:
        self.query_one("#intel_sidebar", SidebarStatus).clear_visual_buffer()

    def set_pathetic_meter(self, value: int) -> None:
        self.query_one("#pathetic_meter_bar", VerticalProgressBar).set_value(value)
        self.query_one("#pathetic_meter_value", Static).update(f"{value}/10")

    def set_terminal_link_online(self, online: bool) -> None:
        """Update terminal link status indicator."""
        sidebar = self.query_one("#intel_sidebar", SidebarStatus)
        sidebar.set_terminal_link_status(online)

    def set_knowledge_sync_status(self, statuses: dict[str, str]) -> None:
        sidebar = self.query_one("#intel_sidebar", SidebarStatus)
        sidebar.set_knowledge_sync_status(statuses)

    def set_github_api_status(self, summary: str) -> None:
        sidebar = self.query_one("#intel_sidebar", SidebarStatus)
        sidebar.set_github_api_status(summary)

    def set_system_readiness(self, ok: bool, details: str = "") -> None:
        sidebar = self.query_one("#intel_sidebar", SidebarStatus)
        sidebar.set_system_readiness(ok, details)

    def set_llm_cache_metrics(self, latency_ms: int | None, tokens_cached: int | None) -> None:
        sidebar = self.query_one("#intel_sidebar", SidebarStatus)
        sidebar.set_llm_cache_metrics(latency_ms, tokens_cached)

    def set_context_token_counter(self, current_tokens: int, max_tokens: int) -> None:
        counter = self.query_one("#context_token_counter", Static)
        counter.update(f"Active Context: {max(0, current_tokens)} / {max(0, max_tokens)}")

    async def pulse_terminal_link(self) -> None:
        """Pulse the terminal link indicator to show data is flowing."""
        sidebar = self.query_one("#intel_sidebar", SidebarStatus)
        await sidebar.pulse_terminal_link()

    def apply_responsive_layout(self, width: int) -> None:
        explorer = self.query_one("#explorer_pane", Vertical)
        left_pane = self.query_one("#left_pane", Vertical)
        sidebar = self.query_one("#intel_sidebar", SidebarStatus)
        response_title = self.query_one("#response_title", Static)
        response_markdown = self.query_one("#response_markdown", Markdown)
        response_actions = self.query_one("#response_actions", Horizontal)
        easy_button = self.query_one("#easy_button", Button)

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

        if width < 180:
            explorer.styles.display = "none"
            response_title.styles.display = "none"
            response_markdown.styles.display = "none"
            response_actions.styles.display = "none"
            easy_button.styles.display = "none"
            left_pane.styles.width = "2fr"
            self.set_active_view("chat")
            return

        if width < 220:
            response_title.styles.display = "none"
            response_markdown.styles.display = "none"
            response_actions.styles.display = "none"
            easy_button.styles.display = "none"

        # Preserve explicit tab selection for larger layouts.
        self.set_active_view(self._active_view)

    def toggle_upload_tree(self) -> None:
        """Toggle the file tree open/closed; the pane button strip stays visible."""
        tree = self.query_one("#upload_tree", DirectoryTree)
        explorer = self.query_one("#explorer_pane", Vertical)
        if tree.styles.display == "none":
            tree.styles.display = "block"
            explorer.styles.width = "30"
        else:
            tree.styles.display = "none"
            explorer.styles.width = "auto"

    @on(Button.Pressed, "#open_image_ingest")
    def on_open_image_ingest(self) -> None:
        action = getattr(self.app, "action_open_image_ingest", None)
        if callable(action):
            action()

    @on(Button.Pressed, "#open_document_ingest")
    def on_open_document_ingest(self) -> None:
        action = getattr(self.app, "action_open_document_ingest", None)
        if callable(action):
            action()

    @on(Button.Pressed, "#tab_chat")
    def show_chat_view(self) -> None:
        self.set_active_view("chat")

    @on(Button.Pressed, "#tab_ops")
    def show_ops_view(self) -> None:
        self.set_active_view("ops")

    def set_upload_root(self, root_path: Path) -> None:
        tree = self.query_one("#upload_tree", DirectoryTree)
        target = root_path.expanduser().resolve()
        tree.path = target
        tree.root.label = str(target)
        tree.reload()
        # Keep tree hidden until user explicitly toggles it.
        tree.styles.display = "none"

    def set_loading(self, active: bool) -> None:
        indicator = self.query_one("#analysis_loading", LoadingIndicator)
        indicator.display = active

    def set_upload_progress(self, value: int, label: str = "UPLOAD") -> None:
        sidebar = self.query_one("#intel_sidebar", SidebarStatus)
        sidebar.set_upload_progress(value, label)

    def set_context_chip(self, filename: str, *, ingest_type: str) -> None:
        chip = self.query_one("#context_chip", Static)
        marker = "IMG" if ingest_type == "image" else "DOC"
        chip.update(f"[{marker}] {filename}")
        chip.remove_class("muted-chip")
        chip.add_class("active-chip")

    def set_boot_status(self, text: str) -> None:
        box = self.query_one("#boot_status_box", Static)
        body = (text or "").strip()
        if body:
            box.update(body)
            box.styles.display = "block"
        else:
            box.update("")
            box.styles.display = "none"

    def set_active_view(self, view: str) -> None:
        explorer = self.query_one("#explorer_pane", Vertical)
        left_pane = self.query_one("#left_pane", Vertical)
        sidebar = self.query_one("#intel_sidebar", SidebarStatus)
        gibson_pane = self.query_one("#gibson_pane", Vertical)
        bottom_row = self.query_one("#bottom_row", Horizontal)
        tab_chat = self.query_one("#tab_chat", Button)
        tab_ops = self.query_one("#tab_ops", Button)
        tab_gibson = self.query_one("#tab_gibson", Button)
        main_row = self.query_one("#main_row", Horizontal)

        if view == "ops":
            self._active_view = "ops"
            explorer.styles.display = "none"
            left_pane.styles.display = "none"
            sidebar.styles.display = "block"
            gibson_pane.styles.display = "none"
            bottom_row.styles.display = "block"
            self._set_tab_states(tab_chat, tab_ops, tab_gibson, active="ops")
        elif view == "gibson":
            self._active_view = "gibson"
            explorer.styles.display = "none"
            left_pane.styles.display = "none"
            sidebar.styles.display = "none"
            gibson_pane.styles.display = "block"
            bottom_row.styles.display = "none"
            self._set_tab_states(tab_chat, tab_ops, tab_gibson, active="gibson")
        else:
            self._active_view = "chat"
            explorer.styles.display = "block"
            left_pane.styles.display = "block"
            sidebar.styles.display = "none"
            gibson_pane.styles.display = "none"
            bottom_row.styles.display = "block"
            self._set_tab_states(tab_chat, tab_ops, tab_gibson, active="chat")

        # Brief tint pulse to mimic CRT refresh when changing views.
        self.add_class("crt-refresh")
        self.set_timer(0.12, lambda: self.remove_class("crt-refresh"))
        main_row.styles.opacity = 0.92
        main_row.styles.animate("opacity", 1.0, duration=0.16)

    @on(Button.Pressed, "#tab_gibson")
    def show_gibson_view(self) -> None:
        self.set_active_view("gibson")
    # ── Gibson helpers ──────────────────────────────────────────────────────

    def set_gibson_results(self, snippets: list[dict]) -> None:
        """Populate grouped Gibson results with collapsible title groups."""
        list_pane = self.query_one("#gibson_list_pane", VerticalScroll)
        viewer_pane = self.query_one("#gibson_viewer_pane", VerticalScroll)
        self._gibson_all_snippets = list(snippets)
        self._render_gibson_grouped_options()

        if snippets:
            self.show_gibson_snippet(snippets[0])
        else:
            self.query_one("#gibson_viewer", Markdown).update("_No results found._")

        # Fade-in animation for new Gibson results.
        list_pane.styles.opacity = 0.0
        viewer_pane.styles.opacity = 0.0
        list_pane.styles.animate("opacity", 1.0, duration=0.24)
        viewer_pane.styles.animate("opacity", 1.0, duration=0.24)

    def _render_gibson_grouped_options(self) -> None:
        option_list = self.query_one("#gibson_result_list", OptionList)
        option_list.clear_options()
        self._gibson_option_rows = []

        grouped: dict[str, list[dict]] = {}
        for snippet in self._gibson_all_snippets:
            title = (snippet.get("title") or "untitled").strip() or "untitled"
            key = title.lower()
            grouped.setdefault(key, []).append(snippet)

        for group_key, members in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
            title = (members[0].get("title") or "untitled").strip() or "untitled"
            collapsed = self._gibson_group_collapsed.get(group_key, False)
            marker = "▶" if collapsed else "▼"
            by_source: dict[str, int] = {}
            for member in members:
                source = str(member.get("source") or "?").strip().lower()
                by_source[source] = by_source.get(source, 0) + 1
            source_mix = ", ".join(
                f"{name}:{count}"
                for name, count in sorted(by_source.items(), key=lambda item: (-item[1], item[0]))
            )
            label = f"{marker} {title[:40]} ({len(members)}) [{source_mix}]"
            option_list.add_option(label)
            self._gibson_option_rows.append({"kind": "group", "group": group_key})

            if collapsed:
                continue

            for snippet in members:
                source = str(snippet.get("source") or "?")
                source_label = source[:16]
                machine = str(snippet.get("machine") or "").strip()
                display = machine[:40] if machine else title[:40]
                option_list.add_option(f"   • [{source_label}] {display}")
                self._gibson_option_rows.append({"kind": "item", "snippet": snippet, "group": group_key})

    def resolve_gibson_selection(self, option_index: int) -> dict | None:
        if option_index < 0 or option_index >= len(self._gibson_option_rows):
            return None
        row = self._gibson_option_rows[option_index]
        kind = str(row.get("kind", ""))
        if kind == "item":
            snippet = row.get("snippet")
            return snippet if isinstance(snippet, dict) else None

        if kind == "group":
            group = str(row.get("group", ""))
            self._gibson_group_collapsed[group] = not self._gibson_group_collapsed.get(group, False)
            self._render_gibson_grouped_options()
        return None

    def show_gibson_summary(self, markdown_text: str) -> None:
        self.query_one("#gibson_viewer", Markdown).update(markdown_text)

    def show_gibson_snippet(self, snippet: dict) -> None:
        self.query_one("#gibson_viewer", Markdown).update(
            self._build_gibson_markdown(snippet)
        )

    def set_gibson_loading(self, active: bool) -> None:
        self.query_one("#gibson_loading", LoadingIndicator).display = active

    def focus_gibson_input(self) -> None:
        self.query_one("#gibson_search_input", Input).focus()

    def focus_chat_input(self) -> None:
        self.query_one("#command_input", Input).focus()

    @staticmethod
    def _set_tab_states(tab_chat: Button, tab_ops: Button, tab_gibson: Button, *, active: str) -> None:
        tab_map = {"chat": tab_chat, "ops": tab_ops, "gibson": tab_gibson}
        for name, button in tab_map.items():
            button.variant = "default"
            button.remove_class("active-tab")
            button.add_class("inactive-tab")
            if name == active:
                button.add_class("active-tab")
                button.remove_class("inactive-tab")

    @staticmethod
    def _build_gibson_markdown(snippet: dict) -> str:
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
            token = quote(str(visual_image_url), safe="")
            parts.append(f"> [VIEW IMAGE](view-image://{token})")
        parts.append("")
        parts.append(content)
        return "\n".join(parts)

    @on(Markdown.LinkClicked, "#gibson_viewer")
    def on_gibson_link_clicked(self, event: Markdown.LinkClicked) -> None:
        href = event.href.strip()
        if href.startswith(("http://", "https://")):
            webbrowser.open(href)
            return
        if not href.startswith("view-image://"):
            return
        encoded = href.replace("view-image://", "", 1)
        remote_url = unquote(encoded).strip()
        if not remote_url:
            return
        loader = getattr(self.app, "queue_remote_visual_url", None)
        if callable(loader):
            loader(remote_url)


    def _update_response_markdown(self, text: str) -> None:
        self._last_response_raw = text or ""
        normalized = self._normalize_response_markdown(self._last_response_raw)
        self._last_response_markdown = normalized
        response_markdown = self.query_one("#response_markdown", Markdown)
        response_markdown.update(self._inject_copy_links(normalized))

    @staticmethod
    def _normalize_response_markdown(text: str) -> str:
        content = (text or "").replace("\r\n", "\n").strip()
        if not content:
            return "_No response yet._"
        if "```" in content:
            return content

        lines = content.splitlines()
        output: list[str] = []
        code_block: list[str] = []

        def flush_code() -> None:
            if not code_block:
                return
            output.append("```bash")
            output.extend(code_block)
            output.append("```")
            code_block.clear()

        for line in lines:
            stripped = line.strip()
            if MainDashboard._is_probable_command_line(stripped):
                command_line = stripped[2:] if stripped.startswith("$ ") else stripped
                code_block.append(command_line)
                continue
            flush_code()
            output.append(line)

        flush_code()
        return "\n".join(output).strip() or content

    @staticmethod
    def _is_probable_command_line(line: str) -> bool:
        if not line:
            return False
        if line.startswith(("http://", "https://")):
            return False
        return bool(PROBABLE_COMMAND_RE.match(line))

    @staticmethod
    def _inject_copy_links(markdown_text: str) -> str:
        def replacer(match: re.Match[str]) -> str:
            body = match.group(1).strip()
            token = quote(body)
            return f"[COPY](copy://{token})\n```\n{body}\n```"

        return CODE_BLOCK_RE.sub(replacer, markdown_text)

    @on(Markdown.LinkClicked, "#response_markdown")
    def copy_response_code_block(self, event: Markdown.LinkClicked) -> None:
        href = event.href.strip()
        if href.startswith(("http://", "https://")):
            webbrowser.open(href)
            return
        if not href.startswith("copy://"):
            return
        encoded = href.replace("copy://", "", 1)
        command = unquote(encoded)
        copy_text(command, fallback=self.app.copy_to_clipboard)
        self.app.notify("Copied code block to clipboard", title="Copied", severity="information")

    @on(Button.Pressed, "#copy_response")
    def copy_response_text(self) -> None:
        payload = self._last_response_raw.strip()
        if not payload:
            self.app.notify("No response to copy yet", title="Copy Response", severity="warning")
            return
        copy_text(payload, fallback=self.app.copy_to_clipboard)
        self.app.notify("Copied full response to clipboard", title="Copied", severity="information")

    @on(Button.Pressed, "#copy_code")
    def copy_latest_code_block(self) -> None:
        matches = CODE_BLOCK_RE.findall(self._last_response_markdown)
        if not matches:
            self.app.notify("No code block found in latest response", title="Copy Code", severity="warning")
            return
        copy_text(matches[-1].strip(), fallback=self.app.copy_to_clipboard)
        self.app.notify("Copied latest code block", title="Copied", severity="information")

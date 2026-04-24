"""Main dashboard screen - extracted from screens.py."""
from __future__ import annotations

import asyncio
import re
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DirectoryTree, Footer, Input, LoadingIndicator, Markdown, OptionList, Rule, Static

from cereal_killer.ui.widgets import ChatMessage, CommandInput, SidebarStatus, VerticalProgressBar
from ..screens import CODE_BLOCK_RE, PROBABLE_COMMAND_RE
from .gibson_manager import GibsonManager
from .response_manager import ResponseManager
from .status_manager import StatusManager
from .view_manager import ViewManager

# Re-export constants for backward compatibility
__all__ = ["MainDashboard", "CODE_BLOCK_RE", "PROBABLE_COMMAND_RE"]


class MainDashboard(Screen[None]):
    def __init__(self) -> None:
        super().__init__()
        self._last_response_raw = ""
        self._last_response_markdown = ""
        self._active_view = "chat"
        self._gibson_group_collapsed: dict[str, bool] = {}
        self._gibson_option_rows: list[dict[str, object]] = []
        self._gibson_all_snippets: list[dict] = []
        self._view_manager = ViewManager(self)
        self._streaming_active = False
        self._streaming_cancel_event: asyncio.Event | None = None
        self._gibson_manager = GibsonManager(self)
        self._response_manager = ResponseManager(self)
        self._status_manager = StatusManager(self)

    async def on_mount(self) -> None:
        """Initialize display state to show chat view."""
        self._view_manager.set_active_view("chat")

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
                        yield Button("Cancel", id="cancel_stream", variant="warning", classes="streaming-only")
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
        return self._view_manager.chat_log()

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
        self._status_manager.set_phase(phase)

    def set_active_tool(self, tool_name: str) -> None:
        self._status_manager.set_active_tool(tool_name)

    def set_visual_buffer(self, description: str, preview: str = "") -> None:
        self._status_manager.set_visual_buffer(description, preview=preview)

    def set_visual_buffer_image(self, image_path: Path, *, source: str, preview: str = "") -> None:
        self._status_manager.set_visual_buffer_image(image_path, source=source, preview=preview)

    def set_remote_image_candidate(self, url: str | None) -> None:
        self._status_manager.set_remote_image_candidate(url)

    def get_remote_image_candidate(self) -> str | None:
        return self._status_manager.get_remote_image_candidate()

    def get_visual_buffer_image_path(self) -> Path | None:
        return self._status_manager.get_visual_buffer_image_path()

    def clear_visual_buffer(self) -> None:
        self._status_manager.clear_visual_buffer()

    def set_pathetic_meter(self, value: int) -> None:
        self._status_manager.set_pathetic_meter(value)

    def set_terminal_link_online(self, online: bool) -> None:
        self._status_manager.set_terminal_link_online(online)

    def set_knowledge_sync_status(self, statuses: dict[str, str]) -> None:
        self._status_manager.set_knowledge_sync_status(statuses)

    def set_github_api_status(self, summary: str) -> None:
        self._status_manager.set_github_api_status(summary)

    def set_system_readiness(self, ok: bool, details: str = "") -> None:
        self._status_manager.set_system_readiness(ok, details)

    def set_llm_cache_metrics(self, latency_ms: int | None, tokens_cached: int | None) -> None:
        self._status_manager.set_llm_cache_metrics(latency_ms, tokens_cached)

    def set_context_token_counter(self, current_tokens: int, max_tokens: int) -> None:
        self._status_manager.set_context_token_counter(current_tokens, max_tokens)

    async def pulse_terminal_link(self) -> None:
        """Pulse the terminal link indicator to show data is flowing."""
        await self._status_manager.pulse_terminal_link()

    def apply_responsive_layout(self, width: int) -> None:
        self._view_manager.apply_responsive_layout(width)

    def toggle_upload_tree(self) -> None:
        """Toggle the file tree open/closed; the pane button strip stays visible."""
        self._view_manager.toggle_upload_tree()

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
        self._view_manager.set_active_view("chat")

    @on(Button.Pressed, "#tab_ops")
    def show_ops_view(self) -> None:
        self._view_manager.set_active_view("ops")

    def set_upload_root(self, root_path: Path) -> None:
        self._view_manager.set_upload_root(root_path)

    def set_loading(self, active: bool) -> None:
        self._view_manager.set_loading(active)

    def set_upload_progress(self, value: int, label: str = "UPLOAD") -> None:
        self._status_manager.set_upload_progress(value, label)

    def set_context_chip(self, filename: str, *, ingest_type: str) -> None:
        self._status_manager.set_context_chip(filename, ingest_type=ingest_type)

    def set_boot_status(self, text: str) -> None:
        self._status_manager.set_boot_status(text)

    def set_active_view(self, view: str) -> None:
        self._view_manager.set_active_view(view)

    @on(Button.Pressed, "#tab_gibson")
    def show_gibson_view(self) -> None:
        self._view_manager.show_gibson_view()
    # ── Gibson helpers ──────────────────────────────────────────────────────

    def set_gibson_results(self, snippets: list[dict]) -> None:
        """Populate grouped Gibson results with collapsible title groups."""
        self._gibson_manager.set_gibson_results(snippets)

    def _render_gibson_grouped_options(self) -> None:
        self._gibson_manager._render_gibson_grouped_options()

    def resolve_gibson_selection(self, option_index: int) -> dict | None:
        return self._gibson_manager.resolve_gibson_selection(option_index)

    def show_gibson_summary(self, markdown_text: str) -> None:
        self._gibson_manager.show_gibson_summary(markdown_text)

    def show_gibson_snippet(self, snippet: dict) -> None:
        self._gibson_manager.show_gibson_snippet(snippet)

    def set_gibson_loading(self, active: bool) -> None:
        self._gibson_manager.set_gibson_loading(active)

    def focus_gibson_input(self) -> None:
        self._gibson_manager.focus_gibson_input()

    def focus_chat_input(self) -> None:
        self.query_one("#command_input").focus()

    def _set_tab_states(self, tab_chat: Button, tab_ops: Button, tab_gibson: Button, *, active: str) -> None:
        self._view_manager._set_tab_states(tab_chat, tab_ops, tab_gibson, active=active)

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
        self._response_manager._update_response_markdown(text)

    @staticmethod
    def _normalize_response_markdown(text: str) -> str:
        return self._response_manager._normalize_response_markdown(text)

    @staticmethod
    def _is_probable_command_line(line: str) -> bool:
        return self._response_manager._is_probable_command_line(line)

    @staticmethod
    def _inject_copy_links(markdown_text: str) -> str:
        return self._response_manager._inject_copy_links(markdown_text)

    @on(Markdown.LinkClicked, "#response_markdown")
    def copy_response_code_block(self, event: Markdown.LinkClicked) -> None:
        self._response_manager.copy_response_code_block(event)

    @on(Button.Pressed, "#copy_response")
    def copy_response_text(self) -> None:
        self._response_manager.copy_response_text()

    @on(Button.Pressed, "#copy_code")
    def copy_latest_code_block(self) -> None:
        self._response_manager.copy_latest_code_block()

    async def stream_response(self, token: str, *, role: str = "assistant") -> None:
        """Append a token to the current streaming response display."""
        self._streaming_active = True
        container = self.chat_log()
        # Find or create streaming message widget
        msg = ChatMessage(role=role, message=token)
        container.mount(msg)
        container.scroll_end(animate=False)

    async def finish_stream(self, content: str) -> None:
        """Finalize streaming — replace temp widget with full response."""
        self._streaming_active = False
        container = self.chat_log()
        # Remove last widget (streaming temp) and append full response
        if container.children:
            last = list(container.children)[-1]
            container.remove(last)
        self.append_assistant(content)

    def cancel_stream(self) -> None:
        """Cancel the current streaming response."""
        self._streaming_active = False
        if self._streaming_cancel_event:
            self._streaming_cancel_event.set()
            self._streaming_cancel_event = None

    @on(Button.Pressed, "#cancel_stream")
    def on_cancel_stream(self) -> None:
        """Handle cancel button press during streaming."""
        self.cancel_stream()
        self.remove_class("streaming-active")
        self.notify("Streaming cancelled.", title="Zero Cool", severity="information")

    def show_streaming_ui(self) -> None:
        """Switch to streaming-optimized UI state."""
        self.add_class("streaming-active")
        self.query_one("#cancel_stream", Button).visible = True
        self.query_one("#analysis_loading").visible = True

    def hide_streaming_ui(self) -> None:
        """Return to normal UI state after streaming ends."""
        self.remove_class("streaming-active")
        self.query_one("#cancel_stream", Button).visible = False
        self.query_one("#analysis_loading").visible = False

    @property
    def streaming_active(self) -> bool:
        """Whether streaming is currently active."""
        return self._streaming_active

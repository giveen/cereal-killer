from __future__ import annotations

import asyncio
import re
import webbrowser
from pathlib import Path
from urllib.parse import quote, unquote

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Collapsible, DirectoryTree, LoadingIndicator, Markdown, Static

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


class MainDashboard(Screen[None]):
    def __init__(self) -> None:
        super().__init__()
        self._last_response_raw = ""
        self._last_response_markdown = ""
        self._active_view = "chat"

    def compose(self) -> ComposeResult:
        with Vertical(id="dashboard"):
            with Horizontal(id="view_tabs"):
                yield Button("Chat", id="tab_chat", variant="primary")
                yield Button("Ops", id="tab_ops", variant="default")
            with Horizontal(id="main_row"):
                with Vertical(id="explorer_pane"):
                    with Collapsible(title="📸 Screenshots", collapsed=False, id="upload_collapsible"):
                        screenshots_dir = Path("/screenshots") if Path("/screenshots").exists() else Path.cwd()
                        yield DirectoryTree(str(screenshots_dir), id="upload_tree")
                with Vertical(id="left_pane"):
                    yield Static("", id="boot_status_box")
                    yield VerticalScroll(id="chat_log")
                    yield Static("LATEST RESPONSE", id="response_title")
                    yield Markdown("_No response yet._", id="response_markdown", open_links=False)
                    with Horizontal(id="response_actions"):
                        yield Button("Copy Response", id="copy_response", variant="default")
                        yield Button("Copy Code", id="copy_code", variant="primary")
                        yield LoadingIndicator(id="analysis_loading")
                yield SidebarStatus()
            with Horizontal(id="bottom_row"):
                yield CommandInput()

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
        phase_widget.update(f"CURRENT PHASE\n{phase}")
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
        self.query_one("#active_tool", Static).update(f"ACTIVE TOOL\n{tool_name}")

    def set_visual_buffer(self, description: str, preview: str = "") -> None:
        body = description.strip() or "clipboard_obs.png"
        if preview.strip():
            body = f"{body}\n{preview}"
        self.query_one("#visual_buffer", Static).update(body)

    def clear_visual_buffer(self) -> None:
        self.query_one("#visual_buffer", Static).update("clipboard_obs.png\n(waiting for screenshot)")

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
        """Toggle the explorer pane visibility (U shortcut)."""
        explorer = self.query_one("#explorer_pane", Vertical)
        # Use CSS display instead of class to avoid media query conflicts
        if explorer.styles.display == "none":
            explorer.styles.display = "block"
        else:
            explorer.styles.display = "none"

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

    def set_loading(self, active: bool) -> None:
        indicator = self.query_one("#analysis_loading", LoadingIndicator)
        indicator.display = active

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
        tab_chat = self.query_one("#tab_chat", Button)
        tab_ops = self.query_one("#tab_ops", Button)

        self._active_view = "ops" if view == "ops" else "chat"
        if self._active_view == "ops":
            explorer.styles.display = "none"
            left_pane.styles.display = "none"
            sidebar.styles.display = "block"
            tab_chat.variant = "default"
            tab_ops.variant = "primary"
        else:
            explorer.styles.display = "block"
            left_pane.styles.display = "block"
            sidebar.styles.display = "none"
            tab_chat.variant = "primary"
            tab_ops.variant = "default"

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

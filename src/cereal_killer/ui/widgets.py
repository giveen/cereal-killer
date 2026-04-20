from __future__ import annotations

import asyncio
import re
import webbrowser

from textual.containers import Horizontal, Vertical
from textual import on
from textual.widgets import Button, Collapsible, Input, Markdown, Static


CODE_BLOCK_RE = re.compile(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", re.DOTALL)


class VerticalProgressBar(Static):
    def __init__(self, max_value: int = 10, value: int = 0, height: int = 10, id: str | None = None) -> None:
        super().__init__(id=id)
        self.max_value = max_value
        self.value = value
        self.height = height

    def on_mount(self) -> None:
        self.update(self._render_bar())

    def set_value(self, value: int) -> None:
        self.value = max(0, min(self.max_value, value))
        self.update(self._render_bar())

    def _render_bar(self) -> str:
        filled_rows = round((self.value / self.max_value) * self.height) if self.max_value else 0
        rows: list[str] = []
        for idx in range(self.height):
            is_filled = idx >= self.height - filled_rows
            rows.append("[red]█[/red]" if is_filled else "[grey37]░[/grey37]")
        return "\n".join(rows)


class PulsingEasyButton(Button):
    def __init__(self, label: str = "Easy Button", id: str | None = "easy_button") -> None:
        super().__init__(label, id=id)
        self.add_class("easy-pulse")

    def pulse_once(self) -> None:
        self.remove_class("easy-flash")
        self.app.call_after_refresh(lambda: self.add_class("easy-flash"))


class CommandInput(Input):
    def __init__(self) -> None:
        super().__init__(placeholder="Type /box or ask Zero Cool...", id="command_input")


class ChatMessage(Static):
    STREAM_LINE_THRESHOLD = 20
    STREAM_CHUNK_LINES = 10

    def __init__(self, role: str, message: str, id: str | None = None) -> None:
        super().__init__(id=id, classes=f"chat-message chat-{role}")
        self.role = role
        self.message = self._normalize_markdown(message)
        self._code_blocks = [block.strip() for block in CODE_BLOCK_RE.findall(self.message)]
        self._copy_payloads: dict[str, str] = {}

    def compose(self):
        label = {
            "user": "You",
            "assistant": "Zero Cool",
            "system": "System",
        }.get(self.role, self.role.title())
        yield Static(label, classes="chat-role")

        markdown = Markdown("", classes="chat-markdown", open_links=False)
        if hasattr(markdown, "code_dark_theme"):
            markdown.code_dark_theme = "dracula"
        if hasattr(markdown, "code_light_theme"):
            markdown.code_light_theme = "monokai"
        yield markdown

        if self._code_blocks:
            with Vertical(classes="code-actions"):
                for idx, code in enumerate(self._code_blocks, start=1):
                    button_id = f"copy_block_{idx}"
                    self._copy_payloads[button_id] = code
                    with Horizontal(classes="code-action-row"):
                        yield Static(f"Code Block {idx}", classes="code-action-label")
                        yield Button("Copy", id=button_id, classes="copy-code-button")

    async def on_mount(self) -> None:
        markdown = self.query_one(".chat-markdown", Markdown)
        line_count = len(self.message.splitlines())
        if line_count <= self.STREAM_LINE_THRESHOLD:
            markdown.update(self.message)
            return

        stream = Markdown.get_stream(markdown)
        try:
            lines = self.message.splitlines(keepends=True)
            for idx in range(0, len(lines), self.STREAM_CHUNK_LINES):
                chunk = "".join(lines[idx: idx + self.STREAM_CHUNK_LINES])
                await stream.write(chunk)
                parent = self.parent
                if parent is not None and hasattr(parent, "scroll_end"):
                    parent.scroll_end(animate=False)
        finally:
            await stream.stop()

    @on(Button.Pressed, ".copy-code-button")
    def copy_code_block(self, event: Button.Pressed) -> None:
        try:
            import pyperclip
        except Exception:
            pyperclip = None  # type: ignore[assignment]

        payload = self._copy_payloads.get(event.button.id or "", "")
        if not payload:
            self.app.notify("Code block not found", title="Copy", severity="warning")
            return

        copied = False
        if pyperclip is not None:
            try:
                pyperclip.copy(payload)
                copied = True
            except Exception:
                copied = False
        if not copied:
            self.app.copy_to_clipboard(payload)
        self.app.notify("Copied code block to clipboard", title="Copied", severity="information")

    @on(Markdown.LinkClicked, ".chat-markdown")
    def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        href = event.href.strip()
        if href.startswith(("http://", "https://")):
            webbrowser.open(href)

    @staticmethod
    def _normalize_markdown(text: str) -> str:
        content = (text or "").replace("\r\n", "\n").strip()
        if not content:
            return "_No content._"
        return content


class ThoughtBox(Collapsible):
    def __init__(self, *, collapsed: bool = False, id: str | None = "thought_box") -> None:
        super().__init__(title="LLM Thought Stream", collapsed=collapsed, id=id)

    def compose(self):
        yield Markdown("_No reasoning yet._", id="thought_markdown")

    def _markdown(self) -> Markdown:
        return self.query_one("#thought_markdown", Markdown)

    async def stream_thought(self, thought: str) -> None:
        content = thought.strip() or "(No <thought> output)"
        # Model outputs sometimes include literal "\\n" sequences; normalize them
        # so Markdown can wrap/display lines naturally.
        content = content.replace("\\n", "\n")
        buffer: list[str] = []
        for line in content.splitlines() or [content]:
            buffer.append(line)
            self._markdown().update("\n".join(buffer))
            await asyncio.sleep(0.01)


class SidebarStatus(Vertical):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(id="intel_sidebar", **kwargs)
        self._terminal_link_online = True

    def compose(self):
        from .widgets_findings import FindingsWidget
        
        yield Static("[green]●[/green] TERMINAL LINK: ONLINE", id="terminal_link_status")
        yield Static("CURRENT PHASE\n[IDLE]", id="current_phase")
        yield Static("ACTIVE TOOL\nIdle", id="active_tool")
        yield Static("KNOWLEDGE SYNC", id="knowledge_sync_label")
        yield Static(
            "ippsec: never\n"
            "gtfobins: never\n"
            "lolbas: never\n"
            "hacktricks: never\n"
            "payloads: never",
            id="knowledge_sync_status",
        )
        yield Static("VISUAL BUFFER", id="visual_buffer_label")
        yield Static("clipboard_obs.png\n(waiting for screenshot)", id="visual_buffer")
        yield Button("Clear Buffer", id="clear_visual_buffer", variant="warning")
        yield Static("PATHETIC METER", id="pathetic_meter")
        yield VerticalProgressBar(max_value=10, value=0, height=10, id="pathetic_meter_bar")
        yield Static("0/10", id="pathetic_meter_value")
        yield FindingsWidget(id="findings_widget")
        yield PulsingEasyButton()

    def set_terminal_link_status(self, online: bool) -> None:
        """Update terminal link status. If online=True, show green ONLINE. If False, show red OFFLINE."""
        self._terminal_link_online = online
        status_widget = self.query_one("#terminal_link_status", Static)
        if online:
            status_widget.update("[green]●[/green] TERMINAL LINK: ONLINE")
        else:
            status_widget.update("[red]●[/red] TERMINAL LINK: OFFLINE")

    async def pulse_terminal_link(self) -> None:
        """Briefly pulse the terminal link indicator to show data is flowing."""
        status_widget = self.query_one("#terminal_link_status", Static)

        # Pulse effect: flash to white and back
        for _ in range(2):
            status_widget.update("[yellow]●[/yellow] TERMINAL LINK: DATA")
            await asyncio.sleep(0.1)
            status_widget.update("[green]●[/green] TERMINAL LINK: ONLINE" if self._terminal_link_online else "[red]●[/red] TERMINAL LINK: OFFLINE")
            await asyncio.sleep(0.1)

    def set_knowledge_sync_status(self, statuses: dict[str, str]) -> None:
        lines = []
        for name in ("ippsec", "gtfobins", "lolbas", "hacktricks", "payloads"):
            value = statuses.get(name, "never")
            lines.append(f"{name}: {value}")
        self.query_one("#knowledge_sync_status", Static).update("\n".join(lines))

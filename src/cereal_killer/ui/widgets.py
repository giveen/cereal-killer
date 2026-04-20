from __future__ import annotations

import asyncio
import re
import webbrowser
from pathlib import Path

from textual.containers import Horizontal, Vertical
from textual import on
from textual.widgets import Button, Collapsible, Input, Markdown, Static

try:
    from PIL import Image
except Exception:  # pragma: no cover - allow non-vision paths to import
    Image = None  # type: ignore[assignment]

try:
    from textual_imageview.viewer import ImageViewer
except Exception:  # pragma: no cover - keep UI functional when dependency is absent
    ImageViewer = None  # type: ignore[assignment]


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
    BINDINGS = [("ctrl+v", "paste_clipboard", "Paste")]

    def __init__(self) -> None:
        super().__init__(placeholder="Type /box or ask Zero Cool...", id="command_input")

    def action_paste_clipboard(self) -> None:
        """Read system clipboard and insert text at current cursor position."""
        from mentor.utils.clipboard import read_text
        text = read_text()
        if not text:
            return
        # Strip newlines so a multi-line clipboard entry becomes one line.
        cleaned = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()
        if not cleaned:
            return
        # Insert at cursor: splice into current value.
        pos = self.cursor_position
        self.value = self.value[:pos] + cleaned + self.value[pos:]
        self.cursor_position = pos + len(cleaned)


class ChatMessage(Static):
    STREAM_LINE_THRESHOLD = 20
    STREAM_CHUNK_LINES = 10

    def __init__(self, role: str, message: str, id: str | None = None) -> None:
        super().__init__(id=id, classes=f"chat-message chat-{role}")
        self.role = role
        self.message = self._normalize_markdown(message)
        self._segments = self._split_segments(self.message)
        self._code_blocks = [chunk.strip() for kind, chunk in self._segments if kind == "code"]
        self._copy_payloads: dict[str, str] = {}

    def compose(self):
        label = {
            "user": "You",
            "assistant": "Zero Cool",
            "system": "System",
        }.get(self.role, self.role.title())
        yield Static(label, classes="chat-role")

        if not self._code_blocks:
            markdown = Markdown("", classes="chat-markdown", open_links=False)
            self._apply_code_theme(markdown)
            yield markdown
            return

        code_idx = 0
        for kind, chunk in self._segments:
            if kind == "text":
                if not chunk.strip():
                    continue
                text_md = Markdown(chunk, classes="chat-markdown", open_links=False)
                self._apply_code_theme(text_md)
                yield text_md
                continue

            code_idx += 1
            code = chunk.strip()
            if not code:
                continue
            button_id = f"copy_block_{code_idx}"
            self._copy_payloads[button_id] = code
            with Horizontal(classes="code-block-row"):
                code_md = Markdown(f"```bash\n{code}\n```", classes="chat-code-block", open_links=False)
                self._apply_code_theme(code_md)
                yield code_md
                yield Button("Copy", id=button_id, classes="copy-code-button")

    async def on_mount(self) -> None:
        if self._code_blocks:
            return

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

    @staticmethod
    def _split_segments(markdown_text: str) -> list[tuple[str, str]]:
        segments: list[tuple[str, str]] = []
        last = 0
        for match in CODE_BLOCK_RE.finditer(markdown_text):
            if match.start() > last:
                segments.append(("text", markdown_text[last:match.start()]))
            segments.append(("code", match.group(1)))
            last = match.end()
        if last < len(markdown_text):
            segments.append(("text", markdown_text[last:]))
        if not segments:
            segments.append(("text", markdown_text))
        return segments

    @staticmethod
    def _apply_code_theme(markdown_widget: Markdown) -> None:
        if hasattr(markdown_widget, "code_dark_theme"):
            markdown_widget.code_dark_theme = "dracula"
        if hasattr(markdown_widget, "code_light_theme"):
            markdown_widget.code_light_theme = "monokai"


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


class VisualBuffer(Static):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(id="visual_buffer_widget", **kwargs)
        self._active_image_path: Path | None = None
        self._remote_candidate_url: str | None = None

    def compose(self):
        with Vertical(id="visual_buffer_shell"):
            yield Static("No active frame.", id="visual_buffer_status")
            yield Static("SCANLINE FILTER: STANDBY", id="visual_scanline")
            with Vertical(id="visual_image_host"):
                yield Static("Waiting for screenshot upload or clipboard capture...", id="visual_image_placeholder")
            with Horizontal(id="visual_buffer_actions"):
                yield Button("[VIEW IMAGE]", id="visual_view_remote", variant="default")
                yield Button("Send to Zero Cool", id="visual_send_zero_cool", variant="primary")
                yield Button("Clear Buffer", id="clear_visual_buffer", variant="warning")

    def on_mount(self) -> None:
        self.query_one("#visual_view_remote", Button).styles.display = "none"
        self.query_one("#visual_send_zero_cool", Button).styles.display = "none"

    def set_remote_candidate(self, url: str | None) -> None:
        cleaned = (url or "").strip()
        self._remote_candidate_url = cleaned or None
        view_btn = self.query_one("#visual_view_remote", Button)
        if self._remote_candidate_url:
            view_btn.styles.display = "block"
            view_btn.label = "[VIEW IMAGE]"
            self.query_one("#visual_buffer_status", Static).update("Remote diagram detected. Press [VIEW IMAGE].")
        else:
            view_btn.styles.display = "none"

    def consume_remote_candidate(self) -> str | None:
        return self._remote_candidate_url

    def active_image_path(self) -> Path | None:
        return self._active_image_path

    def set_image_from_path(self, image_path: Path, *, source: str, preview: str = "") -> None:
        resolved = image_path.expanduser().resolve()
        if Image is None:
            self.query_one("#visual_buffer_status", Static).update(f"Pillow unavailable: {resolved.name}")
            self._active_image_path = resolved
            self.query_one("#visual_send_zero_cool", Button).styles.display = "block"
            return

        try:
            with Image.open(resolved) as raw:
                image = raw.convert("RGB").copy()
        except Exception as exc:
            self.query_one("#visual_buffer_status", Static).update(f"Failed to load image: {exc}")
            return

        host = self.query_one("#visual_image_host", Vertical)
        host.remove_children()

        if ImageViewer is not None:
            host.mount(ImageViewer(image))
        else:
            host.mount(Static("textual-imageview unavailable", id="visual_image_placeholder"))

        status = f"ACTIVE FRAME: {resolved.name} ({source})"
        if preview.strip():
            status = f"{status}\n{preview}"
        self.query_one("#visual_buffer_status", Static).update(status)
        self.query_one("#visual_send_zero_cool", Button).styles.display = "block"
        self._active_image_path = resolved

    def clear(self) -> None:
        self._active_image_path = None
        host = self.query_one("#visual_image_host", Vertical)
        host.remove_children()
        host.mount(Static("Waiting for screenshot upload or clipboard capture...", id="visual_image_placeholder"))
        self.query_one("#visual_buffer_status", Static).update("No active frame.")
        self.query_one("#visual_send_zero_cool", Button).styles.display = "none"


class SidebarStatus(Vertical):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(id="intel_sidebar", **kwargs)
        self._terminal_link_online = True

    def compose(self):
        from .widgets_findings import FindingsWidget

        with Horizontal(id="ops_status_bar"):
            yield Static("LINK: [green]ONLINE[/green]", id="terminal_link_status", classes="ops-stat")
            yield Static("|", classes="ops-sep")
            yield Static("PHASE: [IDLE]", id="current_phase", classes="ops-stat")
            yield Static("|", classes="ops-sep")
            yield Static("TOOL: Idle", id="active_tool", classes="ops-stat")
            yield Static("|", classes="ops-sep")
            yield Button("SETUP: CHECKING", id="system_readiness_tag", variant="default")
        with Horizontal(id="ops_bottom_row"):
            yield Static("", id="ops_spacer")
            with Vertical(id="ops_media_column"):
                with Collapsible(title="Media Drawer", collapsed=False, id="visual_buffer_collapsible"):
                    yield VisualBuffer()
                yield Static(
                    "[dim]KNOWLEDGE SYNC[/dim]\n"
                    "ippsec: [bold]never[/bold]\n"
                    "gtfobins: [bold]never[/bold]\n"
                    "lolbas: [bold]never[/bold]\n"
                    "hacktricks: [bold]never[/bold]\n"
                    "payloads: [bold]never[/bold]",
                    id="knowledge_sync_status",
                )
                yield Static(
                    "[dim]SYSTEM HEALTH[/dim]\n"
                    "GITHUB API: unknown",
                    id="system_health_status",
                )

        # Retain compatibility targets for existing app update methods,
        # but keep them hidden in the simplified Ops layout.
        with Vertical(id="ops_hidden_compat"):
            yield Static("UPLOAD PIPELINE", id="upload_progress_label")
            yield VerticalProgressBar(max_value=100, value=0, height=4, id="upload_progress_bar")
            yield Static("0%", id="upload_progress_value")
            yield Static("PATHETIC METER", id="pathetic_meter")
            yield VerticalProgressBar(max_value=10, value=0, height=10, id="pathetic_meter_bar")
            yield Static("0/10", id="pathetic_meter_value")
            yield FindingsWidget(id="findings_widget")
            yield PulsingEasyButton()

    def on_mount(self) -> None:
        self.query_one("#visual_buffer_collapsible", Collapsible).styles.display = "none"

    def set_terminal_link_status(self, online: bool) -> None:
        """Update terminal link status. If online=True, show green ONLINE. If False, show red OFFLINE."""
        self._terminal_link_online = online
        status_widget = self.query_one("#terminal_link_status", Static)
        if online:
            status_widget.update("LINK: [green]ONLINE[/green]")
        else:
            status_widget.update("LINK: [red]OFFLINE[/red]")

    async def pulse_terminal_link(self) -> None:
        """Briefly pulse the terminal link indicator to show data is flowing."""
        status_widget = self.query_one("#terminal_link_status", Static)

        # Pulse effect: flash to white and back
        for _ in range(2):
            status_widget.update("LINK: [yellow]DATA[/yellow]")
            await asyncio.sleep(0.1)
            status_widget.update("LINK: [green]ONLINE[/green]" if self._terminal_link_online else "LINK: [red]OFFLINE[/red]")
            await asyncio.sleep(0.1)

    def set_knowledge_sync_status(self, statuses: dict[str, str]) -> None:
        lines = ["[dim]KNOWLEDGE SYNC[/dim]"]
        for name in ("ippsec", "gtfobins", "lolbas", "hacktricks", "payloads"):
            value = statuses.get(name, "never")
            lines.append(f"{name}: [bold]{value}[/bold]")
        self.query_one("#knowledge_sync_status", Static).update("\n".join(lines))

    def set_github_api_status(self, summary: str) -> None:
        line = (summary or "unknown").strip()
        self.query_one("#system_health_status", Static).update(
            "[dim]SYSTEM HEALTH[/dim]\n"
            f"GITHUB API: {line}"
        )

    def set_system_readiness(self, ok: bool, details: str = "") -> None:
        status = self.query_one("#system_readiness_tag", Button)
        if ok:
            status.label = "SETUP: ✓ READY"
            status.disabled = True
            return

        status.label = "SETUP: ⚠ SETUP INCOMPLETE"
        status.disabled = False

        extra = f" ({details})" if details else ""
        self.query_one("#system_health_status", Static).update(
            "[dim]SYSTEM HEALTH[/dim]\n"
            f"GITHUB API: unknown\n"
            f"Setup missing{extra}\n"
            "Guide: docs/setup/README.md"
        )

    def set_visual_buffer_image(self, image_path: Path, *, source: str, preview: str = "") -> None:
        self.query_one("#visual_buffer_collapsible", Collapsible).styles.display = "block"
        buffer_widget = self.query_one("#visual_buffer_widget", VisualBuffer)
        buffer_widget.set_image_from_path(image_path, source=source, preview=preview)

    def set_remote_image_candidate(self, url: str | None) -> None:
        collapsible = self.query_one("#visual_buffer_collapsible", Collapsible)
        buffer_widget = self.query_one("#visual_buffer_widget", VisualBuffer)
        buffer_widget.set_remote_candidate(url)
        if url:
            collapsible.styles.display = "block"
        elif buffer_widget.active_image_path() is None:
            collapsible.styles.display = "none"

    def get_remote_image_candidate(self) -> str | None:
        return self.query_one("#visual_buffer_widget", VisualBuffer).consume_remote_candidate()

    def get_visual_buffer_image_path(self) -> Path | None:
        return self.query_one("#visual_buffer_widget", VisualBuffer).active_image_path()

    def clear_visual_buffer(self) -> None:
        collapsible = self.query_one("#visual_buffer_collapsible", Collapsible)
        buffer_widget = self.query_one("#visual_buffer_widget", VisualBuffer)
        buffer_widget.clear()
        if not buffer_widget.consume_remote_candidate():
            collapsible.styles.display = "none"

    def set_upload_progress(self, value: int, label: str = "UPLOAD") -> None:
        progress = max(0, min(100, value))
        self.query_one("#upload_progress_label", Static).update(f"{label}\n{progress}%")
        self.query_one("#upload_progress_bar", VerticalProgressBar).set_value(progress)
        self.query_one("#upload_progress_value", Static).update(f"{progress}%")

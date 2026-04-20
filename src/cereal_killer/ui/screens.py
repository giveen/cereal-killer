from __future__ import annotations

import asyncio
import re
from urllib.parse import quote, unquote

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Markdown, RichLog, Static

from mentor.utils.clipboard import copy_text

from .widgets import CommandInput, SidebarStatus, VerticalProgressBar


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
            yield Markdown("", id="solution_markdown")
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

    def compose(self) -> ComposeResult:
        with Vertical(id="dashboard"):
            with Horizontal(id="main_row"):
                with Vertical(id="left_pane"):
                    yield RichLog(id="chat_log", markup=True, wrap=True, highlight=True)
                    yield Static("LATEST RESPONSE", id="response_title")
                    yield Markdown("_No response yet._", id="response_markdown")
                    with Horizontal(id="response_actions"):
                        yield Button("Copy Response", id="copy_response", variant="default")
                        yield Button("Copy Code", id="copy_code", variant="primary")
                yield SidebarStatus()
            with Horizontal(id="bottom_row"):
                yield CommandInput()

    def chat_log(self) -> RichLog:
        return self.query_one("#chat_log", RichLog)

    async def stream_thought(self, thought: str) -> None:
        """Thought panel removed; keep async call sites stable."""
        return

    def append_user(self, text: str) -> None:
        self.chat_log().write(f"[cyan]You>[/cyan] {text}")

    def append_assistant(self, text: str) -> None:
        self.chat_log().write(f"[magenta]Zero Cool>[/magenta] {text}")
        self._update_response_markdown(text)

    def append_system(self, text: str, *, style: str = "yellow") -> None:
        self.chat_log().write(f"[{style}]System>[/{style}] {text}")

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

    def apply_responsive_layout(self, width: int) -> None:
        sidebar = self.query_one("#intel_sidebar", Vertical)
        if width < 120:
            sidebar.add_class("hidden")
        else:
            sidebar.remove_class("hidden")

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

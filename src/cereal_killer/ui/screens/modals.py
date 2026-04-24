"""Modal screens for the cereal-killer TUI."""
from __future__ import annotations

import asyncio
import re
import webbrowser
from urllib.parse import quote, unquote

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Markdown, Static

CODE_BLOCK_RE = re.compile(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", re.DOTALL)


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
        from mentor.utils.clipboard import copy_text

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
            yield Static(
                "SYSTEM CRITICAL: INFRASTRUCTURE OFFLINE. SEE DOCS/SETUP.",
                id="decryption_text",
            )
            msg = "Fix setup blockers before running workflows."
            if self.detail:
                msg += f"\n\nDetected hard failures: {self.detail}"
            yield Markdown(msg, id="solution_markdown", open_links=False)
            yield Button("Acknowledge", id="solution_close", variant="primary")

    @on(Button.Pressed, "#solution_close")
    def close_modal(self) -> None:
        self.dismiss(None)

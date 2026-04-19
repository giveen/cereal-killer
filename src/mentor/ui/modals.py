from __future__ import annotations

import asyncio
import re
from urllib.parse import quote, unquote

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Markdown, Static

from mentor.utils.clipboard import copy_text


CODE_BLOCK_RE = re.compile(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", re.DOTALL)


class SolutionModal(ModalScreen[None]):
    """Fullscreen markdown modal for machine-specific walkthrough material."""

    CSS = """
    SolutionModal {
        align: center middle;
    }
    #solution-shell {
        width: 100%;
        height: 100%;
        background: $surface;
        padding: 1;
    }
    #solution-markdown {
        width: 1fr;
        height: 1fr;
        border: round $primary;
        padding: 1;
    }
    #solution-close {
        dock: bottom;
        margin-top: 1;
    }
    """

    def __init__(self, markdown_text: str) -> None:
        super().__init__()
        self.markdown_text = markdown_text

    def compose(self) -> ComposeResult:
        with Vertical(id="solution-shell"):
            yield Static("Decrypting walkthrough payload...", id="decryption-text")
            yield Markdown("", id="solution-markdown")
            yield Button("Close", id="solution-close", variant="primary")

    async def on_mount(self) -> None:
        animation_widget = self.query_one("#decryption-text", Static)
        markdown_widget = self.query_one("#solution-markdown", Markdown)

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

    @on(Markdown.LinkClicked, "#solution-markdown")
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

    @on(Button.Pressed, "#solution-close")
    def close_modal(self) -> None:
        self.dismiss(None)

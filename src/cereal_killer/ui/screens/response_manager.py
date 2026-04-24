"""Response manager - handles response markdown normalization and copy links."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from textual.widgets import Markdown

if TYPE_CHECKING:
    from .dashboard import MainDashboard

# Import the regex patterns from screens module
from ..screens import CODE_BLOCK_RE, PROBABLE_COMMAND_RE


class ResponseManager:
    """Manages response markdown normalization and copy links."""

    def __init__(self, dashboard: MainDashboard) -> None:
        self._dashboard = dashboard

    def _update_response_markdown(self, text: str) -> None:
        """Normalize and update response markdown."""
        self._dashboard._last_response_raw = text or ""
        normalized = self._normalize_response_markdown(self._dashboard._last_response_raw)
        self._dashboard._last_response_markdown = normalized
        response_markdown = self._dashboard.query_one("#response_markdown", Markdown)
        response_markdown.update(self._inject_copy_links(normalized))

    @staticmethod
    def _normalize_response_markdown(text: str) -> str:
        """Normalize markdown with code detection."""
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
            if ResponseManager._is_probable_command_line(stripped):
                command_line = stripped[2:] if stripped.startswith("$ ") else stripped
                code_block.append(command_line)
                continue
            flush_code()
            output.append(line)

        flush_code()
        return "\n".join(output).strip() or content

    @staticmethod
    def _is_probable_command_line(line: str) -> bool:
        """Detect command lines."""
        if not line:
            return False
        if line.startswith(("http://", "https://")):
            return False
        return bool(PROBABLE_COMMAND_RE.match(line))

    @staticmethod
    def _inject_copy_links(markdown_text: str) -> str:
        """Add copy links to code blocks."""
        from urllib.parse import quote

        def replacer(match: re.Match[str]) -> str:
            body = match.group(1).strip()
            token = quote(body)
            return f"[COPY](copy://{token})\n```\n{body}\n```"

        return CODE_BLOCK_RE.sub(replacer, markdown_text)

    def copy_response_code_block(self, event: Markdown.LinkClicked) -> None:
        """Copy code block from link."""
        href = event.href.strip()
        if href.startswith(("http://", "https://")):
            import webbrowser
            webbrowser.open(href)
            return
        if not href.startswith("copy://"):
            return
        from urllib.parse import unquote
        encoded = href.replace("copy://", "", 1)
        command = unquote(encoded)
        from mentor.utils.clipboard import copy_text
        copy_text(command, fallback=self._dashboard.app.copy_to_clipboard)
        self._dashboard.app.notify("Copied code block to clipboard", title="Copied", severity="information")

    def copy_response_text(self) -> None:
        """Copy full response."""
        payload = self._dashboard._last_response_raw.strip()
        if not payload:
            self._dashboard.app.notify("No response to copy yet", title="Copy Response", severity="warning")
            return
        from mentor.utils.clipboard import copy_text
        copy_text(payload, fallback=self._dashboard.app.copy_to_clipboard)
        self._dashboard.app.notify("Copied full response to clipboard", title="Copied", severity="information")

    def copy_latest_code_block(self) -> None:
        """Copy latest code block."""
        matches = CODE_BLOCK_RE.findall(self._dashboard._last_response_markdown)
        if not matches:
            self._dashboard.app.notify("No code block found in latest response", title="Copy Code", severity="warning")
            return
        from mentor.utils.clipboard import copy_text
        copy_text(matches[-1].strip(), fallback=self._dashboard.app.copy_to_clipboard)
        self._dashboard.app.notify("Copied latest code block", title="Copied", severity="information")

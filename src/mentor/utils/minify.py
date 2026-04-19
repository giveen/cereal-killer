"""Output minification helpers for terminal tools."""

from __future__ import annotations

import re

_NMAP_NOISE = (
    re.compile(r"^Starting Nmap.*$", re.IGNORECASE),
    re.compile(r"^Nmap scan report for .*$", re.IGNORECASE),
    re.compile(r"^Host is up.*$", re.IGNORECASE),
    re.compile(r"^Nmap done:.*$", re.IGNORECASE),
)
_GOBUSTER_NOISE = (
    re.compile(r"^===============================================================\s*$"),
    re.compile(r"^\[INFO\].*$", re.IGNORECASE),
    re.compile(r"^Progress: .*$", re.IGNORECASE),
)
_SMBCLIENT_NOISE = (
    re.compile(r'^Try "help" to get a list of possible commands\.$', re.IGNORECASE),
    re.compile(r"^Anonymous login successful.*$", re.IGNORECASE),
)


def _clean_lines(text: str, patterns: tuple[re.Pattern[str], ...]) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        if any(p.search(line) for p in patterns):
            continue
        if line.strip():
            kept.append(line.rstrip())
    return "\n".join(kept)


def minify_tool_output(tool: str, output: str, *, max_chars: int = 4000) -> str:
    """Shrink noisy CLI output while preserving useful findings."""
    tool_name = (tool or "").lower()
    text = output or ""
    if tool_name == "nmap":
        text = _clean_lines(text, _NMAP_NOISE)
    elif tool_name == "gobuster":
        text = _clean_lines(text, _GOBUSTER_NOISE)
    elif tool_name == "smbclient":
        text = _clean_lines(text, _SMBCLIENT_NOISE)
    else:
        text = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 12].rstrip() + "\n...[truncated]"


__all__ = ["minify_tool_output"]


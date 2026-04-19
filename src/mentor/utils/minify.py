from __future__ import annotations

import re


_WHITESPACE_RE = re.compile(r"\s+")


def minify_tool_output(output: str, command: str | None = None, max_lines: int = 80) -> str:
    """Reduce noisy command output before it is sent to the LLM.

    The minifier keeps high-signal lines and strips banners/headers that waste tokens.
    """
    if not output:
        return ""

    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    command_name = _first_command_word(command or "")

    if command_name == "nmap":
        lines = _minify_nmap(lines)
    elif command_name in {"gobuster", "ffuf"}:
        lines = _minify_bruteforce(lines)
    elif command_name in {"smbclient", "smbmap", "enum4linux"}:
        lines = _minify_smb(lines)

    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in lines if line.strip()]
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} lines omitted)"]
    return "\n".join(lines)


def _first_command_word(command: str) -> str:
    parts = command.strip().split()
    if not parts:
        return ""
    if parts[0] in {"sudo", "doas"} and len(parts) > 1:
        return parts[1].lower()
    return parts[0].lower()


def _minify_nmap(lines: list[str]) -> list[str]:
    keep: list[str] = []
    for line in lines:
        lowered = line.lower()
        if lowered.startswith("starting nmap"):
            continue
        if lowered.startswith("nmap done"):
            continue
        if lowered.startswith("service detection performed"):
            continue
        if lowered.startswith("read data files from"):
            continue
        if "host is up" in lowered or "/tcp" in lowered or "/udp" in lowered or "nmap scan report for" in lowered:
            keep.append(line)
    return keep or lines


def _minify_bruteforce(lines: list[str]) -> list[str]:
    keep: list[str] = []
    for line in lines:
        lowered = line.lower()
        if lowered.startswith("[+") and "threads" in lowered:
            continue
        if lowered.startswith("==============================================================="):
            continue
        if "status: 301" in lowered or "status: 302" in lowered or "status: 200" in lowered:
            keep.append(line)
        elif lowered.startswith("/"):
            keep.append(line)
    return keep or lines


def _minify_smb(lines: list[str]) -> list[str]:
    keep: list[str] = []
    for line in lines:
        lowered = line.lower()
        if lowered.startswith("protocol negotiation failed"):
            keep.append(line)
            continue
        if any(token in lowered for token in ("disk", "ipc$", "sharename", "permissions", "nt_status")):
            keep.append(line)
    return keep or lines

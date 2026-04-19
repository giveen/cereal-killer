from __future__ import annotations

import asyncio
import os
import platform
from collections.abc import AsyncIterator
from pathlib import Path

from watchfiles import Change, awatch


def candidate_history_files() -> list[Path]:
    home = Path.home()
    system = platform.system().lower()
    files = [home / ".zsh_history"]
    if "darwin" in system or "linux" in system:
        files.extend([home / ".bash_history", home / ".local/share/fish/fish_history"])
    elif "windows" in system:
        files.extend([
            home / "AppData/Roaming/Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt",
        ])
    return [p for p in files if p.exists()]


def parse_history_lines(raw: str) -> list[str]:
    commands: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(": ") and ";" in stripped:
            commands.append(stripped.split(";", 1)[1].strip())
        else:
            commands.append(stripped)
    return commands


def filter_context_commands(commands: list[str], cwd: str, limit: int = 50) -> list[str]:
    cwd = cwd.strip()
    cwd_name = Path(cwd).name
    session_markers = [os.getenv("TMUX_PANE", ""), os.getenv("STY", ""), str(os.getppid())]
    filtered = [
        cmd
        for cmd in commands
        if cwd in cmd or (cwd_name and cwd_name in cmd) or any(marker and marker in cmd for marker in session_markers)
    ]
    if not filtered:
        filtered = commands
    return filtered[-limit:]


async def observe_history(cwd: str) -> AsyncIterator[list[str]]:
    history_files = candidate_history_files()
    if not history_files:
        while True:
            await asyncio.sleep(5)
            yield []

    target = history_files[0]
    async for changes in awatch(target):
        relevant = any(change in {Change.modified, Change.added} and Path(path) == target for change, path in changes)
        if not relevant:
            continue
        try:
            text = target.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        yield filter_context_commands(parse_history_lines(text), cwd)

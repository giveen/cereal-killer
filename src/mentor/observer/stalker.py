"""Asynchronous shell history observer."""

from __future__ import annotations

import hashlib
import os
import platform
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque

try:
    from watchfiles import awatch
except Exception:  # pragma: no cover
    awatch = None


_ZSH_HISTORY_PREFIX = re.compile(r"^:\s*\d+:\d+;")
_DEFAULT_TECH_TOOLS = {
    "amass",
    "curl",
    "dig",
    "enum4linux",
    "ffuf",
    "gobuster",
    "hydra",
    "john",
    "ldapsearch",
    "masscan",
    "msfconsole",
    "nc",
    "netcat",
    "nikto",
    "nmap",
    "nslookup",
    "onesixtyone",
    "ping",
    "python",
    "python3",
    "redis-cli",
    "smbclient",
    "smbmap",
    "snmpwalk",
    "sqlmap",
    "ssh",
    "wfuzz",
    "whatweb",
}


@dataclass(slots=True)
class HistoryCommand:
    command: str
    cwd: str


class HistoryStalker:
    """Watches shell history and forwards relevant commands to the Brain."""

    def __init__(
        self,
        brain: Any,
        history_file: str | Path | None = None,
        technical_tools: set[str] | None = None,
        context_limit: int = 50,
        on_command: Callable[[HistoryCommand], Any] | None = None,
    ) -> None:
        self.brain = brain
        self.history_path = Path(history_file) if history_file else self._detect_history_file()
        self.technical_tools = technical_tools or set(_DEFAULT_TECH_TOOLS)
        self.context_limit = context_limit
        self.on_command = on_command
        self._known_hashes: set[str] = set()
        self._recent_by_cwd: dict[str, Deque[str]] = {}
        self._offset = 0

    @staticmethod
    def _detect_history_file() -> Path:
        env_hist = os.environ.get("HISTFILE")
        if env_hist:
            return Path(env_hist).expanduser()
        home = Path.home()
        if platform.system() == "Windows":
            app_data = os.environ.get("APPDATA")
            if app_data:
                return Path(app_data) / "Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt"
            return home / "AppData/Roaming/Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt"
        for candidate in (home / ".zsh_history", home / ".bash_history", home / ".history"):
            if candidate.exists():
                return candidate
        return home / ".zsh_history"

    @staticmethod
    def _normalize(raw: str) -> str:
        clean = raw.strip()
        clean = _ZSH_HISTORY_PREFIX.sub("", clean).strip()
        return clean

    @staticmethod
    def _extract_tool(command: str) -> str:
        token = command.split(maxsplit=1)[0] if command else ""
        if "/" in token:
            token = token.rsplit("/", maxsplit=1)[-1]
        return token.lower()

    @staticmethod
    def _hash(command: str) -> str:
        return hashlib.sha256(command.encode("utf-8")).hexdigest()

    def _is_technical_tool(self, command: str) -> bool:
        return self._extract_tool(command) in self.technical_tools

    def _context_for(self, cwd: str) -> list[str]:
        return list(self._recent_by_cwd.get(cwd, deque(maxlen=self.context_limit)))

    def _track(self, command: str, cwd: str) -> None:
        bucket = self._recent_by_cwd.setdefault(cwd, deque(maxlen=self.context_limit))
        bucket.append(command)

    async def _dispatch(self, command: str, cwd: str) -> None:
        if self.on_command is not None:
            self.on_command(HistoryCommand(command=command, cwd=cwd))
        context = self._context_for(cwd)
        process_fn: Callable[..., Awaitable[Any]] | None = getattr(self.brain, "process_command", None)
        if process_fn is not None:
            await process_fn(command=command, context=context, cwd=cwd)
            return
        ask_fn: Callable[..., Awaitable[Any]] | None = getattr(self.brain, "ask", None)
        if ask_fn is not None:
            prompt = f"Observed command in {cwd}:\n{command}\n\nRecent context:\n" + "\n".join(context[-self.context_limit :])
            await ask_fn(prompt=prompt)

    async def _handle_lines(self, lines: list[str]) -> None:
        cwd = os.getcwd()
        for line in lines:
            command = self._normalize(line)
            if not command:
                continue
            digest = self._hash(command)
            if digest in self._known_hashes:
                continue
            self._known_hashes.add(digest)
            self._track(command, cwd)
            if self._is_technical_tool(command):
                await self._dispatch(command, cwd)

    def _read_new_lines(self) -> list[str]:
        if not self.history_path.exists():
            return []
        with self.history_path.open("r", encoding="utf-8", errors="ignore") as f:
            f.seek(self._offset)
            lines = f.readlines()
            self._offset = f.tell()
            return lines

    async def run(self) -> None:
        if awatch is None:
            raise RuntimeError("watchfiles is required to run HistoryStalker.")
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self._offset = self.history_path.stat().st_size if self.history_path.exists() else 0
        async for changes in awatch(self.history_path.parent):
            if any(Path(change[1]) == self.history_path for change in changes):
                await self._handle_lines(self._read_new_lines())

    async def run_once(self) -> None:
        """Convenience method for tests."""
        await self._handle_lines(self._read_new_lines())


__all__ = ["HistoryCommand", "HistoryStalker"]

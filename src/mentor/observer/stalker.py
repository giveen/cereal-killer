from __future__ import annotations

import asyncio
import hashlib
import platform
import re
import shlex
import time as _time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

try:
    from watchfiles import Change, awatch
except ImportError:  # pragma: no cover - enables import-only unit tests in minimal envs
    Change = None  # type: ignore[assignment]
    awatch = None  # type: ignore[assignment]

from cereal_killer.config import HISTORY_CONTEXT_LIMIT


TECHNICAL_TOOLS = {
    "nmap",
    "gobuster",
    "smbclient",
    "smbmap",
    "enum4linux",
    "ffuf",
    "nikto",
    "wfuzz",
    "dirsearch",
    "sqlmap",
    "hydra",
    "netexec",
    "crackmapexec",
    "msfconsole",
    "tcpdump",
    "john",
    "hashcat",
}


@dataclass(slots=True)
class HistoryEvent:
    command: str
    context_commands: list[str]
    trigger_brain: bool
    feedback_signal: str | None = None
    feedback_line: str | None = None
    json_hint: str | None = None
    # Populated when the user `cd`s into a directory whose name looks like an
    # HTB machine so the UI can auto-trigger /box <name>.
    cd_target: str | None = None


def candidate_history_files() -> list[Path]:
    home = Path.home()
    system = platform.system().lower()
    candidates = [home / ".zsh_history"]
    if "darwin" in system or "linux" in system:
        candidates.extend([home / ".bash_history", home / ".local/share/fish/fish_history"])
    elif "windows" in system:
        candidates.append(home / "AppData/Roaming/Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt")
    return [path for path in candidates if path.exists()]


def parse_history_lines(raw: str) -> list[str]:
    commands: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # zsh extended history: ': 1712345678:0;command'
        if stripped.startswith(": ") and ";" in stripped:
            commands.append(stripped.split(";", 1)[1].strip())
            continue

        # fish history snippet: '- cmd: command'
        if stripped.startswith("- cmd:"):
            commands.append(stripped.split(":", 1)[1].strip())
            continue

        commands.append(stripped)
    return commands


def command_hash(command: str) -> str:
    normalized = " ".join(command.strip().split())
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()


def is_technical_command(command: str) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.strip().split()
    if not parts:
        return False

    cmd = parts[0].lower()
    if cmd in {"sudo", "doas"} and len(parts) > 1:
        cmd = parts[1].lower()

    if cmd in TECHNICAL_TOOLS:
        return True
    return any(cmd.startswith(prefix) for prefix in ("nmap", "gobuster", "smb", "enum4linux"))


# Prose/markup patterns that indicate the line is AI-generated output, not raw
# terminal output.  Matching any of these causes detect_feedback_signal to bail
# out early so we never treat an AI explanation as a new trigger.
_PROSE_INDICATORS = (
    # Rich / Textual markup brackets produced by the TUI itself
    "[green]", "[red]", "[magenta]", "[yellow]", "[grey", "[bold",
    # Typical AI sentence openers that would contain failure keywords
    "it looks like", "it seems like", "let me ", "the exploit",
    "you should", "try running", "this suggests", "note that",
)


def detect_feedback_signal(text: str) -> str | None:
    # Ignore lines that are clearly AI/TUI prose, not raw terminal output.
    # A genuine terminal failure line is short and terse; prose is long.
    if len(text) > 300:
        return None
    lowered_text = text.lower()
    if any(indicator in lowered_text for indicator in _PROSE_INDICATORS):
        return None

    normalized = lowered_text
    failure_markers = (
        "access denied",
        "permission denied",
        "command not found",
        "connection refused",
        "authentication failed",
        "no such file or directory",
        "nt_status_logon_failure",
        "exploit completed, but no session was created",
        "no session was created",
        # "failed" alone is intentionally omitted — it matches AI explanations.
        # Use the specific compound forms above instead.
    )
    success_markers = (
        "root@",
        "id: uid=0",
        "uid=0(",
        "pwned",
        "shell spawned",
        "whoami\nroot",
    )

    if any(marker in normalized for marker in success_markers):
        return "success"
    if any(marker in normalized for marker in failure_markers):
        return "failure"
    return None


# HTB machine names are 1-24 chars, letters/digits/hyphens, not all-digits,
# and not common shell navigation tokens like '..', '-', '~', etc.
_HTB_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9-]{0,23}$")
_SHELL_NAV_TOKENS = {"~", "-", "..", ".", "$HOME", "/", ""}


def detect_box_cd(command: str) -> str | None:
    """Return the normalised machine name if *command* looks like `cd <htb-box>`.

    Returns None otherwise.  Does NOT require the path to exist on disk,
    since the user may be running cereal-killer on a different host.
    """
    stripped = command.strip()
    if not stripped.lower().startswith("cd "):
        return None
    target = stripped[3:].strip().strip('"\'')
    # Discard absolute paths and common shell navigation tokens.
    # Allow ~/... paths — we strip the prefix and use the last component.
    if target in _SHELL_NAV_TOKENS or target.startswith("/"):
        return None
    if target.startswith("~/"):
        target = target[2:]
    elif target == "~":
        return None
    # Take only the last component of relative paths (e.g. htb/Lame → Lame).
    name = target.split("/")[-1].split("\\")[-1]
    if _HTB_NAME_RE.match(name) and not name.isdigit():
        return name.lower()
    return None


def needs_structured_output_hint(command: str) -> bool:
    lowered = command.lower()
    json_output_flags = ("-oj", "--json", "-ojson", "--output-format json")
    if any(flag in lowered for flag in json_output_flags):
        return False

    json_capable_tools = ("nmap", "ffuf", "gobuster", "feroxbuster")
    return any(lowered.startswith(tool) or f" {tool} " in lowered for tool in json_capable_tools)


def candidate_feedback_files() -> list[Path]:
    home = Path.home()
    candidates = [
        home / ".mentor_terminal_feedback.log",
        home / "htb/session.log",
    ]
    return [path for path in candidates if path.exists()]


def filter_context_commands(commands: list[str], cwd: str, limit: int = HISTORY_CONTEXT_LIMIT) -> list[str]:
    cwd_path = Path(cwd).expanduser()
    cwd_name = cwd_path.name

    # Approximate shell state by tracking cd transitions.
    shell_dir = cwd_path
    scoped: list[str] = []
    for command in commands:
        stripped = command.strip()
        if stripped.startswith("cd "):
            target = stripped[3:].strip().strip('"\'')
            if target in {"~", "$HOME"}:
                shell_dir = Path.home()
            elif target.startswith("/"):
                shell_dir = Path(target)
            else:
                shell_dir = (shell_dir / target).resolve()

        if shell_dir == cwd_path or cwd in stripped or (cwd_name and cwd_name in stripped):
            scoped.append(command)

    if not scoped:
        scoped = commands
    return scoped[-limit:]


# Minimum seconds between two consecutive feedback-triggered brain calls.
# Prevents a loop where the AI response (containing failure keywords) is
# captured by a shell tee and fed back into the same feedback file.
_FEEDBACK_COOLDOWN_SECS = 30


async def observe_history(cwd: str) -> AsyncIterator[HistoryEvent]:
    if awatch is None or Change is None:
        raise RuntimeError("watchfiles is required for asynchronous history stalking.")

    history_files = candidate_history_files()
    if not history_files:
        while True:
            await asyncio.sleep(5)
            yield HistoryEvent(command="", context_commands=[], trigger_brain=False)

    target = history_files[0]
    feedback_files = candidate_feedback_files()
    watch_targets = [target, *feedback_files]
    feedback_offsets: dict[Path, int] = {}
    for feedback_file in feedback_files:
        try:
            feedback_offsets[feedback_file] = feedback_file.stat().st_size
        except OSError:
            feedback_offsets[feedback_file] = 0

    # Tracks the last time a feedback event triggered the brain, and a rolling
    # set of line-content hashes that have already fired.  Both guards prevent
    # the Sarcastic Singularity: AI response → feedback file → new trigger.
    _last_feedback_trigger: float = 0.0
    _triggered_line_hashes: set[str] = set()

    try:
        text = target.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = ""

    command_stream = parse_history_lines(text)
    seen_hashes = {command_hash(command) for command in command_stream}
    pending_json_hint_candidate = False

    async for changes in awatch(*watch_targets):
        relevant = any(change in {Change.modified, Change.added} and Path(path) == target for change, path in changes)
        feedback_changed_files = [
            Path(path)
            for change, path in changes
            if change in {Change.modified, Change.added} and Path(path) in feedback_files
        ]

        for feedback_file in feedback_changed_files:
            try:
                with feedback_file.open("r", encoding="utf-8", errors="ignore") as fh:
                    fh.seek(feedback_offsets.get(feedback_file, 0))
                    fresh_lines = fh.read().splitlines()
                    feedback_offsets[feedback_file] = fh.tell()
            except OSError:
                fresh_lines = []

            for line in fresh_lines:
                signal = detect_feedback_signal(line)
                if not signal:
                    if pending_json_hint_candidate and len(line.strip()) > 220:
                        yield HistoryEvent(
                            command="",
                            context_commands=command_stream[-HISTORY_CONTEXT_LIMIT:],
                            trigger_brain=False,
                            json_hint=(
                                "I'm not reading that novel. Run it with JSON output next time so I can actually help you."
                            ),
                        )
                        pending_json_hint_candidate = False
                    continue

                # --- Loop-break guards ---
                # 1. Cooldown: suppress if we fired too recently.
                now = _time.monotonic()
                if now - _last_feedback_trigger < _FEEDBACK_COOLDOWN_SECS:
                    continue
                # 2. Deduplication: same exact line already fired once — skip.
                line_hash = command_hash(line.strip())
                if line_hash in _triggered_line_hashes:
                    continue

                _last_feedback_trigger = now
                _triggered_line_hashes.add(line_hash)
                # Bound the dedup set so it doesn't grow unbounded across a session.
                if len(_triggered_line_hashes) > 200:
                    _triggered_line_hashes.clear()

                yield HistoryEvent(
                    command="",
                    context_commands=command_stream[-HISTORY_CONTEXT_LIMIT:],
                    trigger_brain=True,
                    feedback_signal=signal,
                    feedback_line=line.strip(),
                )

        if not relevant:
            continue

        try:
            text = target.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        parsed = parse_history_lines(text)

        # Handle truncation/log rotation by rebuilding seen hashes.
        if len(parsed) < len(command_stream):
            command_stream = parsed
            seen_hashes = {command_hash(command) for command in command_stream}
            continue

        new_commands = parsed[len(command_stream) :]
        command_stream = parsed

        for command in new_commands:
            digest = command_hash(command)
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)

            context_commands = filter_context_commands(command_stream, cwd)
            feedback_signal = detect_feedback_signal(command)
            pending_json_hint_candidate = needs_structured_output_hint(command)
            cd_target = detect_box_cd(command)
            yield HistoryEvent(
                command=command,
                context_commands=context_commands,
                trigger_brain=is_technical_command(command) or feedback_signal is not None,
                feedback_signal=feedback_signal,
                feedback_line=command if feedback_signal else None,
                cd_target=cd_target,
            )

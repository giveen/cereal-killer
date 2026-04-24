from __future__ import annotations

import asyncio
import hashlib
import os
import platform
import re
import logging
import shlex
import time as _time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import pwd
except ImportError:  # pragma: no cover - unavailable on Windows
    pwd = None  # type: ignore[assignment]

try:
    from watchfiles import Change, awatch
except ImportError:  # pragma: no cover - enables import-only unit tests in minimal envs
    Change = None  # type: ignore[assignment]
    awatch = None  # type: ignore[assignment]

from cereal_killer.config import HISTORY_CONTEXT_LIMIT


TECHNICAL_TOOLS = {
    "nmap",
    "gobuster",
    "feroxbuster",
    "ffuf",
    "nikto",
    "sqlmap",
    "dirsearch",
    "wfuzz",
    "smbclient",
    "smbmap",
    "enum4linux",
    "msfconsole",
    "netexec",
    "crackmapexec",
    "hydra",
    "john",
    "hashcat",
    "tcpdump",
    "wireshark",
    "nuclei",
}

def _is_similar_command(command1: str, command2: str) -> bool:
    """Check if two commands are semantically similar.

    Returns True if the commands are the same or differ only by arguments
    (e.g., different IPs, ports, file paths).
    """
    # Normalize commands
    cmd1 = " ".join(command1.strip().split()).lower()
    cmd2 = " ".join(command2.strip().split()).lower()

    # Exact match
    if cmd1 == cmd2:
        return True

    # Similar if they differ only by arguments
    cmd1_parts = cmd1.split()
    cmd2_parts = cmd2.split()

    if len(cmd1_parts) != len(cmd2_parts):
        return False

    # Check if same command with different arguments
    for part1, part2 in zip(cmd1_parts, cmd2_parts):
        # Skip if parts differ only by numbers or paths
        if part1 != part2:
            # Extract numbers and paths
            part1_no_num = re.sub(r'\d+', 'NUM', part1)
            part2_no_num = re.sub(r'\d+', 'NUM', part2)
            if part1_no_num != part2_no_num:
                return False
    return True


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
    # Populated when a command references a host like `cap.htb`.
    host_target: str | None = None


def candidate_history_files() -> list[Path]:
    homes = candidate_user_homes()
    system = platform.system().lower()
    candidates: list[Path] = []
    if "darwin" in system or "linux" in system:
        suffixes = [
            ".zsh_history",
            ".bash_history",
            ".local/share/fish/fish_history",
        ]
    elif "windows" in system:
        suffixes = ["AppData/Roaming/Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt"]
    else:
        suffixes = [".zsh_history", ".bash_history"]

    for home in homes:
        candidates.extend(home / suffix for suffix in suffixes)

    return [path for path in candidates if path.exists()]


def candidate_user_homes() -> list[Path]:
    """Return plausible user-home paths, preferring the logged-in user over root."""

    candidates: list[Path] = []

    def _add(path: str | Path | None) -> None:
        if not path:
            return
        candidate = Path(path).expanduser()
        if candidate not in candidates:
            candidates.append(candidate)

    # Prefer sudo-invoking user when running as root under sudo.
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and pwd is not None:
        try:
            _add(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass

    # Include explicit HOME and current process user home.
    _add(os.environ.get("HOME"))
    if pwd is not None:
        try:
            _add(pwd.getpwuid(os.getuid()).pw_dir)
        except KeyError:
            pass
    _add(Path.home())

    # Add user homes hinted by common identity env vars.
    for env_var in ("LOGNAME", "USER"):
        username = os.environ.get(env_var)
        if not username:
            continue
        if pwd is None:
            continue
        try:
            _add(pwd.getpwnam(username).pw_dir)
        except KeyError:
            continue

    # Keep root home as a last resort when other homes are present.
    root_home = Path("/root")
    if root_home in candidates and len(candidates) > 1:
        candidates = [path for path in candidates if path != root_home] + [root_home]

    return candidates


def parse_history_lines(raw: str) -> list[str]:
    commands: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # zsh extended history: ': 1712345678:0;command'
        # Strip the timestamp before adding to commands
        if stripped.startswith(": ") and ";" in stripped:
            # Extract and clean the command after the semicolon
            cmd = stripped.split(";", 1)[1].strip()
            if cmd:
                commands.append(cmd)
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


# Patterns indicating the line is Python source code rather than a shell command.
# These leak into history when users run heredoc scripts directly in the terminal.
_PYTHON_CODE_PREFIXES = (
    "from ", "import ", "print(", "def ", "class ", "async def ",
    "await ", "return ", "for ", "try:", "except ", "if __name__",
    "with open", "    ",  # indented lines
)


def _is_python_code_line(command: str) -> bool:
    """Return True if the command looks like Python source rather than a shell command."""
    stripped = command.strip()
    # Multi-line heredoc content often starts with keywords or is indented
    if any(stripped.startswith(p) for p in _PYTHON_CODE_PREFIXES):
        return True
    # Lines ending with 'PY' delimiter are heredoc terminators
    if stripped in ("PY", "EOF", "PYTHON"):
        return True
    return False


def is_technical_command(
    command: str,
    tech_tools: frozenset[str] | None = None,
    prefixes: tuple[str, ...] | None = None,
) -> bool:
    # Never treat Python source code lines as technical commands
    if _is_python_code_line(command):
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.strip().split()
    if not parts:
        return False

    cmd = parts[0].lower()
    if cmd in {"sudo", "doas"} and len(parts) > 1:
        cmd = parts[1].lower()

    # Prefer provided tech_tools, fall back to hardcoded TECHNICAL_TOOLS
    tools_set = tech_tools if tech_tools is not None else TECHNICAL_TOOLS
    if cmd in tools_set:
        return True
    # Prefer provided prefixes, fall back to hardcoded defaults
    prefix_list = prefixes if prefixes is not None else ("nmap", "gobuster", "smb", "enum4linux")
    return any(cmd.startswith(prefix) for prefix in prefix_list)


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
    # Ignore lines that look like Python source code (heredoc leakage).
    if _is_python_code_line(text):
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
        "error:",
        "failed:",
        "timeout",
        "timed out",
        "refused",
    )
    success_markers = (
        "root@",
        "id: uid=0",
        "uid=0(",
        "pwned",
        "shell spawned",
        "whoami\nroot",
        "access granted",
        "connection established",
        "session opened",
        "successfully",
        "complete",
        "done",
    )

    if any(marker in normalized for marker in failure_markers):
        return "failure"
    if any(marker in normalized for marker in success_markers):
        return "success"
    return None


# HTB machine names are 1-24 chars, letters/digits/hyphens, not all-digits,
# and not common shell navigation tokens like '..', '-', '~', etc.
_HTB_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9-]{0,23}$")
_HTB_HOST_RE = re.compile(r"\b([a-zA-Z][a-zA-Z0-9-]{0,23})\.htb\b", re.IGNORECASE)
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


def detect_box_host(command: str) -> str | None:
    """Return a machine name when a command references a host like `cap.htb`."""
    for match in _HTB_HOST_RE.finditer(command):
        candidate = match.group(1).lower()
        # Avoid common local aliases that are not HTB box names.
        if candidate not in {"localhost", "local", "host", "gateway"}:
            return candidate
    return None


def needs_structured_output_hint(command: str) -> bool:
    lowered = command.lower()
    json_output_flags = ("-oj", "--json", "-ojson", "--output-format json")
    if any(flag in lowered for flag in json_output_flags):
        return False

    json_capable_tools = ("nmap", "ffuf", "gobuster", "feroxbuster")
    return any(lowered.startswith(tool) or f" {tool} " in lowered for tool in json_capable_tools)


def candidate_feedback_files() -> list[Path]:
    candidates: list[Path] = []
    for home in candidate_user_homes():
        candidates.extend(
            [
                home / ".mentor_terminal_feedback.log",
                home / "htb/session.log",
            ]
        )
    return [path for path in candidates if path.exists()]


def filter_context_commands(commands: list[str], cwd: str, limit: int = HISTORY_CONTEXT_LIMIT) -> list[str]:
    cwd_path = Path(cwd).expanduser()
    cwd_name = cwd_path.name
    candidate_homes = candidate_user_homes()
    default_home = candidate_homes[0] if candidate_homes else Path.home()

    # Approximate shell state by tracking cd transitions.
    shell_dir = cwd_path
    scoped: list[str] = []
    for command in commands:
        stripped = command.strip()
        if stripped.startswith("cd "):
            target = stripped[3:].strip().strip('"\'')
            if target in {"~", "$HOME"}:
                shell_dir = default_home
            elif target.startswith("/"):
                shell_dir = Path(target)
            else:
                shell_dir = (shell_dir / target).resolve()

        # Enhanced filtering: check if command is relevant to current directory
        # or any parent directory in the current path
        cmd_path_match = any(target in stripped for target in shell_dir.parts)
        if shell_dir == cwd_path or cwd in stripped or (cwd_name and cwd_name in stripped) or cmd_path_match:
            scoped.append(command)

    if not scoped:
        scoped = commands
    return scoped[-limit:]


# Minimum seconds between two consecutive feedback-triggered brain calls.
# Prevents a loop where the AI response (containing failure keywords) is
# captured by a shell tee and fed back into the same feedback file.
# When settings is available, the cooldown is resolved at call time
# inside observe_history via the `settings` parameter.
_FEEDBACK_COOLDOWN_SECS = 30


def _read_history_file_binary(path: Path) -> str:
    """Read history file in binary mode to handle non-UTF-8 bytes and null characters.
    
    This prevents crashes when Kali or other systems write weird metadata to history files.
    Returns decoded text with errors ignored.
    """
    try:
        with path.open("rb") as f:
            raw_bytes = f.read()
        return raw_bytes.decode("utf-8", errors="ignore")
    except OSError:
        return ""


def _check_history_path_readable(path: Path) -> tuple[bool, str]:
    """Check if history path is readable. Returns (is_readable, error_message)."""
    if not path.exists():
        return False, f"I'm blind! I can't read {path}. The history file doesn't exist. Check your HISTORY_PATH or file permissions."
    
    if not os.access(path, os.R_OK):
        return False, f"I'm blind! I can't read {path}. Check your UID/GID or file permissions. Current user: {os.getuid()}:{os.getgid()}"
    
    return True, ""


def _resolve_history_path() -> Path:
    """Resolve history file path from env override or machine-local defaults."""
    history_path_override = os.environ.get("HISTORY_PATH", "").strip()
    if history_path_override:
        override = Path(history_path_override).expanduser()
        is_readable, _ = _check_history_path_readable(override)
        if is_readable:
            return override

    candidates = candidate_history_files()
    for candidate in candidates:
        is_readable, _ = _check_history_path_readable(candidate)
        if is_readable:
            return candidate

    # Last-resort fallback: prefer bash history on unknown shells.
    return Path.home() / ".bash_history"


async def observe_history(cwd: str, settings: Any = None) -> AsyncIterator[HistoryEvent]:
    if awatch is None or Change is None:
        raise RuntimeError("watchfiles is required for asynchronous history stalking.")

    # Resolve settings-based values at call time.
    # When settings is available, use its tech_tools and feedback cooldown.
    # Otherwise fall back to the hardcoded defaults.
    _tech_tools: frozenset[str] | None = None
    _cooldown_secs: float = _FEEDBACK_COOLDOWN_SECS
    if settings is not None:
        _tech_tools = frozenset(getattr(settings, "tech_tools", []))
        _cooldown_secs = getattr(settings, "feedback_cooldown_seconds", _FEEDBACK_COOLDOWN_SECS)

    history_path = _resolve_history_path()
    
    # Check if history path is readable and try one more fallback before erroring.
    is_readable, error_msg = _check_history_path_readable(history_path)
    if not is_readable:
        logging.getLogger(__name__).error(error_msg)
        raise RuntimeError(error_msg)
    
    # Try one more fallback before erroring.
    if not is_readable:
        for candidate in candidate_history_files():
            if candidate == history_path:
                continue
            candidate_ok, _ = _check_history_path_readable(candidate)
            if candidate_ok:
                history_path = candidate
                is_readable = True
                break

    if not is_readable:
        raise RuntimeError(error_msg)
    
    target = history_path
    feedback_files = candidate_feedback_files()
    if not feedback_files:
        logging.getLogger(__name__).debug("No feedback files found")
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

    # Track file size for delta-only reading
    file_offset = 0
    try:
        text = _read_history_file_binary(target)
        file_offset = target.stat().st_size
    except OSError:
        text = ""
        file_offset = 0

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
            except OSError as exc:
                logging.getLogger(__name__).warning(f"Error reading feedback file {feedback_file}: {exc}")
                continue

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
                if now - _last_feedback_trigger < _cooldown_secs:
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
            current_size = target.stat().st_size
            if current_size < file_offset:
                # File was truncated/rotated - read entire file
                text = _read_history_file_binary(target)
            elif current_size > file_offset:
                # Read only the delta
                with target.open("rb") as f:
                    f.seek(file_offset)
                    delta_bytes = f.read()
                delta_text = delta_bytes.decode("utf-8", errors="ignore")
                text = text + delta_text
            else:
                # No change in size
                continue
            
            file_offset = current_size
        except OSError as exc:
            logging.getLogger(__name__).warning(f"Error reading history file {target}: {exc}")
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
            host_target = detect_box_host(command)
            yield HistoryEvent(
                command=command,
                context_commands=context_commands,
                trigger_brain=is_technical_command(command, tech_tools=_tech_tools) or feedback_signal is not None,
                feedback_signal=feedback_signal,
                feedback_line=command if feedback_signal else None,
                cd_target=cd_target,
                host_target=host_target,
            )

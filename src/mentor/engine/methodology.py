"""Methodology auditor — warns when the user skips critical enumeration phases.

The auditor checks whether a command looks like an exploitation attempt
(searchsploit, msfconsole, raw python exploits, sqlmap, …) and whether the
shell history contains evidence of thorough enumeration first.

If no prior thorough recon is found, ``audit_command`` returns a Zero Cool
warning string that the UI should display in the chat log.
"""
from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Commands that represent a jump straight to exploitation.
_EXPLOIT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bsearchsploit\b", re.IGNORECASE),
    re.compile(r"\bmsfconsole\b", re.IGNORECASE),
    re.compile(r"\bmsf\s*>\s*use\b", re.IGNORECASE),
    re.compile(r"python3?\s+\S+\.py\b", re.IGNORECASE),
    re.compile(r"\bsqlmap\b", re.IGNORECASE),
    re.compile(r"\bburpsuite\b|\bjava\s+-jar\s+burp\b", re.IGNORECASE),
]

# Evidence of adequate prior enumeration in history.
_RECON_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"nmap\b.+(-sV|-A\b|-sC|-p-)", re.IGNORECASE),
    re.compile(r"\b(gobuster|feroxbuster|dirbuster|ffuf|wfuzz|nikto)\b", re.IGNORECASE),
    re.compile(r"\b(smbclient|smbmap|enum4linux|crackmapexec|netexec)\b", re.IGNORECASE),
]

_WARNING = (
    "You're jumping the gun. "
    "Finish your enumeration first. "
    "I need at minimum: nmap -sV, a web directory scan, and SMB enumeration "
    "before you start throwing exploits at the wall."
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_exploit_command(command: str) -> bool:
    """Return True if *command* looks like a premature exploitation attempt."""
    return any(p.search(command) for p in _EXPLOIT_PATTERNS)


def has_thorough_recon(history: list[str]) -> bool:
    """Return True if *history* contains at least one comprehensive recon command."""
    return any(
        any(p.search(cmd) for p in _RECON_PATTERNS)
        for cmd in history
    )


def audit_command(command: str, history: list[str]) -> str | None:
    """Return a Zero Cool warning if the user is exploiting before adequate recon.

    Returns ``None`` when no violation is detected (normal case).
    """
    if not is_exploit_command(command):
        return None
    if has_thorough_recon(history):
        return None
    return _WARNING

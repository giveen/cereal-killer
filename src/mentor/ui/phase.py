from __future__ import annotations

import re


_PHASE_RULES: list[tuple[str, tuple[re.Pattern[str], ...]]] = [
    (
        "[POST-EXPLOITATION]",
        (
            re.compile(r"\blinpeas\b", re.IGNORECASE),
            re.compile(r"(^|\s)whoami(\s|$)", re.IGNORECASE),
            re.compile(r"(^|\s)id(\s|$)", re.IGNORECASE),
        ),
    ),
    (
        "[EXPLOITATION]",
        (
            re.compile(r"\bnc\b", re.IGNORECASE),
            re.compile(r"\bsearchsploit\b", re.IGNORECASE),
            re.compile(r"\bmsfconsole\b", re.IGNORECASE),
            re.compile(r"python3\s+-c\s+['\"]import\s+pty", re.IGNORECASE),
            re.compile(r"python3.*(exploit|payload|reverse\s*shell|cve-\d{4}-\d+)", re.IGNORECASE),
        ),
    ),
    (
        "[ENUMERATION]",
        (
            re.compile(r"\bgobuster\b", re.IGNORECASE),
            re.compile(r"\bdirbuster\b", re.IGNORECASE),
            re.compile(r"\bferoxbuster\b", re.IGNORECASE),
            re.compile(r"\bnikto\b", re.IGNORECASE),
        ),
    ),
    (
        "[RECON]",
        (
            re.compile(r"\bnmap\b", re.IGNORECASE),
            re.compile(r"\bmasscan\b", re.IGNORECASE),
        ),
    ),
]


def detect_phase(history_commands: list[str]) -> str:
    for command in reversed(history_commands):
        for phase, patterns in _PHASE_RULES:
            if any(pattern.search(command) for pattern in patterns):
                return phase
    return "[IDLE]"

"""Socratic state machine for Cereal-Killer.

Tracks how long the user has been stuck in the current phase (no meaningful
technical command seen) and scales Brain's hint depth accordingly.

States
------
VAGUE       (0–10 min)   — Socratic questions only; no technical specifics.
CONCEPTUAL  (10–20 min)  — Conceptual direction; class of vulnerability named.
DIRECT      (20+ min)    — Concrete technical pointer; exact tool / CVE / step.

The 'DIRECT' state also unlocks SearXNG web search as a last resort.
"""
from __future__ import annotations

import time
from enum import Enum


# ---------------------------------------------------------------------------
# State definition
# ---------------------------------------------------------------------------

class HintLevel(Enum):
    VAGUE      = "vague"       # user is making progress
    CONCEPTUAL = "conceptual"  # user has been stuck 10–20 min
    DIRECT     = "direct"      # user has been stuck 20+ min


# Thresholds in seconds.
_VAGUE_SECONDS      = 10 * 60   # 0–10 min
_CONCEPTUAL_SECONDS = 20 * 60   # 10–20 min


# System-prompt addenda injected per level.
_HINT_ADDENDA: dict[HintLevel, str] = {
    HintLevel.VAGUE: (
        "HINT DEPTH: CRYPTIC. "
        "The user is still actively working — do not give direct technical answers. "
        "Use only Socratic questions and general methodology nudges. "
        "Example: 'What services did you find on that port?'"
    ),
    HintLevel.CONCEPTUAL: (
        "HINT DEPTH: CONCEPTUAL. "
        "The user has been stuck for 10–20 minutes. "
        "Name the class of vulnerability or technique without giving the exact exploit or CVE. "
        "Example: 'Have you considered what that service version implies about known CVEs?'"
    ),
    HintLevel.DIRECT: (
        "HINT DEPTH: DIRECT. "
        "The user has been stuck for over 20 minutes. "
        "Give a concrete technical pointer — name the specific technique, CVE, or tool to use. "
        "Be blunt but brief. "
        "Example: 'Run searchsploit against that exact version string. CVE-XXXX-XXXX is the one.'"
    ),
}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class PedagogyEngine:
    """Tracks time-since-last-progress and exposes the current hint level."""

    def __init__(self) -> None:
        self._last_progress_at: float = time.monotonic()
        self._current_phase: str = "[IDLE]"

    def record_command(self) -> None:
        """Reset the stuck-timer; call when a meaningful technical command is seen."""
        self._last_progress_at = time.monotonic()

    def record_phase_change(self, new_phase: str) -> None:
        """Reset timer when the detected phase transitions to a new phase."""
        if new_phase != self._current_phase:
            self._current_phase = new_phase
            self._last_progress_at = time.monotonic()

    def elapsed_seconds(self) -> float:
        """Seconds since last meaningful progress."""
        return time.monotonic() - self._last_progress_at

    def current_hint_level(self) -> HintLevel:
        elapsed = self.elapsed_seconds()
        if elapsed < _VAGUE_SECONDS:
            return HintLevel.VAGUE
        if elapsed < _CONCEPTUAL_SECONDS:
            return HintLevel.CONCEPTUAL
        return HintLevel.DIRECT

    def system_prompt_addendum(self) -> str:
        """Return the system-prompt text that calibrates Brain's hint depth."""
        return _HINT_ADDENDA[self.current_hint_level()]

    def should_allow_web_search(self) -> bool:
        """Web search (SearXNG last resort) is only unlocked at DIRECT level."""
        return self.current_hint_level() == HintLevel.DIRECT

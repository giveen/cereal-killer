from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class StallState(Enum):
    """States representing the stall detection status of a machine."""

    IDLE = "idle"             # Normal activity, recent command
    STALLING = "stalling"     # Approaching threshold (1-1.5x threshold)
    STALLED = "stalled"       # Past threshold, user is stuck
    RETRY_LOOP = "retry_loop" # Same command executed N times rapidly


@dataclass(slots=True)
class _MachineState:
    """Internal state tracking for a single machine."""

    last_command_at: float = field(default_factory=time.monotonic)
    last_command: Optional[str] = None
    consecutive_retries: int = 0


@dataclass(slots=True)
class StallRecord:
    """Immutable snapshot of stall detection state for a machine."""

    machine: str
    state: StallState
    last_command_at: float
    last_command: Optional[str]
    consecutive_retries: int
    elapsed_seconds: float
    recommended_action: str


class StallDetector:
    """Detects user stalls based on time-based and retry-loop heuristics."""

    RETRY_WINDOW_SECONDS = 30  # seconds

    def __init__(self, stall_threshold_seconds: int = 600, retry_threshold: int = 3) -> None:
        self._stall_threshold_seconds = stall_threshold_seconds
        self._retry_threshold = retry_threshold
        self._machines: dict[str, _MachineState] = {}

    def record_command(self, machine: str, command: str) -> None:
        """Record a command execution for stall detection."""
        if machine not in self._machines:
            self._machines[machine] = _MachineState()

        now = time.monotonic()
        state = self._machines[machine]

        # Check if this is a retry (same command within window)
        is_retry = (
            state.last_command == command and
            (now - state.last_command_at) < self.RETRY_WINDOW_SECONDS
        )

        if is_retry:
            state.consecutive_retries += 1
        else:
            state.consecutive_retries = 0

        state.last_command_at = now
        state.last_command = command

    def get_stall_state(self, machine: str) -> StallState:
        """Get current stall state for a machine."""
        if machine not in self._machines:
            return StallState.IDLE

        state = self._machines[machine]
        elapsed = time.monotonic() - state.last_command_at

        # Check for retry loop first
        if state.consecutive_retries >= self._retry_threshold:
            return StallState.RETRY_LOOP

        # Time-based detection
        if elapsed >= self._stall_threshold_seconds:
            return StallState.STALLED
        elif elapsed >= self._stall_threshold_seconds * 0.67:  # ~2/3 of threshold
            return StallState.STALLING

        return StallState.IDLE

    def get_stall_record(self, machine: str) -> StallRecord:
        """Get full stall record for a machine."""
        if machine not in self._machines:
            return StallRecord(
                machine=machine,
                state=StallState.IDLE,
                last_command_at=time.monotonic(),
                last_command=None,
                consecutive_retries=0,
                elapsed_seconds=0.0,
                recommended_action="User is making progress, no intervention needed.",
            )

        state = self._machines[machine]
        elapsed = time.monotonic() - state.last_command_at
        stall_state = self.get_stall_state(machine)

        # Determine recommended action
        if stall_state == StallState.RETRY_LOOP:
            action = (
                f"User is repeating the same command ({state.last_command}) "
                f"{state.consecutive_retries} times. Suggest an alternative approach."
            )
        elif stall_state == StallState.STALLED:
            action = (
                f"User has been stuck for {elapsed:.0f}s. "
                "Escalate pedagogy level and suggest concrete next step."
            )
        elif stall_state == StallState.STALLING:
            action = (
                f"User is approaching stall ({elapsed:.0f}s). "
                "Consider a gentle nudge or Socratic question."
            )
        else:
            action = "User is making progress, no intervention needed."

        return StallRecord(
            machine=machine,
            state=stall_state,
            last_command_at=state.last_command_at,
            last_command=state.last_command,
            consecutive_retries=state.consecutive_retries,
            elapsed_seconds=elapsed,
            recommended_action=action,
        )

    def get_recommended_action(self, machine: str) -> str:
        """Get the recommended coaching action for current stall state."""
        return self.get_stall_record(machine).recommended_action

    def clear_machine(self, machine: str) -> None:
        """Clear stall tracking for a machine."""
        self._machines.pop(machine, None)

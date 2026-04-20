from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(slots=True)
class ContextManager:
    """Manage transcript condensation and lightweight token accounting."""

    summarize_after_turns: int = 10
    summarize_window_turns: int = 8

    @staticmethod
    def estimate_tokens(text: str) -> int:
        # Fast approximation good enough for UI/guardrail decisions.
        return max(0, len((text or "").strip()) // 4)

    def should_condense(self, transcript: list[dict[str, str]]) -> bool:
        turns = sum(1 for entry in transcript if entry.get("role") in {"user", "assistant"})
        return turns >= self.summarize_after_turns

    def select_entries_for_condense(
        self,
        transcript: list[dict[str, str]],
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        if not transcript:
            return [], []

        turn_count = 0
        cutoff = -1
        for idx, entry in enumerate(transcript):
            if entry.get("role") in {"user", "assistant"}:
                turn_count += 1
                if turn_count >= self.summarize_window_turns:
                    cutoff = idx
                    break

        if cutoff < 0:
            return [], transcript

        head = transcript[: cutoff + 1]
        tail = transcript[cutoff + 1 :]
        return head, tail

    @staticmethod
    def build_summary_blob(entries: list[dict[str, str]]) -> str:
        return "\n".join(
            f"{entry.get('role', 'unknown')}: {entry.get('text', '')}" for entry in entries
        )

    @staticmethod
    def make_summary_entry(summary_text: str) -> dict[str, str]:
        return {
            "role": "summary",
            "text": summary_text.strip(),
            "timestamp": datetime.now(UTC).isoformat(),
        }

    def estimate_active_context_tokens(
        self,
        transcript: list[dict[str, str]],
        history_commands: list[str],
    ) -> int:
        transcript_text = "\n".join(entry.get("text", "") for entry in transcript)
        command_text = "\n".join(history_commands[-20:])
        return self.estimate_tokens(transcript_text) + self.estimate_tokens(command_text)

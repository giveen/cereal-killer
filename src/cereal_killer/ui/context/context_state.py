from __future__ import annotations

import asyncio
import difflib
import re
from datetime import UTC, datetime
from typing import Any

from textual.widgets import Markdown

from cereal_killer.ui.screens import MainDashboard
from ..base import resolve_dashboard

CODE_BLOCK_PATTERN = re.compile(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", re.DOTALL)


class ContextStateManager:
    """Manages chat transcript, context token counting, and pruning."""

    def __init__(self, app: Any) -> None:
        self._app = app

    # -- helpers ----------------------------------------------------------

    def _try_dashboard(self) -> MainDashboard | None:
        """Return MainDashboard from active screen or stack, if present."""
        return resolve_dashboard(self._app)

    # -- transcript management --------------------------------------------

    def _append_chat(self, role: str, text: str) -> None:
        self._app.active_transcript.append(
            {
                "role": role,
                "text": text,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        self._update_context_token_counter()
        self._schedule_context_prune()

    def _update_context_token_counter(self) -> None:
        dashboard = self._try_dashboard()
        if dashboard is None:
            return
        current_tokens = self._app._context_manager.estimate_active_context_tokens(
            self._app.active_transcript,
            self._app.active_history,
        )
        max_tokens = int(getattr(self._app.engine.settings, "max_model_len", 0) or 0)
        dashboard.set_context_token_counter(current_tokens, max_tokens)

    def _warn_if_repetitive_response(self, new_response: str) -> None:
        last_assistant = ""
        for entry in reversed(self._app.active_transcript):
            if entry.get("role") == "assistant":
                last_assistant = str(entry.get("text", ""))
                break
        if not last_assistant or not new_response:
            return
        ratio = difflib.SequenceMatcher(None, last_assistant, new_response).ratio()
        if ratio >= 0.90:
            self._app.notify(
                "[System] Zero Cool is repeating himself. Try providing more specific tool output.",
                title="Repetition Warning",
                severity="warning",
            )

    def _schedule_context_prune(self) -> None:
        if not self._app._pruning_in_flight:
            asyncio.create_task(self._maybe_prune_transcript())

    async def _maybe_prune_transcript(self) -> None:
        if self._app._pruning_in_flight:
            return

        total_chars = sum(len(e.get("text", "")) for e in self._app.active_transcript)
        threshold = self._app.engine.prune_threshold()
        needs_budget_prune = total_chars > threshold
        needs_turn_condense = self._app._context_manager.should_condense(
            self._app.active_transcript
        )

        if not needs_budget_prune and not needs_turn_condense:
            return

        self._app._pruning_in_flight = True
        try:
            if needs_turn_condense and await self._maybe_condense_transcript():
                self._update_context_token_counter()
                return

            if needs_budget_prune and await self._maybe_budget_prune_transcript(total_chars):
                self._update_context_token_counter()
        finally:
            self._app._pruning_in_flight = False

    async def _maybe_condense_transcript(self) -> bool:
        entries_to_summarize, remaining = self._app._context_manager.select_entries_for_condense(
            self._app.active_transcript
        )
        if not entries_to_summarize:
            return False
        summary_blob = self._app._context_manager.build_summary_blob(entries_to_summarize)
        summary = await self._app.engine.summarize_session(summary_blob)
        self._app.set_active_transcript([
            self._app._context_manager.make_summary_entry(summary),
            *remaining,
        ])
        return True

    async def _maybe_budget_prune_transcript(self, total_chars: int) -> bool:
        target = self._app.engine.prune_target()
        chars_to_drop = total_chars - target
        entries_to_summarize: list[dict[str, str]] = []
        dropped = 0
        for entry in self._app.active_transcript:
            if dropped >= chars_to_drop:
                break
            entries_to_summarize.append(entry)
            dropped += len(entry.get("text", ""))
        if not entries_to_summarize:
            return False
        blob = "\n".join(
            f"{e.get('role', 'unknown')}: {e.get('text', '')}"
            for e in entries_to_summarize
        )
        summary = await self._app.engine.summarize_session(blob)
        summary_entry = {
            "role": "summary",
            "text": summary,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        remaining = self._app.active_transcript[len(entries_to_summarize):]
        self._app.set_active_transcript([summary_entry, *remaining])
        return True

    # -- code block tracking ----------------------------------------------

    def _track_code_block(self, response_text: str) -> None:
        matches = CODE_BLOCK_PATTERN.findall(response_text)
        if matches:
            self._app.last_code_block = matches[-1].strip()

    # Public aliases for delegation from app.py
    schedule_prune = _schedule_context_prune
    maybe_prune_transcript = _maybe_prune_transcript
    append_chat = _append_chat
    update_token_counter = _update_context_token_counter
    warn_repetitive = _warn_if_repetitive_response
    track_code_block = _track_code_block

from __future__ import annotations

from collections.abc import AsyncIterator

from mentor.observer.stalker import (
    HistoryEvent,
    candidate_history_files,
    detect_box_cd,
    detect_box_host,
    detect_feedback_signal,
    filter_context_commands,
    is_technical_command,
    needs_structured_output_hint,
    observe_history as _observe_history_events,
    parse_history_lines,
)

from .vision_watcher import (
    ClipboardImageDetected,
    ClipboardImageWatcher,
    ascii_preview_for_image,
    clear_clipboard_buffer,
)


async def observe_history(cwd: str) -> AsyncIterator[list[str]]:
    async for event in _observe_history_events(cwd):
        yield event.context_commands


async def observe_history_events(cwd: str) -> AsyncIterator[HistoryEvent]:
    async for event in _observe_history_events(cwd):
        yield event


__all__ = [
    "HistoryEvent",
    "candidate_history_files",
    "detect_box_cd",
    "detect_box_host",
    "detect_feedback_signal",
    "filter_context_commands",
    "is_technical_command",
    "needs_structured_output_hint",
    "parse_history_lines",
    "observe_history",
    "observe_history_events",
    "ClipboardImageDetected",
    "ClipboardImageWatcher",
    "ascii_preview_for_image",
    "clear_clipboard_buffer",
]

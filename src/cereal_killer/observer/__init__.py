from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from mentor.observer import stalker
from mentor.observer.stalker import (
    HistoryEvent,
    candidate_history_files,
    detect_box_cd,
    detect_box_host,
    detect_feedback_signal,
    filter_context_commands,
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


async def observe_history(cwd: str, settings: Any = None) -> AsyncIterator[list[str]]:
    async for event in _observe_history_events(cwd, settings):
        yield event.context_commands


async def observe_history_events(cwd: str, settings: Any = None) -> AsyncIterator[HistoryEvent]:
    async for event in _observe_history_events(cwd, settings):
        yield event


def is_technical_command(command: str, settings: Any = None) -> bool:
    """Check if a command is technical, using settings for tool list if available."""
    if settings is not None:
        return stalker.is_technical_command(command, tech_tools=frozenset(settings.tech_tools))
    return stalker.is_technical_command(command)


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

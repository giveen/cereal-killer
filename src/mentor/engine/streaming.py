"""Streaming types and helpers for LLM response streaming."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class StreamingState:
    """Tracks the accumulating state of a streaming LLM response."""
    accumulated_content: str = ""
    accumulated_thought: str = ""
    accumulated_answer: str = ""
    reasoning_content: str = ""
    backend_meta: dict[str, Any] = field(default_factory=dict)
    cancelled: bool = False


@runtime_checkable
class StreamingCallbacks(Protocol):
    """Protocol for streaming callbacks."""

    async def on_token(self, token: str) -> None: ...

    async def on_thought_update(self, thought: str) -> None: ...

    async def on_complete(self, response: Any) -> None: ...  # BrainResponse

    async def on_error(self, error: Exception) -> None: ...

    async def cancel(self) -> None: ...


THOUGHT_PATTERN = re.compile(r"<thought>(.*?)</thought>", re.DOTALL | re.IGNORECASE)


def extract_partial_thought(content: str) -> str:
    """Extract all complete <thought>...</thought> blocks from partial content.

    Only returns complete tags — partial/opening tags without a closing </thought>
    are ignored, since the stream may still be arriving.
    """
    thoughts = THOUGHT_PATTERN.findall(content)
    return "\n\n".join(item.strip() for item in thoughts if item.strip())


def extract_partial_answer(content: str) -> str:
    """Extract the answer portion from partial content.

    Removes all complete <thought>...</thought> blocks and returns the remainder.
    """
    return THOUGHT_PATTERN.sub("", content).strip()

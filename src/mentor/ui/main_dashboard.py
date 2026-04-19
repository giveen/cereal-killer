"""Main dashboard bridge for live feed and prompt routing."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Sequence

from mentor.engine.brain import Brain, BrainResponse

_CODE_BLOCK_PATTERN = re.compile(r"```(?:[\w.+-]+)?\n(.*?)```", re.DOTALL)


@dataclass(slots=True)
class DashboardReply:
    text: str
    thoughts: list[str] = field(default_factory=list)
    code_blocks: list[str] = field(default_factory=list)


class MainDashboard:
    """Bridges terminal events and user prompts to the Brain."""

    def __init__(
        self,
        brain: Brain,
        *,
        feed_limit: int = 250,
        copy_handler: Callable[[str], None] | None = None,
    ) -> None:
        self.brain = brain
        self.feed_limit = feed_limit
        self.copy_handler = copy_handler
        self._live_feed: Deque[str] = deque(maxlen=feed_limit)
        self._last_code_blocks: list[str] = []
        self._thought_feed: Deque[str] = deque(maxlen=feed_limit)

        if getattr(self.brain, "on_thoughts", None) is None:
            self.brain.on_thoughts = self.add_thoughts

    def add_terminal_command(self, command: str) -> None:
        self._live_feed.append(command)

    def add_thoughts(self, thoughts: Sequence[str]) -> None:
        for thought in thoughts:
            self._thought_feed.append(thought)

    def get_live_feed(self) -> list[str]:
        return list(self._live_feed)

    def get_reasoning_feed(self) -> list[str]:
        return list(self._thought_feed)

    async def submit_prompt(self, prompt: str, cwd: str | None = None) -> DashboardReply:
        response: BrainResponse = await self.brain.ask(
            prompt=prompt,
            context_commands=self.get_live_feed()[-50:],
            cwd=cwd,
        )
        code_blocks = [block.strip() for block in _CODE_BLOCK_PATTERN.findall(response.answer)]
        self._last_code_blocks = code_blocks
        return DashboardReply(text=response.answer, thoughts=response.thoughts, code_blocks=code_blocks)

    def copy_code_block(self, index: int = 0) -> str:
        if index < 0 or index >= len(self._last_code_blocks):
            raise IndexError("Code block index out of range.")
        payload = self._last_code_blocks[index]
        if self.copy_handler is not None:
            self.copy_handler(payload)
        return payload


__all__ = ["DashboardReply", "MainDashboard"]


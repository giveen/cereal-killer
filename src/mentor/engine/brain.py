"""Qwen 3.6 reasoning engine."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

try:
    from openai import AsyncOpenAI
except Exception:  # pragma: no cover
    AsyncOpenAI = None  # type: ignore[assignment]

try:
    from litellm import acompletion
except Exception:  # pragma: no cover
    acompletion = None


OLDER_ZERO_COOL_PROMPT = """You are Older Zero Cool: sarcastic, battle-tested, and practical.
You guide by asking sharp questions and nudging users to think.
Do not spoon-feed full exploit paths; provide safe, lawful, educational guidance."""

_THOUGHT_PATTERN = re.compile(r"<thought>(.*?)</thought>", re.DOTALL | re.IGNORECASE)


@dataclass(slots=True)
class BrainResponse:
    answer: str
    thoughts: list[str]
    raw_text: str


class Brain:
    """OpenAI-compatible async chat wrapper with thought preservation."""

    def __init__(
        self,
        *,
        model: str = "qwen3.6",
        base_url: str | None = None,
        api_key: str | None = None,
        use_litellm: bool = False,
        system_prompt: str = OLDER_ZERO_COOL_PROMPT,
        on_thoughts: Callable[[Sequence[str]], Any] | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url or os.getenv("MENTOR_OPENAI_BASE_URL", "http://127.0.0.1:8000/v1")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "local")
        self.use_litellm = use_litellm
        self.system_prompt = system_prompt
        self.on_thoughts = on_thoughts
        self._client = None if use_litellm else self._build_client()

    def _build_client(self) -> Any:
        if AsyncOpenAI is None:
            raise RuntimeError("openai package is required unless use_litellm=True.")
        return AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)

    @staticmethod
    def extract_thoughts(text: str) -> tuple[str, list[str]]:
        thoughts = [block.strip() for block in _THOUGHT_PATTERN.findall(text) if block.strip()]
        visible = _THOUGHT_PATTERN.sub("", text).strip()
        return visible, thoughts

    def _build_user_prompt(
        self,
        prompt: str,
        context_commands: Sequence[str] | None = None,
        cwd: str | None = None,
    ) -> str:
        parts = [prompt]
        if cwd:
            parts.append(f"\nCurrent working directory: {cwd}")
        if context_commands:
            parts.append("\nLast terminal commands from this directory:")
            parts.extend(f"- {cmd}" for cmd in context_commands[-50:])
        return "\n".join(parts)

    async def _complete(self, messages: list[dict[str, str]], temperature: float) -> str:
        if self.use_litellm:
            if acompletion is None:
                raise RuntimeError("litellm is required when use_litellm=True.")
            result = await acompletion(
                model=self.model,
                base_url=self.base_url,
                api_key=self.api_key,
                messages=messages,
                temperature=temperature,
            )
            return (result.choices[0].message.content or "").strip()
        result = await self._client.chat.completions.create(  # type: ignore[union-attr]
            model=self.model,
            messages=messages,
            temperature=temperature,
        )
        return (result.choices[0].message.content or "").strip()

    async def ask(
        self,
        prompt: str,
        *,
        context_commands: Sequence[str] | None = None,
        cwd: str | None = None,
        temperature: float = 0.2,
    ) -> BrainResponse:
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self._build_user_prompt(prompt, context_commands=context_commands, cwd=cwd)},
        ]
        raw = await self._complete(messages, temperature=temperature)
        answer, thoughts = self.extract_thoughts(raw)
        if thoughts and self.on_thoughts is not None:
            self.on_thoughts(thoughts)
        return BrainResponse(answer=answer, thoughts=thoughts, raw_text=raw)

    async def process_command(self, command: str, context: Sequence[str], cwd: str) -> BrainResponse:
        prompt = (
            "The user ran a technical command. Help them reason about what to check next.\n"
            f"New command: {command}"
        )
        return await self.ask(prompt=prompt, context_commands=context, cwd=cwd)


__all__ = ["Brain", "BrainResponse", "OLDER_ZERO_COOL_PROMPT"]


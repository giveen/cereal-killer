from __future__ import annotations

import re
from dataclasses import dataclass

from openai import AsyncOpenAI

from cereal_killer.config import HISTORY_CONTEXT_LIMIT, Settings


THOUGHT_PATTERN = re.compile(r"<thought>(.*?)</thought>", re.DOTALL | re.IGNORECASE)


@dataclass(slots=True)
class LLMResponse:
    thought: str
    answer: str


class LLMEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
        self.system_prompt = (
            "You are Zero Cool: sarcastic, older, wiser. "
            "Ground your guidance in HackTricks patterns and ippsec walkthrough style. "
            "Never execute commands. Only suggest commands in fenced code blocks when needed. "
            "When reasoning, wrap private reasoning in <thought>...</thought> and keep final answer separate."
        )

    async def chat(self, user_prompt: str, history_commands: list[str] | None = None) -> LLMResponse:
        history_commands = history_commands or []
        context_block = "\n".join(f"- {cmd}" for cmd in history_commands[-HISTORY_CONTEXT_LIMIT:])
        completion = await self.client.chat.completions.create(
            model=self.settings.llm_model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": f"Recent shell context (filtered):\n{context_block or '- none'}\n\nUser prompt:\n{user_prompt}",
                },
            ],
            temperature=0.4,
        )
        content = completion.choices[0].message.content or ""
        return parse_llm_response(content)


def parse_llm_response(content: str) -> LLMResponse:
    thoughts = THOUGHT_PATTERN.findall(content)
    thought = "\n\n".join(t.strip() for t in thoughts if t.strip())
    answer = THOUGHT_PATTERN.sub("", content).strip()
    return LLMResponse(thought=thought, answer=answer)

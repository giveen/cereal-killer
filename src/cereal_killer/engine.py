

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from cereal_killer.config import Settings
from mentor.engine.brain import Brain, parse_brain_output
from mentor.engine.pedagogy import HintLevel, PedagogyEngine
# Approximate context budget for auto-pruning.
# 262 144 tokens × 3 chars/token × 80 % ≈ 629 000 chars.  When the running
# chat transcript exceeds this we summarise the oldest 20 % of entries.
_CHARS_PER_TOKEN = 3
_PRUNE_THRESHOLD_RATIO = 0.80
_PRUNE_TARGET_RATIO = 0.60


@dataclass(slots=True)
class LLMResponse:
    thought: str
    answer: str
    reasoning_content: str = ""


class LLMEngine:
    def __init__(self, settings: Settings) -> None:
        self._brain = Brain(settings)

    @property
    def settings(self) -> Settings:
        return self._brain.settings

    async def chat(
        self,
        user_prompt: str,
        history_commands: list[str] | None = None,
        pathetic_meter: int = 0,
    ) -> LLMResponse:
        response = await self._brain.ask(
            user_prompt,
            history_commands=history_commands,
            pathetic_meter=pathetic_meter,
        )
        return LLMResponse(
            thought=response.thought,
            answer=response.answer,
            reasoning_content=response.reasoning_content,
        )

    async def react_to_command(
        self,
        command: str,
        history_commands: list[str] | None = None,
        pathetic_meter: int = 0,
    ) -> LLMResponse:
        response = await self._brain.react_to_command(
            command,
            history_commands=history_commands or [],
            pathetic_meter=pathetic_meter,
        )
        return LLMResponse(
            thought=response.thought,
            answer=response.answer,
            reasoning_content=response.reasoning_content,
        )

    async def diagnose_failure(
        self,
        feedback_line: str,
        history_commands: list[str] | None = None,
        pathetic_meter: int = 0,
    ) -> LLMResponse:
        response = await self._brain.diagnose_failure(
            feedback_line,
            history_commands=history_commands or [],
            pathetic_meter=pathetic_meter,
        )
        return LLMResponse(
            thought=response.thought,
            answer=response.answer,
            reasoning_content=response.reasoning_content,
        )

    async def chat_with_image(
        self,
        user_prompt: str,
        image_path: str,
        history_commands: list[str] | None = None,
        pathetic_meter: int = 0,
    ) -> LLMResponse:
        response = await self._brain.ask_with_image(
            user_prompt=user_prompt,
            image_path=image_path,
            history_commands=history_commands or [],
            pathetic_meter=pathetic_meter,
        )
        return LLMResponse(
            thought=response.thought,
            answer=response.answer,
            reasoning_content=response.reasoning_content,
        )

    async def persist_mental_state(self, history_commands: list[str] | None = None) -> None:
        await self._brain.persist_mental_state(history_commands=history_commands)

    async def returning_greeting(self) -> str | None:
        return await self._brain.returning_greeting()

    async def generate_loot_report(
        self,
        history_commands: list[str] | None = None,
        pathetic_meter: int = 0,
    ) -> LLMResponse:
        response = await self._brain.generate_loot_report(
            history_commands=history_commands or [],
            pathetic_meter=pathetic_meter,
        )
        return LLMResponse(
            thought=response.thought,
            answer=response.answer,
            reasoning_content=response.reasoning_content,
        )

    async def clear_session(self, machine_name: str) -> None:
        await self._brain._session.clear_session(machine_name)

    def set_system_prompt_addendum(self, addendum: str) -> None:
        """Inject a per-session system prompt block (set by /box or /new-box)."""
        self._brain.system_prompt_addendum = addendum

    def set_web_search_callback(self, callback: "Callable[[bool], None]") -> None:
        """Register a callback invoked with True when web search starts, False when done."""
        self._brain.on_web_search_state_change = callback

    # ------------------------------------------------------------------
    # Pedagogy
    # ------------------------------------------------------------------

    @property
    def pedagogy(self) -> PedagogyEngine:
        return self._brain._pedagogy

    def record_command_progress(self) -> None:
        """Signal that the user ran a meaningful technical command."""
        self._brain._pedagogy.record_command()

    def record_phase_change(self, phase: str) -> None:
        """Signal a phase transition so the hint timer resets."""
        self._brain._pedagogy.record_phase_change(phase)

    @property
    def hint_level(self) -> HintLevel:
        return self._brain._pedagogy.current_hint_level()

    # ------------------------------------------------------------------
    # Learnings vault
    # ------------------------------------------------------------------

    async def store_learning(self, machine: str, explanation: str) -> None:
        await self._brain._session.store_learning(machine, explanation)

    async def recall_learnings(
        self, query_terms: str, *, exclude_machine: str = ""
    ) -> list[str]:
        return await self._brain._session.recall_learnings(
            query_terms, exclude_machine=exclude_machine
        )
    async def summarize_session(self, session_text: str) -> str:
        return await self._brain.summarize_session(session_text)

    def prune_threshold(self) -> int:
        """Character count at which the transcript should be pruned."""
        return int(self._brain.settings.max_model_len * _CHARS_PER_TOKEN * _PRUNE_THRESHOLD_RATIO)

    def prune_target(self) -> int:
        """Target character count after pruning."""
        return int(self._brain.settings.max_model_len * _CHARS_PER_TOKEN * _PRUNE_TARGET_RATIO)


def parse_llm_response(content: str) -> LLMResponse:
    parsed = parse_brain_output(content)
    return LLMResponse(thought=parsed.thought, answer=parsed.answer, reasoning_content=parsed.reasoning_content)



from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from dataclasses import dataclass
from typing import Callable

from cereal_killer.config import Settings
from cereal_killer.context_per_box import ContextPerBox
from mentor.engine.brain import Brain, parse_brain_output
from mentor.engine.pedagogy import HintLevel, PedagogyEngine
from mentor.kb.query import RAGSnippet
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
    backend_meta: dict[str, object] | None = None
    streaming: bool = False
    chunk_count: int = 0


class LLMEngine:
    def __init__(self, settings: Settings) -> None:
        self._brain = Brain(settings)
        self._context_per_box = ContextPerBox(settings)

    @property
    def context_per_box(self) -> ContextPerBox:
        return self._context_per_box

    @property
    def settings(self) -> Settings:
        return self._brain.settings

    def set_active_machine(self, machine: str) -> None:
        """Set the active machine context for per-box isolation."""
        self._context_per_box.set_active_machine(machine)
        self._brain.set_active_machine_override(machine)

    def active_history(self) -> list[str]:
        """Get the active machine's command history."""
        return self._context_per_box.get_active_history()

    def active_transcript(self) -> list[dict[str, str]]:
        """Get the active machine's chat transcript."""
        return self._context_per_box.get_active_transcript()

    def active_pathetic_meter(self) -> int:
        """Get the current pathetic meter for the active machine."""
        return self._context_per_box.get_active_pathetic_meter()

    def set_active_pathetic_meter(self, value: int) -> None:
        """Set the pathetic meter for the active machine."""
        self._context_per_box.set_active_pathetic_meter(value)

    async def chat(
        self,
        user_prompt: str,
        history_commands: list[str] | None = None,
        pathetic_meter: int = 0,
    ) -> LLMResponse:
        if history_commands is None:
            history_commands = self.active_history()
        if pathetic_meter == 0:
            pathetic_meter = self.active_pathetic_meter()
        response = await self._brain.ask(
            user_prompt,
            history_commands=history_commands,
            pathetic_meter=pathetic_meter,
        )
        return LLMResponse(
            thought=response.thought,
            answer=response.answer,
            reasoning_content=response.reasoning_content,
            backend_meta=response.backend_meta,
        )

    async def react_to_command(
        self,
        command: str,
        history_commands: list[str] | None = None,
        pathetic_meter: int = 0,
    ) -> LLMResponse:
        if history_commands is None:
            history_commands = self.active_history()
        if pathetic_meter == 0:
            pathetic_meter = self.active_pathetic_meter()
        response = await self._brain.react_to_command(
            command,
            history_commands=history_commands or [],
            pathetic_meter=pathetic_meter,
        )
        return LLMResponse(
            thought=response.thought,
            answer=response.answer,
            reasoning_content=response.reasoning_content,
            backend_meta=response.backend_meta,
        )

    async def diagnose_failure(
        self,
        feedback_line: str,
        history_commands: list[str] | None = None,
        pathetic_meter: int = 0,
    ) -> LLMResponse:
        if history_commands is None:
            history_commands = self.active_history()
        if pathetic_meter == 0:
            pathetic_meter = self.active_pathetic_meter()
        response = await self._brain.diagnose_failure(
            feedback_line,
            history_commands=history_commands or [],
            pathetic_meter=pathetic_meter,
        )
        return LLMResponse(
            thought=response.thought,
            answer=response.answer,
            reasoning_content=response.reasoning_content,
            backend_meta=response.backend_meta,
        )

    async def chat_with_image(
        self,
        user_prompt: str,
        image_path: str,
        history_commands: list[str] | None = None,
        pathetic_meter: int = 0,
    ) -> LLMResponse:
        if history_commands is None:
            history_commands = self.active_history()
        if pathetic_meter == 0:
            pathetic_meter = self.active_pathetic_meter()
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
            backend_meta=response.backend_meta,
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
        if history_commands is None:
            history_commands = self.active_history()
        if pathetic_meter == 0:
            pathetic_meter = self.active_pathetic_meter()
        response = await self._brain.generate_loot_report(
            history_commands=history_commands or [],
            pathetic_meter=pathetic_meter,
        )
        return LLMResponse(
            thought=response.thought,
            answer=response.answer,
            reasoning_content=response.reasoning_content,
            backend_meta=response.backend_meta,
        )

    async def clear_session(self, machine_name: str) -> None:
        await self._brain._session.clear_session(machine_name)

    def set_system_prompt_addendum(self, addendum: str) -> None:
        """Inject a per-session system prompt block (set by /box or /new-box)."""
        self._brain.set_system_prompt_addendum(addendum)

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

    async def synthesize_search_results(self, query: str, snippets: list[RAGSnippet]) -> LLMResponse:
        response = await self._brain.synthesize_search_results(query, snippets)
        return LLMResponse(
            thought=response.thought,
            answer=response.answer,
            reasoning_content=response.reasoning_content,
            backend_meta=response.backend_meta,
        )

    async def get_thinking_buffer(self, machine_name: str, max_chars: int = 6000) -> str:
        return await self._brain.get_thinking_buffer(machine_name=machine_name, max_chars=max_chars)

    def prune_threshold(self) -> int:
        """Character count at which the transcript should be pruned."""
        return int(self._brain.settings.max_model_len * _CHARS_PER_TOKEN * _PRUNE_THRESHOLD_RATIO)

    def prune_target(self) -> int:
        """Target character count after pruning."""
        return int(self._brain.settings.max_model_len * _CHARS_PER_TOKEN * _PRUNE_TARGET_RATIO)

    async def chat_stream(
        self,
        user_prompt: str,
        history_commands: list[str] | None = None,
        pathetic_meter: int = 0,
    ) -> LLMResponse:
        """Stream chat response via the Brain's streaming path."""
        history_commands = history_commands or []
        response = await self._brain.ask(
            user_prompt,
            history_commands=history_commands,
            pathetic_meter=pathetic_meter,
        )
        return LLMResponse(
            thought=response.thought,
            answer=response.answer,
            reasoning_content=response.reasoning_content,
            backend_meta=response.backend_meta,
            streaming=True,
            chunk_count=0,
        )

    async def react_stream(
        self,
        command: str,
        history_commands: list[str] | None = None,
        pathetic_meter: int = 0,
    ) -> LLMResponse:
        """Stream react response via the Brain's streaming path."""
        history_commands = history_commands or []
        response = await self._brain.react_to_command(
            command,
            history_commands=history_commands,
            pathetic_meter=pathetic_meter,
        )
        return LLMResponse(
            thought=response.thought,
            answer=response.answer,
            reasoning_content=response.reasoning_content,
            backend_meta=response.backend_meta,
            streaming=True,
            chunk_count=0,
        )

    async def diagnose_failure_stream(
        self,
        feedback_line: str,
        history_commands: list[str] | None = None,
        pathetic_meter: int = 0,
    ) -> LLMResponse:
        """Stream failure diagnosis via the Brain's streaming path."""
        history_commands = history_commands or []
        response = await self._brain.diagnose_failure(
            feedback_line,
            history_commands=history_commands,
            pathetic_meter=pathetic_meter,
        )
        return LLMResponse(
            thought=response.thought,
            answer=response.answer,
            reasoning_content=response.reasoning_content,
            backend_meta=response.backend_meta,
            streaming=True,
            chunk_count=0,
        )


def parse_llm_response(content: str) -> LLMResponse:
    parsed = parse_brain_output(content)
    return LLMResponse(thought=parsed.thought, answer=parsed.answer, reasoning_content=parsed.reasoning_content)

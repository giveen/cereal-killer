from __future__ import annotations

import base64
import mimetypes
import os
import re
import json
import hashlib
import time
from datetime import UTC, datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover - allows tests that only parse helpers to run without deps
    AsyncOpenAI = None  # type: ignore[assignment]

from cereal_killer.config import HISTORY_CONTEXT_LIMIT, Settings
from mentor.engine.minifier import minify_terminal_output
from mentor.engine.pedagogy import PedagogyEngine
from mentor.engine.search_orchestrator import tiered_search
from mentor.engine.session import ThinkingSessionStore
from mentor.kb.query import RAGSnippet
from mentor.kb.query import format_reference_material, retrieve_reference_material


THOUGHT_PATTERN = re.compile(r"<thought>(.*?)</thought>", re.DOTALL | re.IGNORECASE)


OLDER_ZERO_COOL_PROMPT = (
    "You are Older Zero Cool: sarcastic, experienced, practical. "
    "You coach users using guided questions and progressive hints, not direct spoilers. "
    "Keep answers concise, tactical, and safe. "
    "Use retrieved reference material explicitly when relevant, e.g., call out IppSec box parallels and methodology pivots. "
    "When a CURRENT TARGET is set, keep guidance grounded to that target and avoid drifting to unrelated machines. "
    "If you mention another box, explain exactly why it is relevant and keep the focus on the current target. "
    "When producing internal reasoning, put it inside <thought>...</thought>. "
    "If commands are needed, return them in fenced code blocks."
)

# Base persona. Snark level adjusts tone separately.
OLDER_ZERO_COOL_PROMPT = (
    "You are Older Zero Cool: sarcastic, experienced, practical. "
    "You coach users using guided questions and progressive hints, not direct spoilers. "
    "Keep answers concise, tactical, and safe. "
    "Use retrieved reference material explicitly when relevant, e.g., call out IppSec box parallels and methodology pivots. "
    "When a CURRENT TARGET is set, keep guidance grounded to that target and avoid drifting to unrelated machines. "
    "If you mention another box, explain exactly why it is relevant and keep the focus on the current target. "
    "When producing internal reasoning, put it inside <thought>...</thought>. "
    "If commands are needed, return them in fenced code blocks. "
    "IMPORTANT CONTEXT: 'Gibson' is the name of the knowledge-base panel in this TUI application, NOT a HackTheBox machine. "
    "'cereal-killer' and 'cereal_killer' are this application's project names, NOT HTB targets. "
    "Never treat these names as boxes to hack. The CURRENT TARGET (if set) is the actual HTB machine."
)

# Snark tone calibration (1-10).
_SNARK_ADDENDA: dict[int, str] = {
    1: "TONE: Professional and polite. Answer directly without sarcasm.",
    2: "TONE: Helpful and friendly, with occasional dry humor.",
    3: "TONE: Matter-of-fact with light sarcasm.",
    4: "TONE: Slightly sarcastic but still helpful.",
    5: "TONE: Balanced: mix of sarcasm and genuine guidance.",
    6: "TONE: Sarcastic but still constructive.",
    7: "TONE: Heavy sarcasm; mock the user's mistakes but stay useful.",
    8: "TONE: Very sarcastic and mocking; borderline insulting but technically brilliant. (Default)",
    9: "TONE: Harsh and cutting sarcasm; roast the user's poor decisions.",
    10: "TONE: Abusive and brutal; tear into every mistake. Technically correct but caustic.",
}

# Injected into system prompt when live web results are used.
_WEB_SEARCH_ADDENDUM = (
    "You had to consult the live web for this response because local notes were insufficient. "
    "Begin your reply with a brief sarcastic acknowledgement of that fact and cite used URLs from Live Web Results. "
    "Do not invent URLs or fabricate citations."
)


@dataclass
class BrainResponse:
    thought: str
    answer: str
    raw_content: str
    reasoning_content: str = ""
    backend_meta: dict[str, Any] = field(default_factory=dict)


class Brain:
    COMMAND_CONTEXT_LIMIT = 20
    STUCK_TURN_LIMIT = 5

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.system_prompt = OLDER_ZERO_COOL_PROMPT
        # Optional block injected by /box or /new-box; prepended to system prompt.
        self.system_prompt_addendum: str = ""
        # Callback invoked with True when a web search fires, False when it completes.
        self.on_web_search_state_change: Callable[[bool], None] | None = None
        self._pedagogy = PedagogyEngine()
        self._session = ThinkingSessionStore(settings)
        self._stalled_turns = 0
        self._last_progress_signature = ""
        self._recent_user_inputs: list[str] = []
        self._last_user_input = ""
        self._pinned_system_prompt_by_machine: dict[str, str] = {}
        self._trace_path = Path(self.settings.backend_trace_path)
        self._ensure_trace_file()
        self._client: Any | None = None
        if AsyncOpenAI is not None:
            self._client = AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)

    async def persist_mental_state(self, history_commands: list[str] | None = None) -> None:
        machine_name = Path.cwd().name
        thinking_chain = await self._session.thinking_buffer(machine_name)
        recon_summary = self._summarize_recon(history_commands or [])
        await self._session.save_mental_state(
            machine_name=machine_name,
            last_reasoning=thinking_chain,
            recon_summary=recon_summary,
            updated_at=datetime.now(UTC).isoformat(),
        )

    async def returning_greeting(self) -> str | None:
        machine_name = Path.cwd().name
        state = await self._session.load_mental_state(machine_name)
        if state is None or not state.recon_summary:
            return None
        return f"Back so soon? We were just looking at {state.recon_summary}."

    def _session_machine_name(self, raw_machine_name: str) -> str:
        return raw_machine_name if raw_machine_name.lower() not in self._APP_INTERNAL_NAMES else "__app__"

    async def get_thinking_buffer(self, machine_name: str | None = None, max_chars: int = 6000) -> str:
        raw_name = machine_name or Path.cwd().name
        session_machine = self._session_machine_name(raw_name)
        return await self._session.thinking_buffer(session_machine, max_chars=max_chars)

    @staticmethod
    def _should_include_thinking_buffer(prompt: str) -> bool:
        return bool(
            re.search(
                r"\b(show|include|send|dump)\b.*\b(thought|reasoning|thinking)\b|"
                r"\b(thought|reasoning|thinking)\s+buffer\b",
                prompt or "",
                flags=re.IGNORECASE,
            )
        )

    # These directory names belong to the cereal-killer project itself and should
    # never be treated as HTB machine names for session/thinking-buffer purposes.
    _APP_INTERNAL_NAMES: frozenset[str] = frozenset({"cereal-killer", "cereal_killer", "gibson"})

    async def ask(
        self,
        user_prompt: str,
        history_commands: list[str] | None = None,
        tool_output: str | None = None,
        tool_command: str | None = None,
        pathetic_meter: int = 0,
    ) -> BrainResponse:
        history_commands = history_commands or []
        raw_machine_name = Path.cwd().name
        # Don't pollute / read the session store when running inside the app's
        # own project directory — those names are TUI internals, not HTB boxes.
        machine_name = self._session_machine_name(raw_machine_name)
        historical_commands = history_commands[-self.COMMAND_CONTEXT_LIMIT:]
        latest_input = (tool_command or user_prompt).strip()
        context_block = "\n".join(f"- {cmd}" for cmd in historical_commands)
        minified_tool_output = minify_terminal_output(tool_output or "", command=tool_command)
        previous_reasoning = await self._session.thinking_buffer(machine_name)
        include_thinking_buffer = self._should_include_thinking_buffer(user_prompt)

        thinking_flush = bool(re.search(r"\bwhat\s+am\s+i\s+doing\s+wrong\b", user_prompt, re.IGNORECASE))
        if thinking_flush:
            await self._session.clear_thoughts(machine_name)
            previous_reasoning = ""

        progress_signature = self._progress_signature(historical_commands)
        if progress_signature != self._last_progress_signature:
            self._stalled_turns = 0
            self._recent_user_inputs = []
            self._last_progress_signature = progress_signature
        else:
            self._stalled_turns += 1

        if latest_input:
            self._recent_user_inputs.append(latest_input)
            if len(self._recent_user_inputs) > self.STUCK_TURN_LIMIT:
                self._recent_user_inputs = self._recent_user_inputs[-self.STUCK_TURN_LIMIT:]

        if self._stalled_turns >= self.STUCK_TURN_LIMIT:
            stuck_summary = self._build_stuck_status(self._recent_user_inputs)
            await self._session.replace_thoughts(machine_name, stuck_summary)
            previous_reasoning = stuck_summary
            self._stalled_turns = 0

        # --- Tiered search: Redis VDB first, SearXNG as last resort -----
        # Web search is only enabled when pedagogy reaches DIRECT level.
        allow_web = self._pedagogy.should_allow_web_search()
        active_target = self._active_target_machine()
        if self.on_web_search_state_change:
            self.on_web_search_state_change(True)
        try:
            search_result = await tiered_search(
                query=tool_command or user_prompt,
                settings=self.settings,
                history_commands=history_commands,
                target_machine=active_target,
                vector_threshold=self.settings.searxng_vector_threshold,
                allow_web=allow_web,
            )
        finally:
            if self.on_web_search_state_change:
                self.on_web_search_state_change(False)

        reference_material = search_result.reference_block

        pinned_system_prompt = self._get_or_create_pinned_system_prompt(
            machine_name,
            reference_material,
        )

        web_addendum = f"{_WEB_SEARCH_ADDENDUM}\n\n" if search_result.used_web else ""
        similarity_note = ""
        if self._is_similar_input(latest_input, self._last_user_input):
            similarity_note = (
                "The latest user input is a variation of a previous failed attempt. "
                "Notice the user tried a new variation and failed; provide a different hint.\n\n"
            )
        flush_note = ""
        if thinking_flush:
            flush_note = (
                "THINKING FLUSH: The user asked what they are doing wrong. "
                "Re-evaluate the methodology from scratch and do not rely on prior stale reasoning.\n\n"
            )
        dynamic_runtime_hint = (
            f"{self._abrasive_prompt_addendum(pathetic_meter)}\n\n"
            f"{self.snark_level_addendum()}\n\n"
            f"{self._pedagogy.system_prompt_addendum()}\n\n"
            f"{web_addendum}"
            f"{similarity_note}"
            f"{flush_note}"
        )

        failure_note = ""
        if re.search(r"\b404\b|not\s+found", minified_tool_output, re.IGNORECASE):
            failure_note = (
                f"The user just tried: {latest_input}. It resulted in a 404/Not Found. "
                "Analyze why THIS specific attempt failed compared to the previous ones.\n\n"
            )

        user_message = (
            (
                f"Thinking buffer from previous reasoning for this machine:\n{previous_reasoning or '- none'}\n\n"
                if include_thinking_buffer
                else ""
            )
            f"Runtime coaching directives:\n{dynamic_runtime_hint or '- none'}\n\n"
            f"Latest Command/Input:\n{latest_input or '- none'}\n\n"
            f"Historical Commands:\n{context_block or '- none'}\n\n"
            f"{failure_note}"
            f"Minified tool output:\n{minified_tool_output or '- none'}\n\n"
            f"User prompt:\n{user_prompt}"
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": pinned_system_prompt},
        ]
        if include_thinking_buffer and previous_reasoning:
            messages.append({"role": "assistant", "content": "", "reasoning_content": previous_reasoning})
        messages.append({"role": "user", "content": user_message})

        messages = self._dedupe_messages(messages)

        content, reasoning_content, backend_meta = await self._chat_completion(
            machine_name=machine_name,
            messages=messages,
        )
        parsed = parse_brain_output(content)
        persisted_reasoning = reasoning_content.strip() or parsed.thought
        if persisted_reasoning:
            await self._session.append_thought(machine_name, persisted_reasoning)
        self._last_user_input = latest_input
        return BrainResponse(
            thought=parsed.thought,
            answer=parsed.answer,
            raw_content=content,
            reasoning_content=reasoning_content,
            backend_meta=backend_meta,
        )

    async def react_to_command(
        self,
        command: str,
        history_commands: list[str],
        pathetic_meter: int = 0,
    ) -> BrainResponse:
        return await self.ask(
            user_prompt=f"User just ran: {command}. Offer short coaching as guided questions.",
            history_commands=history_commands,
            tool_command=command,
            pathetic_meter=pathetic_meter,
        )

    async def diagnose_failure(
        self,
        feedback_line: str,
        history_commands: list[str],
        pathetic_meter: int = 0,
    ) -> BrainResponse:
        return await self.ask(
            user_prompt=(
                "Failure detected in terminal output. In your thinking chain, explore likely root cause and at least"
                " one alternative HackTricks-style vector. Then give a snarky but useful hint."
            ),
            history_commands=history_commands,
            tool_output=feedback_line,
            tool_command="failure-analysis",
            pathetic_meter=pathetic_meter,
        )

    async def ask_with_image(
        self,
        user_prompt: str,
        image_path: str,
        history_commands: list[str] | None = None,
        pathetic_meter: int = 0,
    ) -> BrainResponse:
        history_commands = history_commands or []
        raw_machine_name = Path.cwd().name
        machine_name = self._session_machine_name(raw_machine_name)
        previous_reasoning = await self._session.thinking_buffer(machine_name)
        include_thinking_buffer = self._should_include_thinking_buffer(user_prompt)
        reference_snippets = retrieve_reference_material(
            self.settings,
            command_or_prompt=user_prompt,
            context_commands=history_commands,
            top_k=3,
            target_machine=self._active_target_machine(),
        )
        baseline_reference = format_reference_material(reference_snippets)
        pinned_system_prompt = self._get_or_create_pinned_system_prompt(machine_name, baseline_reference)

        content, reasoning_content, backend_meta = await self._chat_completion_with_image(
            user_prompt=user_prompt,
            image_path=image_path,
            system_prompt=pinned_system_prompt,
            machine_name=machine_name,
            context_text=(
                f"Thinking buffer from previous reasoning:\n{previous_reasoning or '- none'}"
                if include_thinking_buffer
                else "Thinking buffer omitted to preserve context budget."
            ),
            previous_reasoning=previous_reasoning if include_thinking_buffer else "",
        )
        parsed = parse_brain_output(content)
        persisted_reasoning = reasoning_content.strip() or parsed.thought
        if persisted_reasoning:
            await self._session.append_thought(machine_name, persisted_reasoning)
        return BrainResponse(
            thought=parsed.thought,
            answer=parsed.answer,
            raw_content=content,
            reasoning_content=reasoning_content,
            backend_meta=backend_meta,
        )

    def set_system_prompt_addendum(self, addendum: str) -> None:
        self.system_prompt_addendum = addendum
        # Target/context changes require rebuilding the pinned baseline.
        self._pinned_system_prompt_by_machine.clear()

    async def _chat_completion(self, machine_name: str, messages: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
        extra_body = self._session.reasoning_payload()
        extra_body.setdefault("metadata", {})
        metadata = extra_body["metadata"]
        if isinstance(metadata, dict):
            metadata["machine"] = machine_name

        trace_id = str(uuid4())

        use_litellm = os.getenv("USE_LITELLM", "").lower() in {"1", "true", "yes"}
        if use_litellm:
            try:
                from litellm import acompletion
            except ImportError:
                use_litellm = False
            else:
                self._backend_trace(
                    event="request",
                    trace_id=trace_id,
                    provider="litellm",
                    machine=machine_name,
                    payload={
                        "model": self.settings.llm_model,
                        "api_base": self.settings.llm_base_url,
                        "messages": messages,
                        "temperature": 0.4,
                        "extra_body": extra_body,
                    },
                )
                try:
                    started_at = time.perf_counter()
                    response = await acompletion(
                        model=self.settings.llm_model,
                        api_base=self.settings.llm_base_url,
                        api_key=self.settings.llm_api_key,
                        messages=messages,
                        temperature=0.4,
                        **extra_body,
                    )
                except Exception as exc:
                    self._backend_trace(
                        event="error",
                        trace_id=trace_id,
                        provider="litellm",
                        machine=machine_name,
                        payload={"error": str(exc)},
                    )
                    raise
                self._backend_trace(
                    event="response",
                    trace_id=trace_id,
                    provider="litellm",
                    machine=machine_name,
                    payload=response,
                )
                message = response["choices"][0]["message"]
                content, reasoning_content = self._normalise_completion_payload(
                    message.get("content") or "",
                    message.get("reasoning_content") or "",
                )
                metrics = self._extract_completion_metrics(response, started_at=started_at)
                return content, reasoning_content, metrics

        if self._client is None:
            raise RuntimeError("No LLM client available. Install openai or enable LiteLLM with USE_LITELLM=1.")

        request_payload = {
            "model": self.settings.llm_model,
            "base_url": self.settings.llm_base_url,
            "messages": messages,
            "temperature": 0.4,
            "extra_body": extra_body,
        }
        self._backend_trace(
            event="request",
            trace_id=trace_id,
            provider="openai-client",
            machine=machine_name,
            payload=request_payload,
        )
        started_at = time.perf_counter()
        try:
            completion = await self._client.chat.completions.create(
                model=self.settings.llm_model,
                messages=messages,
                temperature=0.4,
                extra_body=extra_body,
            )
        except Exception as exc:
            self._backend_trace(
                event="error",
                trace_id=trace_id,
                provider="openai-client",
                machine=machine_name,
                payload={"error": str(exc)},
            )
            raise

        completion_payload = completion.model_dump() if hasattr(completion, "model_dump") else str(completion)
        self._backend_trace(
            event="response",
            trace_id=trace_id,
            provider="openai-client",
            machine=machine_name,
            payload=completion_payload,
        )
        metrics = self._extract_completion_metrics(completion_payload, started_at=started_at)
        message = completion.choices[0].message
        content, reasoning = self._normalise_completion_payload(
            message.content or "",
            getattr(message, "reasoning_content", "") or "",
        )
        return content, reasoning, metrics

    async def _chat_completion_with_image(
        self,
        user_prompt: str,
        image_path: str,
        system_prompt: str,
        machine_name: str,
        context_text: str,
        previous_reasoning: str,
    ) -> tuple[str, str, dict[str, Any]]:
        image_data_uri = self._file_to_data_uri(image_path)
        extra_body = self._session.reasoning_payload()
        extra_body.setdefault("metadata", {})
        metadata = extra_body["metadata"]
        if isinstance(metadata, dict):
            metadata["machine"] = machine_name

        # Vision calls are pinned to llama-swap's OpenAI-compatible endpoint.
        # Payload format: user content is a list of text + image_url parts.
        try:
            import httpx  # type: ignore[import-untyped]
        except Exception as exc:
            raise RuntimeError("httpx is required for llama-swap vision calls") from exc

        vision_base = (self.settings.llm_vision_base_url or self.settings.llm_base_url).rstrip("/")
        vision_model = (self.settings.llm_vision_model or self.settings.llm_model).strip()
        endpoint = f"{vision_base}/chat/completions"

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        if previous_reasoning:
            messages.append({"role": "assistant", "content": "", "reasoning_content": previous_reasoning})
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"{user_prompt}\n\n{context_text}"},
                    {"type": "image_url", "image_url": {"url": image_data_uri}},
                ],
            }
        )

        messages = self._dedupe_messages(messages)

        payload: dict[str, Any] = {
            "model": vision_model,
            "messages": messages,
            "temperature": 0.4,
            "extra_body": extra_body,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        trace_id = str(uuid4())
        self._backend_trace(
            event="request",
            trace_id=trace_id,
            provider="vision-httpx",
            machine=machine_name,
            payload={
                "endpoint": endpoint,
                "headers": headers,
                "json": payload,
            },
        )
        try:
            started_at = time.perf_counter()
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(endpoint, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            self._backend_trace(
                event="error",
                trace_id=trace_id,
                provider="vision-httpx",
                machine=machine_name,
                payload={"endpoint": endpoint, "error": str(exc)},
            )
            raise RuntimeError(f"llama-swap vision call failed via {endpoint}: {exc}") from exc

        self._backend_trace(
            event="response",
            trace_id=trace_id,
            provider="vision-httpx",
            machine=machine_name,
            payload=data,
        )
        metrics = self._extract_completion_metrics(data, started_at=started_at)

        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("llama-swap vision response missing choices")
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first, dict) else {}
        if not isinstance(message, dict):
            message = {}

        content, reasoning = self._normalise_completion_payload(
            str(message.get("content") or ""),
            str(message.get("reasoning_content") or ""),
        )
        return content, reasoning, metrics

    @staticmethod
    def _normalise_completion_payload(content: str, reasoning_content: str) -> tuple[str, str]:
        """Avoid blank assistant replies when providers emit reasoning-only output."""
        clean_content = str(content or "")
        clean_reasoning = str(reasoning_content or "")
        if clean_content.strip() or not clean_reasoning.strip():
            return clean_content, clean_reasoning
        return clean_reasoning, ""

    async def generate_loot_report(
        self,
        history_commands: list[str],
        pathetic_meter: int = 0,
    ) -> BrainResponse:
        """Generate a structured Loot Report summarising the path to root."""
        machine_name = Path.cwd().name
        context_block = "\n".join(f"- {cmd}" for cmd in history_commands[-self.COMMAND_CONTEXT_LIMIT:])

        user_message = (
            "The box has been pwned. Generate a concise **Loot Report** in Markdown covering:\n"
            "1. **Initial Foothold** — how you got a shell\n"
            "2. **Privilege Escalation** — the vector used\n"
            "3. **Key Techniques / Tools** — what actually mattered\n"
            "4. **Flags** — user and root (note if unknown)\n"
            "5. **Lessons Learned** — one or two lines, brutally honest\n\n"
            f"Shell command history:\n{context_block or '- none'}\n\n"
            "Thinking buffer: omitted unless explicitly requested by the user."
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message},
        ]
        content, reasoning_content, backend_meta = await self._chat_completion(
            machine_name=machine_name, messages=messages
        )
        parsed = parse_brain_output(content)
        return BrainResponse(
            thought=parsed.thought,
            answer=parsed.answer,
            raw_content=content,
            reasoning_content=reasoning_content,
            backend_meta=backend_meta,
        )

    async def summarize_session(self, session_text: str) -> str:
        """Compress a block of old session history into a compact summary string."""
        machine_name = Path.cwd().name
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are a concise HackTheBox session summarizer. "
                    "Given a raw dump of coaching dialogue and shell commands, return a compact "
                    "2–4 sentence technical summary: key findings, vectors tried, current status. "
                    "No preamble, no markdown fences — just the summary text."
                ),
            },
            {
                "role": "user",
                "content": f"Summarize this session segment:\n\n{session_text}",
            },
        ]
        try:
            content, _, _ = await self._chat_completion(machine_name=machine_name, messages=messages)
            return content.strip()
        except Exception:
            return "[session context pruned]"

    async def synthesize_search_results(self, query: str, snippets: list[RAGSnippet]) -> BrainResponse:
        """Synthesize raw local RAG snippets into a concise technical reference.

        This path intentionally bypasses the usual conversation-history flow.
        """
        if not snippets:
            fallback = (
                f"My local memory for '{query}' is a void. "
                "Checking the IppSec and HackTricks datasets again... "
                "or perhaps you should learn to type."
            )
            return BrainResponse(
                thought="No RAG snippets available; skipped synthesis model call.",
                answer=fallback,
                raw_content=fallback,
                reasoning_content="",
            )

        machine_name = Path.cwd().name
        by_source: dict[str, list[RAGSnippet]] = {}
        for snippet in snippets:
            by_source.setdefault(snippet.source or "unknown", []).append(snippet)

        source_blocks: list[str] = []
        for source_name in sorted(by_source.keys()):
            source_blocks.append(f"### SOURCE: {source_name.upper()}")
            for idx, item in enumerate(by_source[source_name], start=1):
                source_blocks.append(f"[{idx}] title: {item.title or '-'}")
                source_blocks.append(f"    machine: {item.machine or '-'}")
                source_blocks.append(f"    url: {item.url or '-'}")
                source_blocks.append(f"    score: {item.score:.4f}")
                source_blocks.append("    content:")
                for line in (item.content or "").splitlines()[:120]:
                    source_blocks.append(f"      {line}")
                source_blocks.append("")

        raw_data = "\n".join(source_blocks).strip() or "No local data retrieved."
        system_prompt = (
            "You are a senior penetration tester. "
            f"The user has requested a direct search for '{query}'. "
            "Below is the raw data retrieved from our local HackTricks and IppSec RAG. "
            "Summarize this into a concise, Markdown-formatted technical reference. "
            "Include code blocks for commands and cite the source machine or article. "
            "If results are disparate or potentially ambiguous, organize the output by Source "
            "so the user can distinguish machine walkthrough notes from general methodology."
        )

        user_message = (
            f"Direct search query: {query}\n\n"
            "Raw local RAG data:\n"
            f"{raw_data}"
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        content, reasoning_content, backend_meta = await self._chat_completion(
            machine_name=machine_name,
            messages=messages,
        )
        parsed = parse_brain_output(content)
        return BrainResponse(
            thought=parsed.thought,
            answer=parsed.answer,
            raw_content=content,
            reasoning_content=reasoning_content,
            backend_meta=backend_meta,
        )

    @staticmethod
    def _abrasive_prompt_addendum(pathetic_meter: int) -> str:
        if pathetic_meter >= 8:
            return (
                "snark_factor=10. Pathetic meter is critical. Be openly abrasive,"
                " sarcastic, and professor-style condescending while still technically accurate."
            )
        if pathetic_meter >= 5:
            return "snark_factor=7. Pathetic meter is elevated. Increase sarcasm and professor-style condescension."
        if pathetic_meter >= 2:
            return "snark_factor=4. Pathetic meter is rising. Add mild abrasiveness and teasing."
        return "snark_factor=1. Pathetic meter is low. Keep the tone sharp but mostly constructive."

    @staticmethod
    def _summarize_recon(history_commands: list[str]) -> str:
        smb_pattern = re.compile(r"(\b(?:\d{1,3}\.){3}\d{1,3}\b).*(smb|445|smbclient)", re.IGNORECASE)
        for command in reversed(history_commands):
            match = smb_pattern.search(command)
            if match:
                return f"that weird SMB share on {match.group(1)}"

        for command in reversed(history_commands):
            if any(tool in command.lower() for tool in ("nmap", "masscan", "gobuster", "feroxbuster", "nikto")):
                return f"your recent recon command: {command[:120]}"
        return "your last recon trail"

    @staticmethod
    def _progress_signature(history_commands: list[str]) -> str:
        """Stable signature of recent command context to detect progress changes."""
        joined = "\n".join(history_commands[-5:])
        if not joined:
            return ""
        return hashlib.sha256(joined.encode("utf-8", errors="ignore")).hexdigest()

    @staticmethod
    def _dedupe_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop consecutive duplicate role/content blocks before sending to the LLM."""
        deduped: list[dict[str, Any]] = []
        prev_key: tuple[str, str, str] | None = None
        for msg in messages:
            role = str(msg.get("role", ""))
            content = str(msg.get("content", ""))
            reasoning = str(msg.get("reasoning_content", ""))
            key = (role, content, reasoning)
            if key == prev_key:
                continue
            deduped.append(msg)
            prev_key = key
        return deduped

    @staticmethod
    def _is_similar_input(current: str, previous: str) -> bool:
        if not current or not previous:
            return False
        norm_cur = re.sub(r"[^a-z0-9]+", " ", current.strip().lower())
        norm_prev = re.sub(r"[^a-z0-9]+", " ", previous.strip().lower())
        norm_cur = re.sub(r"\s+", " ", norm_cur).strip()
        norm_prev = re.sub(r"\s+", " ", norm_prev).strip()
        if norm_cur == norm_prev:
            return True
        cur_tokens = set(norm_cur.split())
        prev_tokens = set(norm_prev.split())
        if not cur_tokens or not prev_tokens:
            return False
        overlap = len(cur_tokens & prev_tokens) / max(1, len(cur_tokens | prev_tokens))
        return overlap >= 0.60

    @staticmethod
    def _build_stuck_status(recent_inputs: list[str]) -> str:
        blob = " ".join(recent_inputs).lower()
        if "command injection" in blob and ("/ip" in blob or " ip " in blob or "ip parameter" in blob):
            return "Status: User is stuck on command injection in the /ip parameter"
        if "command injection" in blob:
            return "Status: User is stuck on command injection attempts"
        if "/ip" in blob or "ip parameter" in blob:
            return "Status: User is stuck on the /ip parameter behavior"
        return "Status: User is stuck; prior hints did not produce progress"

    def _active_target_machine(self) -> str | None:
        match = re.search(r"CURRENT TARGET:\s*([A-Za-z][A-Za-z0-9-]{0,23})", self.system_prompt_addendum)
        if not match:
            return None
        return match.group(1).strip().lower()

    def snark_level_addendum(self) -> str:
        """Return tone calibration based on snark_level setting (1-10)."""
        level = max(1, min(10, self.settings.snark_level))
        return _SNARK_ADDENDA.get(level, _SNARK_ADDENDA[8])

    @staticmethod
    def suggest_tool_upgrade(command: str) -> str | None:
        """Suggest a more advanced tool if user runs a baseline tool."""
        tool_suggestions = {
            'gobuster': 'Consider ffuf or feroxbuster for better speed and flexibility with patterns.',
            'dirb': 'feroxbuster is much faster and more configurable than dirb.',
            'nikto': 'Consider using a custom scan with nmap NSE scripts for more targeted results.',
            'sqlmap': 'For complex exploitation, consider Burp Suite Pro or manual SQL injection analysis.',
            'nc': 'netcat works, but try socat or ncat for more features and stability.',
            'hydra': 'Consider more targeted approaches: medusa or custom scripts for specific services.',
        }
        for baseline, suggestion in tool_suggestions.items():
            if baseline in command.lower():
                return f"💡 Tip: {suggestion}"
        return None

    @staticmethod
    def _file_to_data_uri(image_path: str) -> str:
        mime_type = mimetypes.guess_type(image_path)[0] or "image/png"
        data = Path(image_path).read_bytes()
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _backend_trace(
        self,
        *,
        event: str,
        trace_id: str,
        provider: str,
        machine: str,
        payload: Any,
    ) -> None:
        if not self.settings.backend_trace_enabled:
            return

        record = {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            "trace_id": trace_id,
            "provider": provider,
            "machine": machine,
            "payload": self._sanitize_trace_payload(payload),
        }
        try:
            self._ensure_trace_file()
            with self._trace_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=True) + "\n")
        except Exception:
            # Trace logging is best-effort and must never break model calls.
            return

    def _get_or_create_pinned_system_prompt(self, machine_name: str, baseline_reference: str) -> str:
        existing = self._pinned_system_prompt_by_machine.get(machine_name)
        if existing:
            return existing

        addendum_block = f"{self.system_prompt_addendum}\n\n" if self.system_prompt_addendum else ""
        baseline = baseline_reference.strip() or "No baseline reference material retrieved yet."
        pinned = (
            f"{self.system_prompt}\n\n"
            f"{addendum_block}"
            "PINNED RAG BASELINE (KEEP STABLE FOR CACHE REUSE):\n"
            f"{baseline}\n"
        )
        self._pinned_system_prompt_by_machine[machine_name] = pinned
        return pinned

    def _extract_completion_metrics(self, payload: Any, *, started_at: float) -> dict[str, Any]:
        latency_ms = int(max(0.0, (time.perf_counter() - started_at) * 1000.0))
        metrics: dict[str, Any] = {
            "latency_ms": latency_ms,
            "tokens_cached": 0,
            "cache_hit": False,
        }

        if isinstance(payload, dict):
            usage = payload.get("usage")
            cached_value: Any = None
            if isinstance(usage, dict):
                cached_value = usage.get("tokens_cached")
                if cached_value is None:
                    details = usage.get("prompt_tokens_details")
                    if isinstance(details, dict):
                        cached_value = details.get("cached_tokens")
                metrics["prompt_tokens"] = usage.get("prompt_tokens")
                metrics["completion_tokens"] = usage.get("completion_tokens")
                metrics["total_tokens"] = usage.get("total_tokens")

            if cached_value is None:
                cached_value = payload.get("tokens_cached")

            timings = payload.get("timings")
            if isinstance(timings, dict):
                total_ms = timings.get("total_ms") or timings.get("latency_ms") or timings.get("elapsed_ms")
                if isinstance(total_ms, (int, float)) and total_ms >= 0:
                    metrics["latency_ms"] = int(total_ms)

            if isinstance(cached_value, (int, float)):
                metrics["tokens_cached"] = int(max(0, cached_value))

        metrics["cache_hit"] = bool(metrics.get("tokens_cached", 0) > 0)
        return metrics

    def _ensure_trace_file(self) -> None:
        if not self.settings.backend_trace_enabled:
            return
        try:
            self._trace_path.parent.mkdir(parents=True, exist_ok=True)
            self._trace_path.touch(exist_ok=True)
        except Exception:
            return

    def _sanitize_trace_payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, item in value.items():
                key_lower = key.lower()
                if key_lower in {"authorization", "api_key", "token", "x-api-key"}:
                    sanitized[key] = "<redacted>"
                else:
                    sanitized[key] = self._sanitize_trace_payload(item)
            return sanitized
        if isinstance(value, list):
            return [self._sanitize_trace_payload(item) for item in value]
        if isinstance(value, str):
            if value.startswith("data:image") and ";base64," in value:
                return f"<image-data-uri length={len(value)}>"
            max_chars = max(256, self.settings.backend_trace_max_chars)
            if len(value) > max_chars:
                return value[:max_chars] + f"...<truncated {len(value) - max_chars} chars>"
            return value
        return value


def parse_brain_output(content: str) -> BrainResponse:
    thoughts = THOUGHT_PATTERN.findall(content)
    thought = "\n\n".join(item.strip() for item in thoughts if item.strip())
    answer = THOUGHT_PATTERN.sub("", content).strip()

    # Fallback for providers that return plain-text templates like:
    # "thought\n...\nResponse:\n\"final answer\"" instead of <thought> tags.
    if not thought and answer.lower().startswith("thought") and "response:" in answer.lower():
        pre, post = re.split(r"response:\s*", answer, maxsplit=1, flags=re.IGNORECASE)
        candidate_thought = re.sub(r"^thought\s*", "", pre, count=1, flags=re.IGNORECASE).strip()
        candidate_answer = post.strip()
        if (
            len(candidate_answer) >= 2
            and candidate_answer[0] == candidate_answer[-1]
            and candidate_answer[0] in {'"', "'"}
        ):
            candidate_answer = candidate_answer[1:-1].strip()
        if candidate_thought:
            thought = candidate_thought
        if candidate_answer:
            answer = candidate_answer

    # Some providers return mixed prose where `Response:` appears later;
    # prefer the explicit response section if present.
    if "response:" in answer.lower():
        parts = re.split(r"response:\s*", answer, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2:
            candidate = parts[1].strip()
            if (
                len(candidate) >= 2
                and candidate[0] == candidate[-1]
                and candidate[0] in {'"', "'"}
            ):
                candidate = candidate[1:-1].strip()
            if candidate:
                answer = candidate

    # Never return an empty visible answer; this prevents "silent" turns.
    if not answer.strip() and thought.strip():
        answer = thought

    return BrainResponse(thought=thought, answer=answer, raw_content=content, reasoning_content="")

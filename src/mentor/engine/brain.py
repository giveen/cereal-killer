from __future__ import annotations

import base64
import mimetypes
import os
import re
import hashlib
from datetime import UTC, datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover - allows tests that only parse helpers to run without deps
    AsyncOpenAI = None  # type: ignore[assignment]

from cereal_killer.config import HISTORY_CONTEXT_LIMIT, Settings
from mentor.engine.minifier import minify_terminal_output
from mentor.engine.pedagogy import PedagogyEngine
from mentor.engine.search_orchestrator import tiered_search
from mentor.engine.session import ThinkingSessionStore
from mentor.kb.query import format_reference_material, retrieve_reference_material


THOUGHT_PATTERN = re.compile(r"<thought>(.*?)</thought>", re.DOTALL | re.IGNORECASE)


OLDER_ZERO_COOL_PROMPT = (
    "You are Older Zero Cool: sarcastic, experienced, practical. "
    "You coach users using guided questions and progressive hints, not direct spoilers. "
    "Keep answers concise, tactical, and safe. "
    "Use retrieved reference material explicitly when relevant, e.g., call out IppSec box parallels and methodology pivots. "
    "Say things like: 'IppSec handled this exact service in Monitor; check his approach to port 8080.' "
    "When producing internal reasoning, put it inside <thought>...</thought>. "
    "If commands are needed, return them in fenced code blocks."
)

# Injected into system prompt when live web results are used.
_WEB_SEARCH_ADDENDUM = (
    "You had to consult the live web for this response because local notes were insufficient. "
    "Begin your reply with a brief sarcastic acknowledgement of that fact — "
    "e.g., 'The local notes are dry. Hold on, let me see what the rest of the world thinks about this...' "
    "Always cite the source URLs from the Live Web Results block when you use that information. "
    "Do NOT invent URLs or fabricate citations."
)


@dataclass(slots=True)
class BrainResponse:
    thought: str
    answer: str
    raw_content: str
    reasoning_content: str


class Brain:
    COMMAND_CONTEXT_LIMIT = 20
    STUCK_TURN_LIMIT = 5

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.system_prompt = OLDER_ZERO_COOL_PROMPT
        # Optional block injected by /box or /new-box; prepended to system prompt.
        self.system_prompt_addendum: str = ""
        # Callback invoked with True when a web search fires, False when it completes.
        # Set by the UI to drive the live-web indicator.
        self.on_web_search_state_change: "Callable[[bool], None] | None" = None
        # Socratic state machine — tracks how long the user has been stuck.
        self._pedagogy = PedagogyEngine()
        self._session = ThinkingSessionStore(settings)
        self._stalled_turns = 0
        self._last_progress_signature = ""
        self._recent_user_inputs: list[str] = []
        self._last_user_input = ""
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

    async def ask(
        self,
        user_prompt: str,
        history_commands: list[str] | None = None,
        tool_output: str | None = None,
        tool_command: str | None = None,
        pathetic_meter: int = 0,
    ) -> BrainResponse:
        history_commands = history_commands or []
        machine_name = Path.cwd().name
        historical_commands = history_commands[-self.COMMAND_CONTEXT_LIMIT:]
        latest_input = (tool_command or user_prompt).strip()
        context_block = "\n".join(f"- {cmd}" for cmd in historical_commands)
        minified_tool_output = minify_terminal_output(tool_output or "", command=tool_command)
        previous_reasoning = await self._session.thinking_buffer(machine_name)

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

        addendum_block = f"{self.system_prompt_addendum}\n\n" if self.system_prompt_addendum else ""
        web_addendum = f"{_WEB_SEARCH_ADDENDUM}\n\n" if search_result.used_web else ""
        pedagogy_block = f"{self._pedagogy.system_prompt_addendum()}\n\n"
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
        system_prompt = (
            f"{self.system_prompt}\n\n"
            f"{self._abrasive_prompt_addendum(pathetic_meter)}\n\n"
            f"{pedagogy_block}"
            f"{addendum_block}"
            f"{web_addendum}"
            f"{similarity_note}"
            f"{flush_note}"
            f"{reference_material}"
        )

        failure_note = ""
        if re.search(r"\b404\b|not\s+found", minified_tool_output, re.IGNORECASE):
            failure_note = (
                f"The user just tried: {latest_input}. It resulted in a 404/Not Found. "
                "Analyze why THIS specific attempt failed compared to the previous ones.\n\n"
            )

        user_message = (
            f"Thinking buffer from previous reasoning for this machine:\n{previous_reasoning or '- none'}\n\n"
            f"Latest Command/Input:\n{latest_input or '- none'}\n\n"
            f"Historical Commands:\n{context_block or '- none'}\n\n"
            f"{failure_note}"
            f"Minified tool output:\n{minified_tool_output or '- none'}\n\n"
            f"User prompt:\n{user_prompt}"
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        if previous_reasoning:
            messages.append({"role": "assistant", "content": "", "reasoning_content": previous_reasoning})
        messages.append({"role": "user", "content": user_message})

        messages = self._dedupe_messages(messages)

        content, reasoning_content = await self._chat_completion(
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
        machine_name = Path.cwd().name
        previous_reasoning = await self._session.thinking_buffer(machine_name)
        reference_snippets = retrieve_reference_material(
            self.settings,
            command_or_prompt=user_prompt,
            context_commands=history_commands,
            top_k=3,
            target_machine=self._active_target_machine(),
        )
        system_prompt = (
            f"{self.system_prompt}\n\n"
            f"{self._abrasive_prompt_addendum(pathetic_meter)}\n\n"
            f"{format_reference_material(reference_snippets)}"
        )

        content, reasoning_content = await self._chat_completion_with_image(
            user_prompt=user_prompt,
            image_path=image_path,
            system_prompt=system_prompt,
            machine_name=machine_name,
            context_text=f"Thinking buffer from previous reasoning:\n{previous_reasoning or '- none'}",
            previous_reasoning=previous_reasoning,
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
        )

    async def _chat_completion(self, machine_name: str, messages: list[dict[str, Any]]) -> tuple[str, str]:
        extra_body = self._session.reasoning_payload()
        extra_body.setdefault("metadata", {})
        metadata = extra_body["metadata"]
        if isinstance(metadata, dict):
            metadata["machine"] = machine_name

        use_litellm = os.getenv("USE_LITELLM", "").lower() in {"1", "true", "yes"}
        if use_litellm:
            try:
                from litellm import acompletion
            except ImportError:
                use_litellm = False
            else:
                response = await acompletion(
                    model=self.settings.llm_model,
                    api_base=self.settings.llm_base_url,
                    api_key=self.settings.llm_api_key,
                    messages=messages,
                    temperature=0.4,
                    **extra_body,
                )
                message = response["choices"][0]["message"]
                content, reasoning_content = self._normalise_completion_payload(
                    message.get("content") or "",
                    message.get("reasoning_content") or "",
                )
                return content, reasoning_content

        if self._client is None:
            raise RuntimeError("No LLM client available. Install openai or enable LiteLLM with USE_LITELLM=1.")

        completion = await self._client.chat.completions.create(
            model=self.settings.llm_model,
            messages=messages,
            temperature=0.4,
            extra_body=extra_body,
        )
        message = completion.choices[0].message
        return self._normalise_completion_payload(
            message.content or "",
            getattr(message, "reasoning_content", "") or "",
        )

    async def _chat_completion_with_image(
        self,
        user_prompt: str,
        image_path: str,
        system_prompt: str,
        machine_name: str,
        context_text: str,
        previous_reasoning: str,
    ) -> tuple[str, str]:
        image_data_uri = self._file_to_data_uri(image_path)
        extra_body = self._session.reasoning_payload()
        extra_body.setdefault("metadata", {})
        metadata = extra_body["metadata"]
        if isinstance(metadata, dict):
            metadata["machine"] = machine_name

        if self._client is None:
            raise RuntimeError("No LLM client available for multimodal call.")

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

        completion = await self._client.chat.completions.create(
            model=self.settings.llm_model,
            messages=messages,
            temperature=0.4,
            extra_body=extra_body,
        )
        message = completion.choices[0].message
        return self._normalise_completion_payload(
            message.content or "",
            getattr(message, "reasoning_content", "") or "",
        )

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
        previous_reasoning = await self._session.thinking_buffer(machine_name)

        user_message = (
            "The box has been pwned. Generate a concise **Loot Report** in Markdown covering:\n"
            "1. **Initial Foothold** — how you got a shell\n"
            "2. **Privilege Escalation** — the vector used\n"
            "3. **Key Techniques / Tools** — what actually mattered\n"
            "4. **Flags** — user and root (note if unknown)\n"
            "5. **Lessons Learned** — one or two lines, brutally honest\n\n"
            f"Shell command history:\n{context_block or '- none'}\n\n"
            f"Thinking buffer:\n{previous_reasoning or '- none'}"
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message},
        ]
        content, reasoning_content = await self._chat_completion(
            machine_name=machine_name, messages=messages
        )
        parsed = parse_brain_output(content)
        return BrainResponse(
            thought=parsed.thought,
            answer=parsed.answer,
            raw_content=content,
            reasoning_content=reasoning_content,
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
            content, _ = await self._chat_completion(machine_name=machine_name, messages=messages)
            return content.strip()
        except Exception:
            return "[session context pruned]"

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

    @staticmethod
    def _file_to_data_uri(image_path: str) -> str:
        mime_type = mimetypes.guess_type(image_path)[0] or "image/png"
        data = Path(image_path).read_bytes()
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"


def parse_brain_output(content: str) -> BrainResponse:
    thoughts = THOUGHT_PATTERN.findall(content)
    thought = "\n\n".join(item.strip() for item in thoughts if item.strip())
    answer = THOUGHT_PATTERN.sub("", content).strip()
    return BrainResponse(thought=thought, answer=answer, raw_content=content, reasoning_content="")

"""Chat and LLM response worker management."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cereal_killer.ui.app import CerealKillerApp

from mentor.ui.phase import detect_phase

from ..base import resolve_dashboard, update_llm_cache_metrics as _update_llm_cache_metrics_helper


class ChatWorkerManager:
    """Manages chat, loot, and LLM response workers.

    Extracted from CerealKillerApp to encapsulate chat/loot/LLM
    response logic.
    """

    def __init__(self, app: "CerealKillerApp") -> None:
        self._app = app

    def _worker_name(self, method_name: str) -> str:
        return f"{self.__class__.__name__}.{method_name}"

    @asynccontextmanager
    async def _managed_worker(self, method_name: str, active_tool: str):
        dashboard = self._dashboard()
        worker_name = self._worker_name(method_name)
        self._app._register_worker(worker_name, asyncio.current_task())
        self._app._analysis_busy(True)
        dashboard.set_active_tool(active_tool)
        try:
            yield dashboard
        finally:
            dashboard.set_active_tool("Idle")
            self._app._analysis_busy(False)
            self._app._unregister_worker(worker_name)

    async def _run_chat_worker(self, prompt: str) -> None:
        async with self._managed_worker("run_chat_worker", "Brain") as dashboard:
            machine_name = self._app._context_per_box.active_machine
            if machine_name:
                dashboard.set_phase(f"[{machine_name.upper()}]")
            try:
                await self._app._with_worker_cancellation(self._chat_body(prompt))
            except Exception as exc:
                dashboard.append_system(f"LLM error: {exc}", style="red")

    async def _chat_body(self, prompt: str) -> None:
        response = await self._app.engine.chat(
            prompt,
            self._app.active_history,
            pathetic_meter=self._app.engine.active_pathetic_meter(),
        )
        self._update_llm_cache_metrics(response.backend_meta)
        await self._consume_llm_response(response.answer, response.reasoning_content or response.thought)
        phase = detect_phase(self._app.active_history)
        self._dashboard().set_phase(phase)
        if "pwned" in prompt.lower() or "owned" in prompt.lower():
            self._app._save_session_snapshot("pwned-manual")

    async def _run_loot_worker(self) -> None:
        async with self._managed_worker("run_loot_worker", "Loot") as dashboard:
            machine_name = self._app.active_history[-1] if self._app.active_history else "UNKNOWN"
            dashboard.append_system(f"Generating loot report for {machine_name}...", style="bold green")
            machine_name = self._app._context_per_box.active_machine
            if machine_name:
                dashboard.set_phase(f"[{machine_name.upper()}]")
            try:
                await self._app._with_worker_cancellation(self._loot_body())
            except Exception as exc:
                dashboard.append_system(f"Loot report error: {exc}", style="red")

    async def _loot_body(self) -> None:
        response = await self._app.engine.generate_loot_report(
            history_commands=self._app.active_history,
            pathetic_meter=self._app.engine.active_pathetic_meter(),
        )
        self._update_llm_cache_metrics(response.backend_meta)
        await self._consume_llm_response(response.answer, response.reasoning_content or response.thought)

    def _update_llm_cache_metrics(self, backend_meta: dict[str, object] | None) -> None:
        _update_llm_cache_metrics_helper(self._app, backend_meta)

    async def _consume_llm_response(self, answer: str, thought: str) -> None:
        self._app._track_code_block(answer)
        self._app._warn_if_repetitive_response(answer)
        self._app._append_chat("assistant", answer)

        dashboard = self._dashboard()
        if dashboard is None:
            self._app.notify("Response ready, but dashboard is temporarily unavailable.", title="Zero Cool", severity="warning")
            return

        try:
            await self._safe_stream_thought(thought, dashboard=dashboard)
            dashboard.set_active_view("chat")
            dashboard.append_assistant(answer)
        except Exception as exc:
            try:
                dashboard.append_system(f"UI post-processing error: {exc}", style="red")
            except Exception:
                self._app.notify(f"Response ready (UI error: {exc})", title="Zero Cool", severity="warning")

    async def _safe_stream_thought(self, thought: str, *, dashboard: Any = None) -> None:
        active_dashboard = dashboard or self._dashboard()
        if active_dashboard is None:
            return
        stream_method = getattr(active_dashboard, "stream_thought", None)
        if callable(stream_method):
            await stream_method(thought)
            return
        thought_box_method = getattr(active_dashboard, "thought_box", None)
        if callable(thought_box_method):
            thought_box = thought_box_method()
            if thought_box is not None and hasattr(thought_box, "stream_thought"):
                await thought_box.stream_thought(thought)

    async def _run_stream_chat_worker(self, prompt: str) -> None:
        """Run streaming chat worker with callback support."""
        async with self._managed_worker("run_stream_chat_worker", "Brain") as dashboard:
            machine_name = self._app._context_per_box.active_machine
            if machine_name:
                dashboard.set_phase(f"[{machine_name.upper()}]")
            dashboard.show_streaming_ui()
            try:
                await self._stream_chat_body(prompt)
            except Exception as exc:
                dashboard.append_system(f"LLM error: {exc}", style="red")
            finally:
                dashboard.hide_streaming_ui()

    async def _stream_chat_body(self, prompt: str) -> None:
        """Execute streaming chat."""
        dashboard = self._dashboard()
        if dashboard is None:
            return

        response = await self._app.engine.chat_stream(
            prompt,
            self._app.active_history,
            pathetic_meter=self._app.engine.active_pathetic_meter(),
        )
        await self._app._with_worker_cancellation(self._stream_chat_body_final(response, dashboard))

    async def _stream_chat_body_final(self, response: Any, dashboard: Any) -> None:
        """Finalize streaming response after the engine completes."""
        self._update_llm_cache_metrics(response.backend_meta if hasattr(response, 'backend_meta') else None)
        await self._consume_llm_response(response.answer, response.thought)
        phase = detect_phase(self._app.active_history)
        dashboard.set_phase(phase)

    # Public aliases for delegation from app.py
    run_chat_worker = _run_chat_worker
    chat_body = _chat_body
    run_loot_worker = _run_loot_worker
    loot_body = _loot_body
    run_stream_chat_worker = _run_stream_chat_worker
    stream_chat_body = _stream_chat_body
    update_llm_cache_metrics = _update_llm_cache_metrics

    _dashboard = resolve_dashboard

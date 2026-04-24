"""Command execution pipeline for the cereal_killer app."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from cereal_killer.config import Settings


# Sentinel value to signal the drain loop to exit.
_SENTINEL = object()


@dataclass
class _QueueWrapper:
    """Internal helper to link a PipelineCommand to its resolution future."""

    cmd: PipelineCommand
    future: Any


@dataclass
class PipelineCommand:
    """A command to be executed by the pipeline."""

    command: str
    timeout: int = 30
    expect_output: bool = True
    feed_to_brain: bool = True
    context_key: str | None = None  # For per-box context


@dataclass
class PipelineResult:
    """Result of a pipeline command execution."""

    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    parsed_output: dict[str, Any] = field(default_factory=dict)
    feed_brain: bool = True


class CommandPipeline:
    """Manages asynchronous command execution with structured output parsing."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._queue: asyncio.Queue[PipelineCommand] = asyncio.Queue()
        self._running: bool = False
        self._drain_task: asyncio.Task[None] | None = None
        self._all_tasks: set[asyncio.Task[PipelineResult]] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch the background worker that drains the command queue."""
        if self._running:
            return
        self._running = True
        self._drain_task = asyncio.create_task(self._drain_loop())

    def stop(self) -> None:
        """Signal the worker to stop after finishing pending work."""
        self._running = False
        # Wake the queue so the loop exits cleanly.
        self._queue.put_nowait(_SENTINEL)  # type: ignore[name-defined]

    def enqueue(
        self, command: PipelineCommand
    ) -> asyncio.Task[PipelineResult]:
        """Add *command* to the queue and return the awaiting task.

        The returned task resolves to a ``PipelineResult``.
        """
        self._start_if_needed()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[PipelineResult] = loop.create_future()

        # Wrap the command so the drain loop knows how to resolve it.
        wrapper = _QueueWrapper(command, future)
        self._queue.put_nowait(wrapper)

        task = asyncio.create_task(
            self._resolve_wrapper(wrapper),
            name=f"cmd-pipeline-{command.command[:40]}",
        )
        self._all_tasks.add(task)
        task.add_done_callback(self._all_tasks.discard)
        return task

    async def execute(
        self, cmd: PipelineCommand
    ) -> PipelineResult:
        """Execute a single command synchronously (for testing / direct use).

        Bypasses the queue; useful for one-off executions.
        """
        return await self._run_command(cmd)

    def cancel_all(self) -> None:
        """Cancel every pending and in-flight command."""
        self._queue.put_nowait(_SENTINEL)  # type: ignore[name-defined]
        for task in list(self._all_tasks):
            task.cancel()
            task.cancel()  # cancel the drain loop sentinel
        # Also drain remaining items to unblock the drain loop.

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _start_if_needed(self) -> None:
        if not self._running:
            self.start()

    async def _drain_loop(self) -> None:
        """Background coroutine that drains the queue sequentially."""
        while True:
            item = await self._queue.get()
            if item is _SENTINEL:
                self._queue.task_done()
                break
            if isinstance(item, _QueueWrapper):
                wrapper: _QueueWrapper = item
                self._queue.task_done()
                result = await self._run_command(wrapper.cmd)
                if not wrapper.future.done():
                    wrapper.future.set_result(result)
            else:
                self._queue.task_done()

    async def _resolve_wrapper(
        self, wrapper: _QueueWrapper
    ) -> PipelineResult:
        """Wait for the drain-loop to resolve a queued wrapper."""
        return await wrapper.future

    async def _run_command(self, cmd: PipelineCommand) -> PipelineResult:
        """Run a single command via asyncio.subprocess and return a result."""
        stdout = ""
        stderr = ""
        exit_code = -1

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                raw_stdout, raw_stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=cmd.timeout
                )
                stdout = (raw_stdout or b"").decode("utf-8", errors="replace")
                stderr = (raw_stderr or b"").decode("utf-8", errors="replace")
                exit_code = proc.returncode
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                stdout = "[TIMED OUT]"
                stderr = f"Process killed after {cmd.timeout}s"
                exit_code = -1
        except OSError as exc:
            stderr = str(exc)
            exit_code = -1

        parsed_output: dict[str, Any] = {}
        if cmd.expect_output and stdout.strip():
            parsed_output = self._parse_output(stdout, cmd.command)

        return PipelineResult(
            command=cmd.command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            parsed_output=parsed_output,
            feed_brain=cmd.feed_to_brain,
        )

    def _parse_output(self, stdout: str, command: str) -> dict[str, Any]:
        """Attempt structured parsing of command output.

        Order of attempts:
        1. JSON
        2. XML
        3. CSV (with headers)
        4. Raw text fallback
        """
        stripped = stdout.strip()
        if not stripped:
            return {}

        # --- JSON ----------------------------------------------------
        try:
            return {"format": "json", "data": json.loads(stripped)}
        except (json.JSONDecodeError, ValueError):
            pass

        # --- XML -----------------------------------------------------
        try:
            root = ET.fromstring(stripped)
            result: dict[str, Any] = {"format": "xml"}
            result["xml"] = self._element_to_dict(root)
            return result
        except ET.ParseError:
            pass

        # --- CSV detection -------------------------------------------
        if self._looks_like_csv(stripped):
            rows = []
            reader = csv.reader(io.StringIO(stripped))
            for row in reader:
                rows.append(row)
            return {"format": "csv", "rows": rows}

        # --- Raw text fallback ---------------------------------------
        return {"format": "raw", "raw": stripped}

    # ------------------------------------------------------------------
    # XML helper
    # ------------------------------------------------------------------
    @staticmethod
    def _element_to_dict(elem):
        result: dict[str, Any] = {}
        for child in elem:
            tag = child.tag
            text = (child.text or "").strip()
            children = list(child)
            if children:
                parsed = CommandPipeline._element_to_dict(child)
                result[tag] = parsed
            elif text:
                result[tag] = text
            else:
                result[tag] = {}
        return result

    @staticmethod
    def _looks_like_csv(text: str) -> bool:
        """Heuristic: is *text* likely comma-separated with a header row?"""
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        if len(lines) < 2:
            return False
        # First line looks like a header (comma-separated identifiers).
        first = lines[0]
        if "," not in first:
            return False
        # Subsequent lines also contain commas → strong CSV signal.
        for line in lines[1:]:
            if "," not in line:
                return False
        return True

    @classmethod
    def is_structured(cls, text: str) -> bool:
        """Detect whether *text* appears to be structured data."""
        stripped = text.strip()
        if not stripped:
            return False

        # JSON
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                json.loads(stripped)
                return True
            except (json.JSONDecodeError, ValueError):
                pass

        # XML
        if stripped.startswith("<"):
            try:
                ET.fromstring(stripped)
                return True
            except ET.ParseError:
                pass

        # CSV
        if cls._looks_like_csv(stripped):
            return True

        return False

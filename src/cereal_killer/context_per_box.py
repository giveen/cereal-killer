"""Per-box context store for multi-machine workflow support.

Manages isolated chat histories, transcripts, and state per machine.
Uses Redis as a backing store with in-memory fallback when Redis
is unavailable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cereal_killer.config import Settings
from mentor.kb.redis_pool import get_sync_client


@dataclass(slots=True)
class BoxContext:
    """Holds the context state for a single machine/box.

    Attributes:
        history_context: Shell command history for this machine.
        chat_transcript: User/assistant conversation history.
        pathetic_meter: Snark calibration level (0-10).
        last_active: Timestamp of the last interaction.
    """

    history_context: list[str] = field(default_factory=list)
    chat_transcript: list[dict[str, str]] = field(default_factory=list)
    pathetic_meter: int = 0
    last_active: datetime = field(default_factory=lambda: datetime.now(UTC))


# Key format for Redis storage
_CONTEXT_KEY_PREFIX = "cereal_killer:context:"


def _serialize_context(ctx: BoxContext) -> str:
    """Serialize a BoxContext to a JSON string."""
    data = {
        "history_context": ctx.history_context,
        "chat_transcript": ctx.chat_transcript,
        "pathetic_meter": ctx.pathetic_meter,
        "last_active": ctx.last_active.isoformat() if ctx.last_active else None,
    }
    return json.dumps(data)


def _deserialize_context(raw: str) -> BoxContext:
    """Deserialize a JSON string into a BoxContext."""
    data = json.loads(raw)
    last_active_str = data.get("last_active")
    last_active = datetime.fromisoformat(last_active_str) if last_active_str else datetime.now(UTC)
    return BoxContext(
        history_context=data.get("history_context", []),
        chat_transcript=data.get("chat_transcript", []),
        pathetic_meter=data.get("pathetic_meter", 0),
        last_active=last_active,
    )


class ContextPerBox:
    """Manages isolated contexts per machine with Redis persistence.

    Each machine gets its own BoxContext with independent history,
    transcript, and pathetic meter state. Contexts are persisted to
    Redis and gracefully degrade to in-memory-only storage if Redis
    is unavailable.

    Args:
        settings: Application settings containing Redis configuration.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialize the context manager.

        Creates an in-memory context store and attempts to connect to
        Redis. If Redis is unavailable, contexts remain in-memory only.
        """
        self._settings = settings
        self._active_machine: str | None = None
        self._contexts: dict[str, BoxContext] = {}
        self._redis_client: Any = None

    def _client(self) -> Any:
        """Get or create the sync Redis client.

        Returns:
            A sync Redis client instance, or None if Redis is unavailable.
        """
        if self._redis_client is None:
            try:
                self._redis_client = get_sync_client(self._settings.redis_url)
            except Exception:
                self._redis_client = None
        return self._redis_client

    @property
    def active_machine(self) -> str | None:
        """Read-only access to the currently active machine name."""
        return self._active_machine

    def get_or_create(self, machine: str) -> BoxContext:
        """Get or create a context for a machine.

        If Redis is available, attempts to load from storage.
        Falls back to creating a new context if not found or Redis fails.

        Args:
            machine: The machine identifier.

        Returns:
            The BoxContext for the given machine.
        """
        if machine not in self._contexts:
            ctx = self._load_context(machine)
            if ctx is None:
                ctx = BoxContext()
            self._contexts[machine] = ctx
        return self._contexts[machine]

    def _load_context(self, machine: str) -> BoxContext | None:
        """Load a context from Redis.

        Args:
            machine: The machine identifier.

        Returns:
            A BoxContext if found and Redis is available, else None.
        """
        client = self._client()
        if client is None:
            return None

        try:
            raw = client.get(f"{_CONTEXT_KEY_PREFIX}{machine}")
            if raw is None:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return _deserialize_context(raw)
        except Exception:
            # Redis is available but something went wrong; fall back
            return None

    def _save_context(self, machine: str, ctx: BoxContext) -> None:
        """Save a context to Redis.

        Args:
            machine: The machine identifier.
            ctx: The BoxContext to save.
        """
        client = self._client()
        if client is None:
            return

        try:
            key = f"{_CONTEXT_KEY_PREFIX}{machine}"
            client.set(key, _serialize_context(ctx))
        except Exception:
            pass  # Redis unavailable; context remains in memory

    def _delete_context(self, machine: str) -> None:
        """Delete a context from Redis.

        Args:
            machine: The machine identifier.
        """
        client = self._client()
        if client is None:
            return

        try:
            key = f"{_CONTEXT_KEY_PREFIX}{machine}"
            client.delete(key)
        except Exception:
            pass

    def set_active_machine(self, machine: str) -> None:
        """Switch to the active context for a machine.

        Loads the context from Redis if available, or creates a new
        one if this machine hasn't been seen before.

        Args:
            machine: The machine identifier to set as active.
        """
        self._active_machine = machine
        if machine not in self._contexts:
            ctx = self._load_context(machine)
            if ctx is None:
                ctx = BoxContext()
            self._contexts[machine] = ctx
        self._contexts[machine].last_active = datetime.now(UTC)

    def get_active(self) -> BoxContext:
        """Get the current active context, creating one if needed.

        Returns:
            The BoxContext for the active machine.
        """
        if self._active_machine is None:
            raise RuntimeError("No active machine set. Call set_active_machine() first.")
        if self._active_machine not in self._contexts:
            ctx = self._load_context(self._active_machine)
            if ctx is None:
                ctx = BoxContext()
            self._contexts[self._active_machine] = ctx
        return self._contexts[self._active_machine]

    def get_active_history(self) -> list[str]:
        """Get the history context for the active machine.

        Returns:
            A list of shell command strings.
        """
        ctx = self.get_active()
        return ctx.history_context

    def get_active_transcript(self) -> list[dict[str, str]]:
        """Get the chat transcript for the active machine.

        Returns:
            A list of conversation turn dictionaries.
        """
        ctx = self.get_active()
        return ctx.chat_transcript

    def get_active_pathetic_meter(self) -> int:
        """Get the pathetic meter for the active machine.

        Returns:
            The current snark calibration level.
        """
        ctx = self.get_active()
        return ctx.pathetic_meter

    def set_active_pathetic_meter(self, value: int) -> None:
        """Set the pathetic meter for the active machine.

        Args:
            value: The snark calibration level to set.
        """
        ctx = self.get_active()
        ctx.pathetic_meter = value
        ctx.last_active = datetime.now(UTC)

    def save_active(self) -> None:
        """Persist the active context to Redis.

        Serializes the current active context and stores it in Redis.
        Silently ignores errors if Redis is unavailable.
        """
        if self._active_machine is None:
            return

        ctx = self.get_active()
        ctx.last_active = datetime.now(UTC)
        self._save_context(self._active_machine, ctx)

    def save_all(self) -> None:
        """Persist ALL contexts to Redis.

        Useful during shutdown. Iterates over all stored contexts and
        saves them to Redis. Silently ignores errors per-machine.
        """
        client = self._client()
        if client is None:
            return

        for machine, ctx in self._contexts.items():
            ctx.last_active = datetime.now(UTC)
            try:
                key = f"{_CONTEXT_KEY_PREFIX}{machine}"
                client.set(key, _serialize_context(ctx))
            except Exception:
                pass

    def clear_active(self) -> None:
        """Clear the current active context (new session).

        Replaces the active context with a fresh BoxContext and removes
        any saved version from Redis.
        """
        if self._active_machine is None:
            return

        self._contexts[self._active_machine] = BoxContext()

        # Remove from Redis if available
        self._delete_context(self._active_machine)

    def merge_history(self, commands: list[str]) -> None:
        """Append new commands to the active history.

        Adds the provided shell command strings to the active
        context's history.

        Args:
            commands: List of command strings to append.
        """
        if self._active_machine is None:
            return

        ctx = self.get_active()
        ctx.history_context.extend(commands)
        ctx.last_active = datetime.now(UTC)

    def set_active_history(self, commands: list[str]) -> None:
        """Replace the active history context with a fresh list."""
        if self._active_machine is None:
            return
        ctx = self.get_active()
        ctx.history_context = list(commands)
        ctx.last_active = datetime.now(UTC)

    def set_active_transcript(self, entries: list[dict[str, str]]) -> None:
        """Replace the active transcript with a fresh list."""
        if self._active_machine is None:
            return
        ctx = self.get_active()
        ctx.chat_transcript = list(entries)
        ctx.last_active = datetime.now(UTC)

from __future__ import annotations

import json
from dataclasses import dataclass

try:
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover - run without redis package in minimal environments
    Redis = None  # type: ignore[assignment]

from cereal_killer.config import Settings


@dataclass(slots=True)
class SessionMemory:
    machine_name: str
    thoughts: list[str]


@dataclass(slots=True)
class MentalState:
    machine_name: str
    last_reasoning: str
    recon_summary: str
    updated_at: str


class ThinkingSessionStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._redis: Redis | None = None

    async def _client(self) -> Redis | None:
        if Redis is None:
            return None
        if self._redis is None:
            self._redis = Redis.from_url(self.settings.redis_url, decode_responses=True)
        return self._redis

    @staticmethod
    def _key(machine_name: str) -> str:
        return f"mentor:session:{machine_name}:thoughts"

    @staticmethod
    def _mental_state_key(machine_name: str) -> str:
        return f"mentor:session:{machine_name}:mental-state"

    async def load_thoughts(self, machine_name: str, limit: int = 50) -> list[str]:
        client = await self._client()
        if client is None:
            return []
        try:
            values = await client.lrange(self._key(machine_name), -max(1, limit), -1)
            return [str(item) for item in values if str(item).strip()]
        except Exception:
            return []

    async def append_thought(self, machine_name: str, thought: str, keep_last: int = 200) -> None:
        clean = thought.strip()
        if not clean:
            return
        client = await self._client()
        if client is None:
            return
        key = self._key(machine_name)
        try:
            await client.rpush(key, clean)
            await client.ltrim(key, -keep_last, -1)
        except Exception:
            return

    async def cumulative_trace(self, machine_name: str, char_limit: int = 4000) -> str:
        thoughts = await self.load_thoughts(machine_name, limit=60)
        if not thoughts:
            return ""
        blob = "\n\n".join(thoughts)
        if len(blob) > char_limit:
            return blob[-char_limit:]
        return blob

    async def thinking_buffer(self, machine_name: str, max_chars: int = 6000) -> str:
        return await self.cumulative_trace(machine_name, char_limit=max_chars)

    async def save_mental_state(
        self,
        machine_name: str,
        last_reasoning: str,
        recon_summary: str,
        updated_at: str,
    ) -> None:
        client = await self._client()
        if client is None:
            return

        payload = {
            "machine_name": machine_name,
            "last_reasoning": last_reasoning,
            "recon_summary": recon_summary,
            "updated_at": updated_at,
        }
        try:
            await client.set(self._mental_state_key(machine_name), json.dumps(payload), ex=60 * 60 * 24 * 7)
        except Exception:
            return

    async def load_mental_state(self, machine_name: str) -> MentalState | None:
        client = await self._client()
        if client is None:
            return None

        try:
            raw = await client.get(self._mental_state_key(machine_name))
        except Exception:
            return None
        if not raw:
            return None

        try:
            payload = json.loads(raw)
        except Exception:
            return None

        return MentalState(
            machine_name=str(payload.get("machine_name", machine_name)),
            last_reasoning=str(payload.get("last_reasoning", "")),
            recon_summary=str(payload.get("recon_summary", "")),
            updated_at=str(payload.get("updated_at", "")),
        )

    async def clear_session(self, machine_name: str) -> None:
        """Delete all Redis keys for this machine (thoughts + mental state)."""
        client = await self._client()
        if client is None:
            return
        keys_to_delete = [
            self._key(machine_name),
            self._mental_state_key(machine_name),
        ]
        try:
            await client.delete(*keys_to_delete)
        except Exception:
            return

    # ------------------------------------------------------------------
    # User learnings vault  (key: user_learnings:<machine>)
    # ------------------------------------------------------------------

    @staticmethod
    def _learnings_key(machine_name: str) -> str:
        return f"user_learnings:{machine_name}"

    async def store_learning(
        self,
        machine_name: str,
        explanation: str,
        *,
        ttl: int = 60 * 60 * 24 * 365,  # 1 year
    ) -> None:
        """Persist a user-written vulnerability explanation to the learnings vault."""
        client = await self._client()
        if client is None:
            return
        import datetime as _dt
        payload = json.dumps(
            {
                "machine": machine_name,
                "explanation": explanation.strip(),
                "timestamp": _dt.datetime.now(_dt.UTC).isoformat(),
            }
        )
        key = self._learnings_key(machine_name)
        try:
            await client.rpush(key, payload)
            await client.expire(key, ttl)
        except Exception:
            return

    async def recall_learnings(
        self,
        query_terms: str,
        *,
        exclude_machine: str = "",
        limit: int = 20,
    ) -> list[str]:
        """Return past learning explanations whose text overlaps with *query_terms*.

        Simple keyword-overlap ranking — no separate vector index needed.
        """
        client = await self._client()
        if client is None:
            return []
        try:
            keys = [k async for k in client.scan_iter(match="user_learnings:*", count=100)]
        except Exception:
            return []

        terms = {w.lower() for w in query_terms.split() if len(w) > 3}
        scored: list[tuple[int, str]] = []

        for key in keys:
            try:
                entries = await client.lrange(str(key), 0, -1)
            except Exception:
                continue
            for raw in entries:
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                machine = str(data.get("machine", ""))
                if exclude_machine and machine == exclude_machine:
                    continue
                text = str(data.get("explanation", ""))
                score = sum(1 for t in terms if t in text.lower())
                if score > 0:
                    scored.append((score, f"[{machine.upper()}] {text}"))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:limit]]

    def reasoning_payload(self) -> dict[str, object]:
        return {
            "reasoning-parser": self.settings.reasoning_parser,
            "reasoning_parser": self.settings.reasoning_parser,
            "chat_template_kwargs": {
                "preserve_thinking": True,
            },
            "metadata": {
                "preserve_thinking": True,
                "max_model_len": self.settings.max_model_len,
            },
        }

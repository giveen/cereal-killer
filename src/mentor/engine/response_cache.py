"""LRU+TTL cache for LLM Brain responses."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

_LOG = logging.getLogger(__name__)

if TYPE_CHECKING:
    from mentor.engine.brain import BrainResponse


class LLMResponseCache:
    """Thread-safe LRU cache with TTL for cached LLM responses.

    Cache key = SHA-256(system_prompt + user_message + model + temperature + extra_body_keys_hash)
    Evicts oldest entry when at maxsize capacity.
    Invalidates entries older than TTL seconds.
    """

    def __init__(self, maxsize: int = 100, ttl: int = 300) -> None:
        """Initialize the LRU+TTL cache.

        Args:
            maxsize: Maximum number of cached responses before eviction.
            ttl: Time-to-live in seconds for cached entries.
        """
        self._maxsize = maxsize
        self._ttl = ttl
        self._cache: dict[str, tuple[BrainResponse, float, str]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> BrainResponse | None:
        """Retrieve a cached BrainResponse by key.

        Returns the cached response if it exists and has not expired.
        Returns None if the key is not found, expired, or during eviction.

        Args:
            key: The cache key computed via compute_key().

        Returns:
            The cached BrainResponse, or None if not found/expired.
        """
        async with self._lock:
            if key in self._cache:
                response, timestamp, _ = self._cache[key]
                if time.monotonic() - timestamp <= self._ttl:
                    _LOG.debug("Cache HIT for key %s", key[:16])
                    self._cache.move_to_end(key)
                    return response
                else:
                    _LOG.debug("Cache EXPIRED for key %s (TTL: %ds)", key[:16], self._ttl)
                    del self._cache[key]
            _LOG.debug("Cache MISS for key %s", key[:16])
            return None

    async def put(self, key: str, response: BrainResponse) -> None:
        """Store a BrainResponse in the cache.

        Evicts the oldest entry (by insertion time) if the cache is at capacity.

        Args:
            key: The cache key computed via compute_key().
            response: The BrainResponse to cache.
        """
        async with self._lock:
            if self._maxsize > 0 and len(self._cache) >= self._maxsize:
                _LOG.debug("Cache EVICT: removed oldest entry (cache size: %d/%d)", len(self._cache), self._maxsize)
                self._cache.popitem(last=False)
            self._cache[key] = (response, time.monotonic(), "")
            _LOG.debug("Cache STORE for key %s", key[:16])
            # OrderedDict.setitem places new items at the end, maintaining LRU order

    def compute_key(self, system_prompt: str, user_message: str, model: str,
                    temperature: float, extra_body: dict[str, Any]) -> str:
        """Compute a deterministic SHA-256 hash for the prompt context.

        The cache key incorporates system_prompt, user_message, model,
        temperature, and a hash of the sorted extra_body keys.  Using
        sorted keys ensures determinism regardless of dictionary insertion
        order.

        Args:
            system_prompt: The system prompt string.
            user_message: The user message string.
            model: The model identifier string.
            temperature: The sampling temperature (float).
            extra_body: Arbitrary additional context dict.

        Returns:
            A 64-character hex SHA-256 digest serving as the cache key.
        """
        extra_body_keys = dict(sorted(extra_body.items()))
        # Use hash of keys, not values (to avoid storing large payloads)
        extra_hash = hashlib.sha256(
            json.dumps(extra_body_keys, sort_keys=True).encode()
        ).hexdigest()[:16]

        key_data = f"{system_prompt}\n{user_message}\n{model}\n{temperature}\n{extra_hash}"
        return hashlib.sha256(key_data.encode()).hexdigest()

    async def _store_result_if_enabled(
        self,
        cache_key: str,
        thought: str,
        reasoning_content: str,
        raw_content: str,
        backend_meta: dict[str, Any],
        *,
        enable_cache: bool = True,
    ) -> tuple[str, str, dict[str, Any]]:
        """Store a fresh LLM response in the cache (non-blocking)."""
        from mentor.engine.brain import BrainResponse

        if not enable_cache:
            _LOG.debug("Cache disabled, skipping store for key %s", cache_key[:16])
            return thought, reasoning_content, backend_meta

        try:
            cache_response = BrainResponse(
                thought=thought,
                answer=raw_content,
                raw_content=raw_content,
                reasoning_content=reasoning_content,
                backend_meta=backend_meta,
            )
            await self.put(cache_key, cache_response)
            _LOG.debug("Cache STORE SUCCESS for key %s", cache_key[:16])
        except Exception as exc:
            # Cache store failures must never break the main flow
            _LOG.warning("Cache STORE FAILED for key %s: %s", cache_key[:16], exc)

        return thought, reasoning_content, backend_meta

    async def clear(self) -> None:
        """Clear all entries from the cache."""
        async with self._lock:
            self._cache.clear()

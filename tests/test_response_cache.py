"""Tests for LLM response caching."""
import asyncio
import time

import unittest

from cereal_killer.engine import LLMResponse
from mentor.engine.brain import BrainResponse
from mentor.engine.response_cache import LLMResponseCache


class TestLLMResponseCache(unittest.TestCase):
    """Tests for the LRU+TTL response cache."""

    def test_cache_basic_get_set(self):
        """Cache can store and retrieve responses."""
        async def _run():
            cache = LLMResponseCache(maxsize=10, ttl=60)
            response = BrainResponse(
                thought="thinking...",
                answer="hello world",
                raw_content="hello world",
                reasoning_content="reasoning here",
                backend_meta={"latency_ms": 100, "tokens_cached": 0},
            )
            key = cache.compute_key(
                system_prompt="You are a helpful assistant.",
                user_message="Hello",
                model="gpt-4",
                temperature=0.4,
                extra_body={},
            )
            await cache.put(key, response)
            result = await cache.get(key)
            assert result is not None
            assert result.answer == "hello world"
            assert result.reasoning_content == "reasoning here"

        asyncio.run(_run())

    def test_cache_ttl_expiry(self):
        """Cached responses expire after TTL."""
        async def _run():
            cache = LLMResponseCache(maxsize=100, ttl=1)  # 1 second TTL
            response = BrainResponse(
                thought="thought", answer="cached", raw_content="cached",
                reasoning_content="reason", backend_meta={},
            )
            key = cache.compute_key(
                system_prompt="system", user_message="query",
                model="gpt-4", temperature=0.4, extra_body={},
            )
            await cache.put(key, response)
            # Verify hit immediately
            result = await cache.get(key)
            assert result is not None
            # Wait for expiry
            await asyncio.sleep(1.1)
            result = await cache.get(key)
            assert result is None

        asyncio.run(_run())

    def test_cache_lru_eviction(self):
        """Oldest entries are evicted when at capacity."""
        async def _run():
            cache = LLMResponseCache(maxsize=2, ttl=60)
            responses = [
                BrainResponse(
                    thought=f"t{j}", answer=f"resp{j}", raw_content=f"r{j}",
                    reasoning_content=f"r{j}", backend_meta={},
                )
                for j in range(3)
            ]
            keys = []
            for idx, resp in enumerate(responses):
                key = cache.compute_key(
                    system_prompt=f"sys", user_message=f"msg{idx}",
                    model="gpt-4", temperature=0.4, extra_body={},
                )
                keys.append(key)
                await cache.put(key, resp)

            # Oldest should be evicted
            stale_result = await cache.get(keys[0])
            assert stale_result is None
            # Newer should exist
            fresh_result = await cache.get(keys[2])
            assert fresh_result is not None
            assert fresh_result.answer == "resp2"

        asyncio.run(_run())

    def test_cache_compute_key_determinism(self):
        """Same inputs produce the same cache key."""
        cache = LLMResponseCache()
        params = dict(
            system_prompt="You are helpful",
            user_message="What is Python?",
            model="gpt-4",
            temperature=0.4,
            extra_body={"foo": "bar"},
        )
        key1 = cache.compute_key(**params)
        key2 = cache.compute_key(**params)
        assert key1 == key2
        assert key1 != ""

    def test_cache_compute_key_different_inputs(self):
        """Different inputs produce different cache keys."""
        cache = LLMResponseCache()
        key1 = cache.compute_key(
            system_prompt="sys", user_message="msg1",
            model="gpt-4", temperature=0.4, extra_body={},
        )
        key2 = cache.compute_key(
            system_prompt="sys", user_message="msg2",
            model="gpt-4", temperature=0.4, extra_body={},
        )
        assert key1 != key2

    def test_brain_caching_integration(self):
        """Verify Brain's cache instance is properly initialized and usable."""
        from cereal_killer.config import Settings
        from mentor.engine.brain import Brain, BrainResponse
        import asyncio
        
        async def _run():
            settings = Settings(
                enable_llm_cache=True,
                llm_cache_maxsize=100,
                llm_cache_ttl=60,
            )
            brain = Brain(settings)
            
            # Verify Brain has the cache
            assert hasattr(brain, "_response_cache"), "Brain should have _response_cache"
            assert brain._response_cache is not None
            
            # Verify cache key computation works
            key1 = brain._response_cache.compute_key(
                system_prompt="system",
                user_message="message",
                model="gpt-4",
                temperature=0.4,
                extra_body={"key": "value"},
            )
            assert isinstance(key1, str) and len(key1) == 64  # SHA-256 hex digest
            
            # Verify deterministic key generation
            key2 = brain._response_cache.compute_key(
                system_prompt="system",
                user_message="message",
                model="gpt-4",
                temperature=0.4,
                extra_body={"key": "value"},
            )
            assert key1 == key2
            
            # Verify different inputs produce different keys
            key3 = brain._response_cache.compute_key(
                system_prompt="different",
                user_message="message",
                model="gpt-4",
                temperature=0.4,
                extra_body={"key": "value"},
            )
            assert key1 != key3
            
            # Verify cache can store and retrieve BrainResponse
            test_response = BrainResponse(
                thought="test thought",
                answer="test answer",
                raw_content="test answer",
                reasoning_content="test reasoning",
                backend_meta={"test": "data"},
            )
            await brain._response_cache.put(key1, test_response)
            
            retrieved = await brain._response_cache.get(key1)
            assert retrieved is not None
            assert retrieved.answer == "test answer"
            assert retrieved.thought == "test thought"
            
            # Verify cache miss returns None
            miss = await brain._response_cache.get("nonexistent_key")
            assert miss is None
        
        asyncio.run(_run())

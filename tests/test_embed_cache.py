"""Tests for TTL and thread safety of the embedding cache."""
import asyncio
import time
import unittest
from unittest.mock import MagicMock, patch, AsyncMock


class TestEmbedCacheTTL(unittest.IsolatedAsyncioTestCase):
    """Tests for TTL-based eviction in the embedding cache.

    Note: These tests modify module-level constants (_EMBED_CACHE_SIZE,
    _EMBED_CACHE_TTL_SECS) which are preserved as fallbacks for backwards
    compatibility with Settings-based constants.
    """

    async def asyncSetUp(self) -> None:
        # Import the module for patching
        import mentor.kb.query as q
        self.q = q
        # Store original values for restoration
        self._orig_cache = q._embed_cache.copy()
        self._orig_ttl = q._EMBED_CACHE_TTL_SECS
        self._orig_size = q._EMBED_CACHE_SIZE

    async def asyncTearDown(self) -> None:
        # Restore original state
        self.q._embed_cache.clear()
        self.q._EMBED_CACHE_TTL_SECS = self._orig_ttl
        self.q._EMBED_CACHE_SIZE = self._orig_size

    async def test_cache_hit_returns_cached_embedding(self) -> None:
        """Valid cached embeddings are returned without recomputation."""
        self.q._EMBED_CACHE_TTL_SECS = 3600  # 1 hour
        self.q._embed_cache.clear()

        test_text = "test query"
        expected_embedding = [0.1] * self.q.EMBEDDING_DIMS
        now = time.time()
        self.q._embed_cache[test_text] = (expected_embedding, now)

        with patch.object(self.q, '_get_embedding_model', return_value=None):
            result = await self.q._embed_with_cache(test_text)

        self.assertEqual(result, expected_embedding)

    async def test_expired_entry_is_evicted_on_access(self) -> None:
        """Entries older than TTL are evicted and recomputed."""
        self.q._EMBED_CACHE_TTL_SECS = 0  # Expired immediately
        self.q._embed_cache.clear()

        test_text = "test query"
        old_embedding = [0.1] * self.q.EMBEDDING_DIMS
        expired_time = time.time() - 100  # 100 seconds ago
        self.q._embed_cache[test_text] = (old_embedding, expired_time)

        mock_model = MagicMock()
        new_embedding = [0.9] * self.q.EMBEDDING_DIMS
        mock_model.encode.return_value = new_embedding

        with patch.object(self.q, '_get_embedding_model', return_value=mock_model):
            result = await self.q._embed_with_cache(test_text)

        self.assertEqual(result, new_embedding)
        # Verify the old entry was replaced with a fresh one
        self.assertIn(test_text, self.q._embed_cache)
        value, timestamp = self.q._embed_cache[test_text]
        self.assertEqual(value, new_embedding)
        self.assertAlmostEqual(timestamp, time.time(), delta=2)

    async def test_lru_position_refreshed_on_valid_cache_hit(self) -> None:
        """Valid cache hits refresh the LRU position (re-insert at end)."""
        self.q._EMBED_CACHE_TTL_SECS = 3600
        self.q._embed_cache.clear()
        self.q._EMBED_CACHE_SIZE = 2

        emb = [0.1] * self.q.EMBEDDING_DIMS
        now = time.time()

        # Fill cache with 2 entries
        self.q._embed_cache["first"] = (emb, now)
        self.q._embed_cache["second"] = (emb, now)

        # Access "first" — should move it to the end
        with patch.object(self.q, '_get_embedding_model', return_value=None):
            await self.q._embed_with_cache("first")

        # "first" should now be at the end of the dict
        keys = list(self.q._embed_cache.keys())
        self.assertEqual(keys[-1], "first")

    async def test_lru_eviction_when_cache_full(self) -> None:
        """Oldest entry is evicted when cache exceeds max size."""
        self.q._EMBED_CACHE_TTL_SECS = 3600
        self.q._embed_cache.clear()
        self.q._EMBED_CACHE_SIZE = 2

        emb = [0.1] * self.q.EMBEDDING_DIMS
        old_time = time.time() - 10
        self.q._embed_cache["oldest"] = (emb, old_time)
        self.q._embed_cache["newer"] = (emb, old_time + 1)

        mock_model = MagicMock()
        new_emb = [0.9] * self.q.EMBEDDING_DIMS
        mock_model.encode.return_value = new_emb

        with patch.object(self.q, '_get_embedding_model', return_value=mock_model):
            await self.q._embed_with_cache("brand_new")

        # Cache should still have exactly 2 entries
        self.assertEqual(len(self.q._embed_cache), 2)
        # The oldest entry should have been evicted
        self.assertNotIn("oldest", self.q._embed_cache)
        self.assertIn("brand_new", self.q._embed_cache)

    async def test_expired_entries_evicted_before_valid_ones(self) -> None:
        """When evicting, expired entries are removed before valid ones."""
        self.q._EMBED_CACHE_TTL_SECS = 60  # 1 minute
        self.q._embed_cache.clear()
        self.q._EMBED_CACHE_SIZE = 2

        emb = [0.1] * self.q.EMBEDDING_DIMS
        now = time.time()
        # One expired, one valid
        self.q._embed_cache["expired"] = (emb, now - 120)  # 2 min ago
        self.q._embed_cache["valid"] = (emb, now)  # just now

        mock_model = MagicMock()
        new_emb = [0.9] * self.q.EMBEDDING_DIMS
        mock_model.encode.return_value = new_emb

        with patch.object(self.q, '_get_embedding_model', return_value=mock_model):
            await self.q._embed_with_cache("new_entry")

        # The expired entry should be evicted, not the valid one
        self.assertNotIn("expired", self.q._embed_cache)
        self.assertIn("valid", self.q._embed_cache)
        self.assertIn("new_entry", self.q._embed_cache)

    async def test_hash_fallback_when_model_is_none(self) -> None:
        """Hash-based embedding is used and cached when model is None."""
        self.q._EMBED_CACHE_TTL_SECS = 3600
        self.q._embed_cache.clear()

        with patch.object(self.q, '_get_embedding_model', return_value=None):
            result = await self.q._embed_with_cache("test text")

        self.assertEqual(len(result), self.q.EMBEDDING_DIMS)
        self.assertIn("test text", self.q._embed_cache)
        value, timestamp = self.q._embed_cache["test text"]
        self.assertEqual(value, result)

    async def test_clear_embedding_cache_removes_all_entries(self) -> None:
        """_clear_embedding_cache clears all entries."""
        self.q._embed_cache.clear()
        emb = [0.1] * self.q.EMBEDDING_DIMS
        self.q._embed_cache["entry1"] = (emb, time.time())
        self.q._embed_cache["entry2"] = (emb, time.time())

        self.q._clear_embedding_cache()

        self.assertEqual(len(self.q._embed_cache), 0)


class TestEmbedCacheThreadSafety(unittest.IsolatedAsyncioTestCase):
    """Tests for asyncio.Lock thread safety."""

    async def asyncSetUp(self) -> None:
        import mentor.kb.query as q
        self.q = q
        self._orig_cache = q._embed_cache.copy()
        self._orig_lock = q._embed_lock
        self.q._embed_lock = None  # Reset lock for each test

    async def asyncTearDown(self) -> None:
        self.q._embed_cache.clear()
        self.q._embed_lock = self._orig_lock

    async def test_concurrent_calls_do_not_corrupt_cache(self) -> None:
        """Multiple concurrent calls don't corrupt the cache."""
        self.q._EMBED_CACHE_TTL_SECS = 3600
        self.q._embed_cache.clear()
        self.q._EMBED_CACHE_SIZE = 100

        mock_model = MagicMock()
        emb = [0.5] * self.q.EMBEDDING_DIMS
        mock_model.encode.return_value = emb

        texts = [f"query_{i}" for i in range(10)]

        with patch.object(self.q, '_get_embedding_model', return_value=mock_model):
            tasks = [self.q._embed_with_cache(text) for text in texts]
            results = await asyncio.gather(*tasks)

        # All results should be valid embeddings
        for result in results:
            self.assertEqual(len(result), self.q.EMBEDDING_DIMS)

        # All texts should be in cache
        self.assertEqual(len(self.q._embed_cache), 10)

    async def test_lock_is_lazy_initialized(self) -> None:
        """The lock is lazily initialized on first access."""
        self.q._embed_lock = None

        lock = self.q._get_embed_lock()

        self.assertIsInstance(lock, asyncio.Lock)
        self.assertIsNotNone(self.q._embed_lock)

    async def test_same_lock_returned_on_repeated_calls(self) -> None:
        """_get_embed_lock returns the same lock instance on repeated calls."""
        self.q._embed_lock = None

        lock1 = self.q._get_embed_lock()
        lock2 = self.q._get_embed_lock()

        self.assertIs(lock1, lock2)


if __name__ == "__main__":
    unittest.main()

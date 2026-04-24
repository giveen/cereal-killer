"""Tests for batch embedding functionality."""
import asyncio
import time
import unittest
from unittest.mock import MagicMock, patch


class TestBatchEmbed(unittest.IsolatedAsyncioTestCase):
    """Tests for batch_embed() and _batch_embed_with_cache()."""

    async def asyncSetUp(self) -> None:
        import mentor.kb.query as q
        self.q = q
        # Store original values for restoration
        self._orig_cache = q._embed_cache.copy()
        self._orig_ttl = q._EMBED_CACHE_TTL_SECS
        self._orig_size = q._EMBED_CACHE_SIZE
        self._orig_lock = q._embed_lock
        q._embed_lock = None  # Reset lock for each test

    async def asyncTearDown(self) -> None:
        self.q._embed_cache.clear()
        self.q._EMBED_CACHE_TTL_SECS = self._orig_ttl
        self.q._EMBED_CACHE_SIZE = self._orig_size
        self.q._embed_lock = self._orig_lock

    async def test_batch_embed_empty_list_returns_empty_list(self) -> None:
        """batch_embed() with empty list returns empty list."""
        result = await self.q.batch_embed([])
        self.assertEqual(result, [])

    async def test_batch_embed_single_text_same_as_embed(self) -> None:
        """batch_embed() with single text returns same result as embed()."""
        self.q._EMBED_CACHE_TTL_SECS = 3600
        self.q._embed_cache.clear()

        test_text = "test query"
        expected_emb_1d = [0.5] * self.q.EMBEDDING_DIMS
        expected_emb_2d = [expected_emb_1d]

        mock_model = MagicMock()

        def smart_encode(text, **kwargs):
            if isinstance(text, list):
                return expected_emb_2d
            return expected_emb_1d

        mock_model.encode.side_effect = smart_encode

        with patch.object(self.q, '_get_embedding_model', return_value=mock_model):
            single_result = await self.q.embed(test_text)
            batch_result = await self.q.batch_embed([test_text])

        # Both should produce valid embeddings of the correct length
        self.assertEqual(len(single_result), self.q.EMBEDDING_DIMS)
        self.assertEqual(len(batch_result), 1)
        self.assertEqual(len(batch_result[0]), self.q.EMBEDDING_DIMS)
        # Both should return the same embedding values
        self.assertEqual(single_result, batch_result[0])

    async def test_batch_embed_multiple_texts_returns_correct_count(self) -> None:
        """batch_embed() with multiple texts returns correct number of embeddings."""
        self.q._EMBED_CACHE_TTL_SECS = 3600
        self.q._embed_cache.clear()

        texts = ["text one", "text two", "text three"]
        expected_embs = [
            [0.1] * self.q.EMBEDDING_DIMS,
            [0.2] * self.q.EMBEDDING_DIMS,
            [0.3] * self.q.EMBEDDING_DIMS,
        ]

        mock_model = MagicMock()
        mock_model.encode.return_value = expected_embs

        with patch.object(self.q, '_get_embedding_model', return_value=mock_model):
            result = await self.q.batch_embed(texts)

        self.assertEqual(len(result), 3)
        for emb in result:
            self.assertEqual(len(emb), self.q.EMBEDDING_DIMS)

    async def test_batch_embed_preserves_input_order(self) -> None:
        """batch_embed() preserves order of results matching input order."""
        self.q._EMBED_CACHE_TTL_SECS = 3600
        self.q._embed_cache.clear()

        texts = ["alpha", "beta", "gamma"]
        expected_embs = [
            [1.0] * self.q.EMBEDDING_DIMS,
            [2.0] * self.q.EMBEDDING_DIMS,
            [3.0] * self.q.EMBEDDING_DIMS,
        ]

        mock_model = MagicMock()
        mock_model.encode.return_value = expected_embs

        with patch.object(self.q, '_get_embedding_model', return_value=mock_model):
            result = await self.q.batch_embed(texts)

        # First result should match first input
        self.assertEqual(result[0][0], 1.0)
        self.assertEqual(result[1][0], 2.0)
        self.assertEqual(result[2][0], 3.0)

    async def test_cached_texts_returned_from_cache(self) -> None:
        """Cached texts in batch are returned from cache, not recomputed."""
        self.q._EMBED_CACHE_TTL_SECS = 3600
        self.q._embed_cache.clear()

        cached_emb = [0.9] * self.q.EMBEDDING_DIMS
        now = time.time()
        self.q._embed_cache["cached_text"] = (cached_emb, now)

        texts = ["cached_text", "new_text"]
        new_emb = [0.1] * self.q.EMBEDDING_DIMS

        mock_model = MagicMock()
        mock_model.encode.return_value = [new_emb]

        with patch.object(self.q, '_get_embedding_model', return_value=mock_model):
            result = await self.q.batch_embed(texts)

        # First result should be the cached embedding
        self.assertEqual(result[0], cached_emb)
        # Second result should be the newly computed embedding
        self.assertEqual(result[1], new_emb)

    async def test_mixed_cached_and_uncached_works_correctly(self) -> None:
        """Mixed cached + uncached texts in batch work correctly."""
        self.q._EMBED_CACHE_TTL_SECS = 3600
        self.q._embed_cache.clear()

        cached_emb = [0.9] * self.q.EMBEDDING_DIMS
        now = time.time()
        self.q._embed_cache["text_a"] = (cached_emb, now)
        self.q._embed_cache["text_c"] = (cached_emb, now)

        texts = ["text_a", "text_b", "text_c"]
        new_emb = [0.1] * self.q.EMBEDDING_DIMS

        mock_model = MagicMock()
        mock_model.encode.return_value = [new_emb]

        with patch.object(self.q, '_get_embedding_model', return_value=mock_model):
            result = await self.q.batch_embed(texts)

        self.assertEqual(len(result), 3)
        # text_a and text_c should use cached embeddings
        self.assertEqual(result[0], cached_emb)
        self.assertEqual(result[2], cached_emb)
        # text_b should use the newly computed embedding
        self.assertEqual(result[1], new_emb)

    async def test_hash_fallback_when_model_is_none(self) -> None:
        """Hash fallback when model is None works in batch mode."""
        self.q._EMBED_CACHE_TTL_SECS = 3600
        self.q._embed_cache.clear()

        texts = ["text one", "text two"]

        with patch.object(self.q, '_get_embedding_model', return_value=None):
            result = await self.q.batch_embed(texts)

        self.assertEqual(len(result), 2)
        for emb in result:
            self.assertEqual(len(emb), self.q.EMBEDDING_DIMS)
            # Hash embeddings should be in range [-1, 1]
            for val in emb:
                self.assertTrue(-1.0 <= val <= 1.0)

    async def test_batch_embed_new_entries_added_to_cache(self) -> None:
        """Newly computed embeddings are added to the cache."""
        self.q._EMBED_CACHE_TTL_SECS = 3600
        self.q._embed_cache.clear()

        texts = ["new text 1", "new text 2"]
        expected_embs = [
            [0.1] * self.q.EMBEDDING_DIMS,
            [0.2] * self.q.EMBEDDING_DIMS,
        ]

        mock_model = MagicMock()
        mock_model.encode.return_value = expected_embs

        with patch.object(self.q, '_get_embedding_model', return_value=mock_model):
            await self.q.batch_embed(texts)

        # Both texts should now be in the cache
        self.assertIn("new text 1", self.q._embed_cache)
        self.assertIn("new text 2", self.q._embed_cache)


if __name__ == "__main__":
    unittest.main()

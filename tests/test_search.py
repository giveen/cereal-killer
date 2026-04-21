"""Tests for the SearXNG web_search wrapper and tiered search orchestrator."""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from cereal_killer.config import Settings
from mentor.tools.web_search import WebResult, format_web_results, search as web_search
from mentor.engine.search_orchestrator import (
    SearchResult,
    _best_vector_score,
    tiered_search,
)
from mentor.kb.query import RAGSnippet


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# web_search helpers
# ---------------------------------------------------------------------------

class TestFormatWebResults(unittest.TestCase):
    def test_empty_results_returns_no_results_string(self) -> None:
        assert format_web_results([]) == "Web search returned no results."

    def test_formats_single_result(self) -> None:
        r = WebResult(title="Test Title", url="https://example.com", snippet="A snippet here.")
        output = format_web_results([r])
        assert "[1] Test Title" in output
        assert "URL: https://example.com" in output
        assert "A snippet here." in output

    def test_formats_multiple_results_with_index(self) -> None:
        results = [
            WebResult(title="One", url="https://one.com", snippet=""),
            WebResult(title="Two", url="https://two.com", snippet=""),
        ]
        output = format_web_results(results)
        assert "[1] One" in output
        assert "[2] Two" in output

    def test_snippet_truncated_in_wrapper(self) -> None:
        long_snippet = "x" * 400
        # web_search.py truncates to 300 chars when creating WebResult
        r = WebResult(title="T", url="https://t.com", snippet=long_snippet[:300])
        output = format_web_results([r])
        assert "x" * 300 in output

    def test_result_without_snippet_still_formats(self) -> None:
        r = WebResult(title="No snippet", url="https://ns.com", snippet="")
        output = format_web_results([r])
        assert "No snippet" in output
        assert "URL: https://ns.com" in output


class TestWebSearch(unittest.TestCase):
    """Patch at module level (mentor.tools.web_search.httpx) so tests run without httpx installed."""

    def _mock_httpx(self, json_data: dict, *, raise_exc: Exception | None = None) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = json_data

        mock_client = AsyncMock()
        if raise_exc is not None:
            mock_client.get.side_effect = raise_exc
        else:
            mock_client.get = AsyncMock(return_value=mock_resp)

        mock_httpx = MagicMock()
        mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)
        return mock_httpx

    def test_returns_empty_list_on_http_error(self) -> None:
        async def run():
            mock_httpx = self._mock_httpx({}, raise_exc=Exception("connection refused"))
            with patch("mentor.tools.web_search.httpx", mock_httpx):
                return await web_search("test query", "http://localhost:8080")

        results = asyncio.run(run())
        self.assertEqual(results, [])

    def test_parses_results_correctly(self) -> None:
        raw = [
            {"title": "CVE-2021-41773", "url": "https://cve.org/1", "content": "Apache path traversal."},
            {"title": "PoC", "url": "https://github.com/exploit", "content": "Proof of concept code."},
        ]

        async def run():
            mock_httpx = self._mock_httpx({"results": raw})
            with patch("mentor.tools.web_search.httpx", mock_httpx):
                return await web_search("CVE-2021-41773", "http://localhost:8080")

        results = asyncio.run(run())
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].title, "CVE-2021-41773")
        self.assertEqual(results[0].url, "https://cve.org/1")
        self.assertEqual(results[0].snippet, "Apache path traversal.")

    def test_respects_max_results_cap(self) -> None:
        raw = [{"title": f"R{i}", "url": f"https://r{i}.com", "content": ""} for i in range(10)]

        async def run():
            mock_httpx = self._mock_httpx({"results": raw})
            with patch("mentor.tools.web_search.httpx", mock_httpx):
                return await web_search("query", "http://localhost:8080", max_results=3)

        results = asyncio.run(run())
        self.assertEqual(len(results), 3)

    def test_skips_results_without_url(self) -> None:
        raw = [
            {"title": "No URL", "url": "", "content": "something"},
            {"title": "Has URL", "url": "https://valid.com", "content": "valid"},
        ]

        async def run():
            mock_httpx = self._mock_httpx({"results": raw})
            with patch("mentor.tools.web_search.httpx", mock_httpx):
                return await web_search("query", "http://localhost:8080")

        results = asyncio.run(run())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://valid.com")


# ---------------------------------------------------------------------------
# _best_vector_score
# ---------------------------------------------------------------------------

class TestBestVectorScore(unittest.TestCase):
    def _snip(self, distance: float) -> RAGSnippet:
        return RAGSnippet(
            source="ippsec", machine="lame", title="", url="", content="", score=distance
        )

    def test_empty_snippets_returns_zero(self) -> None:
        assert _best_vector_score([]) == 0.0

    def test_converts_distance_to_similarity(self) -> None:
        # distance 0.2 → similarity 0.8
        assert abs(_best_vector_score([self._snip(0.2)]) - 0.8) < 1e-9

    def test_picks_minimum_distance(self) -> None:
        # closest match has distance 0.1 → similarity 0.9
        snips = [self._snip(0.5), self._snip(0.1), self._snip(0.3)]
        assert abs(_best_vector_score(snips) - 0.9) < 1e-9


# ---------------------------------------------------------------------------
# tiered_search integration
# ---------------------------------------------------------------------------

class TestTieredSearch(unittest.TestCase):
    def _settings(self, searxng_url: str = "http://localhost:8080") -> Settings:
        return Settings(redis_url="redis://localhost:59999", searxng_base_url=searxng_url)

    def test_web_not_triggered_when_vector_score_above_threshold(self) -> None:
        good_snippets = [
            RAGSnippet("ippsec", "lame", "T", "http://u", "content", score=0.1)  # similarity 0.9
        ]
        async def run():
            with patch("mentor.engine.search_orchestrator.retrieve_reference_material", return_value=good_snippets), \
                 patch("mentor.engine.search_orchestrator.web_search") as mock_ws:
                result = await tiered_search("nmap results", self._settings(), vector_threshold=0.7)
                mock_ws.assert_not_called()
                return result

        result = asyncio.run(run())
        assert not result.used_web
        assert result.vector_snippets == good_snippets

    def test_web_triggered_when_vector_score_below_threshold(self) -> None:
        poor_snippets = [
            RAGSnippet("ippsec", "lame", "T", "http://u", "content", score=0.8)  # similarity 0.2
        ]
        web_results = [WebResult("WR", "https://wr.com", "snippet")]

        async def run():
            with patch("mentor.engine.search_orchestrator.retrieve_reference_material", return_value=poor_snippets), \
                 patch("mentor.engine.search_orchestrator.web_search", AsyncMock(return_value=web_results)):
                return await tiered_search("obscure CVE", self._settings(), vector_threshold=0.7)

        result = asyncio.run(run())
        assert result.used_web
        assert result.web_results == web_results

    def test_force_web_bypasses_threshold(self) -> None:
        good_snippets = [
            RAGSnippet("ippsec", "lame", "T", "http://u", "content", score=0.05)  # similarity 0.95
        ]
        web_results = [WebResult("WR", "https://wr.com", "snippet")]

        async def run():
            with patch("mentor.engine.search_orchestrator.retrieve_reference_material", return_value=good_snippets), \
                 patch("mentor.engine.search_orchestrator.web_search", AsyncMock(return_value=web_results)):
                return await tiered_search("anything", self._settings(), force_web=True)

        result = asyncio.run(run())
        assert result.used_web

    def test_web_skipped_when_searxng_url_empty(self) -> None:
        poor_snippets = [
            RAGSnippet("ippsec", "lame", "T", "http://u", "content", score=0.9)  # similarity 0.1
        ]

        async def run():
            settings = Settings(redis_url="redis://localhost:59999", searxng_base_url="")
            with patch("mentor.engine.search_orchestrator.retrieve_reference_material", return_value=poor_snippets), \
                 patch("mentor.engine.search_orchestrator.web_search") as mock_ws:
                result = await tiered_search("query", settings, vector_threshold=0.7)
                mock_ws.assert_not_called()
                return result

        result = asyncio.run(run())
        assert not result.used_web

    def test_reference_block_includes_web_preamble_when_web_used(self) -> None:
        poor_snippets: list[RAGSnippet] = []
        web_results = [WebResult("Title", "https://t.com", "snippet text")]

        async def run():
            with patch("mentor.engine.search_orchestrator.retrieve_reference_material", return_value=poor_snippets), \
                 patch("mentor.engine.search_orchestrator.web_search", AsyncMock(return_value=web_results)):
                return await tiered_search("query", self._settings(), vector_threshold=0.7)

        result = asyncio.run(run())
        assert "uncharted territory" in result.reference_block
        assert "cite" in result.reference_block.lower()

    def test_tiered_search_returns_early_when_within_timeout(self) -> None:
        """When search completes within timeout, normal results are returned."""
        quick_snippets = [
            RAGSnippet("ippsec", "lame", "T", "http://u", "content", score=0.1)
        ]
        async def run():
            with patch("mentor.engine.search_orchestrator.retrieve_reference_material", return_value=quick_snippets), \
                 patch("mentor.engine.search_orchestrator.web_search") as mock_ws:
                # timeout=60 should be enough for mocked search to complete
                result = await tiered_search("test", self._settings(), rag_timeout=60)
                mock_ws.assert_not_called()
                return result
        result = asyncio.run(run())
        assert not result.used_web
        assert len(result.vector_snippets) == 1

    def test_tiered_search_times_out_returns_partial_result(self) -> None:
        """When search exceeds rag_timeout, partial result is returned with timeout message."""
        partial_snippets = [RAGSnippet("ippsec", "lame", "T", "http://u", "content", score=0.1)]

        async def run():
            async def slow_web_search(*args, **kwargs):
                await asyncio.sleep(5)  # longer than timeout
                return [WebResult("WR", "https://wr.com", "snippet")]
            
            with patch("mentor.engine.search_orchestrator.retrieve_reference_material", return_value=partial_snippets), \
                 patch("mentor.engine.search_orchestrator.web_search", side_effect=slow_web_search), \
                 patch("mentor.engine.search_orchestrator._best_vector_score", return_value=0.3):  # below threshold
                result = await tiered_search("slow query", self._settings(), rag_timeout=0.1)
                assert "timed out" in result.reference_block.lower()
                return result

        result = asyncio.run(run())
        assert result.vector_snippets == partial_snippets

    def test_tiered_search_partial_data_preserved_on_timeout(self) -> None:
        """When timeout occurs after partial work, partial data is preserved."""
        partial_snippets = [RAGSnippet("ippsec", "lame", "T", "http://u", "partial", score=0.3)]
        async def run():
            async def fast_web_search_then_timeout(*args, **kwargs):
                # This is the slow call inside _run_pipeline that gets wrapped by wait_for
                await asyncio.sleep(5)
                return []
            
            with patch("mentor.engine.search_orchestrator.retrieve_reference_material", return_value=partial_snippets), \
                 patch("mentor.engine.search_orchestrator.web_search", side_effect=fast_web_search_then_timeout), \
                 patch("mentor.engine.search_orchestrator._best_vector_score", return_value=0.3):  # below threshold
                result = await tiered_search("partial query", self._settings(), rag_timeout=0.1)
                assert "timed out" in result.reference_block.lower()
                assert len(result.vector_snippets) == 1
                return result
        result = asyncio.run(run())
        assert len(result.vector_snippets) == 1

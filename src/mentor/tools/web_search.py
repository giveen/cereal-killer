"""SearXNG HTTP wrapper for Cereal-Killer.

Calls the /search?format=json endpoint and returns a compact list of
WebResult objects — title, snippet, URL only.  Raw JSON is never
forwarded to the LLM; we deliberately keep the payload small.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

try:
    import httpx
except ImportError:  # pragma: no cover - test env may not have httpx
    httpx = None  # type: ignore[assignment]


# Maximum wall-clock time for a single SearXNG round-trip.
_REQUEST_TIMEOUT = 12.0

# Hard cap on the number of results forwarded to the LLM.
_MAX_RESULTS = 5

# Snippet truncation limit per result.
_SNIPPET_CHARS = 300


@dataclass(slots=True)
class WebResult:
    title: str
    url: str
    snippet: str


async def search(
    query: str,
    base_url: str,
    *,
    max_results: int = _MAX_RESULTS,
    timeout: float = _REQUEST_TIMEOUT,
) -> list[WebResult]:
    """Execute a SearXNG JSON search and return up to *max_results* results.

    Returns an empty list on any network or parsing error so callers can
    degrade gracefully without raising.
    """
    params = {
        "q": query,
        "format": "json",
        "language": "en",
        "safesearch": "0",
    }
    try:
        if httpx is None:
            return []
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{base_url.rstrip('/')}/search", params=params)
            response.raise_for_status()
            data = response.json()
    except Exception:
        return []

    results: list[WebResult] = []
    for item in data.get("results", [])[:max_results]:
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("content") or item.get("snippet") or "").strip()
        if not url:
            continue
        results.append(
            WebResult(
                title=title,
                url=url,
                snippet=snippet[:_SNIPPET_CHARS],
            )
        )

    return results


def format_web_results(results: list[WebResult]) -> str:
    """Render *results* as a compact block for injection into an LLM prompt."""
    if not results:
        return "Web search returned no results."

    lines = ["Live Web Results (cite these URLs if you use this information):"]
    for i, r in enumerate(results, 1):
        lines.append(f"\n[{i}] {r.title}")
        lines.append(f"    URL: {r.url}")
        if r.snippet:
            lines.append(f"    {r.snippet}")
    return "\n".join(lines)

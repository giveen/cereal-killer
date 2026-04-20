from __future__ import annotations

from dataclasses import dataclass, field
from inspect import signature
from typing import Any


@dataclass(slots=True)
class CrawledPage:
    """Normalized Crawl4AI output for downstream ingestion/synthesis."""

    url: str
    title: str
    # Full fidelity Markdown for archival/database storage.
    raw_markdown: str
    # Noise-reduced Markdown emitted by Crawl4AI's fit pipeline.
    fit_markdown: str
    # Preferred text for chunking/synthesis (fit if substantial, else raw).
    rag_markdown: str
    rag_source: str
    metadata: dict[str, str] = field(default_factory=dict)


def _extract_markdown(payload: Any) -> tuple[str, str]:
    """Extract raw/fit markdown from Crawl4AI result object safely."""
    markdown_obj = getattr(payload, "markdown", None)
    if markdown_obj is None:
        raw = str(getattr(payload, "markdown", "") or "")
        return raw, ""

    raw = str(getattr(markdown_obj, "raw_markdown", "") or "")
    fit = str(getattr(markdown_obj, "fit_markdown", "") or "")
    return raw, fit


def _safe_build_kwargs(callable_obj: Any, candidate_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Filter kwargs by callable signature to survive Crawl4AI version drift."""
    try:
        params = signature(callable_obj).parameters
    except Exception:
        return candidate_kwargs

    # If the callable accepts **kwargs, pass everything through.
    if any(param.kind.name == "VAR_KEYWORD" for param in params.values()):
        return candidate_kwargs

    return {k: v for k, v in candidate_kwargs.items() if k in params}


async def crawl_url(
    url: str,
    *,
    ingested_via: str = "manual_crawl",
    fit_min_chars: int = 500,
) -> CrawledPage:
    """Crawl a URL with security-oriented defaults and return normalized markdown.

    Prompt #26 requirements implemented:
    - PruningContentFilter(threshold=0.5, min_word_threshold=15)
    - CrawlerRunConfig exclusions + iframe/overlay/link controls
    - wait_for="body" + js_code readiness wait for SPAs
    - CacheMode.ENABLED
    - bypass_headful=True
    - capture both raw_markdown and fit_markdown
    - prefer fit_markdown for RAG when len(fit_markdown) > fit_min_chars
    """
    try:
        from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig
        from crawl4ai.content_filter_strategy import PruningContentFilter
    except Exception as exc:  # pragma: no cover - optional dependency in CI
        raise RuntimeError(
            "crawl4ai is required for web crawling. Install crawl4ai to enable /add-source and deep crawl."
        ) from exc

    content_filter = PruningContentFilter(threshold=0.5, min_word_threshold=15)

    run_kwargs = {
        "content_filter": content_filter,
        "excluded_tags": ["nav", "footer", "header", "aside", "form", "script", "style"],
        "remove_overlay_elements": True,
        "process_iframes": True,
        "exclude_external_links": True,
        "exclude_social_media_links": True,
        "wait_for": "body",
        # Ensure SPA content has time to render before extraction.
        "js_code": (
            "async () => {"
            "  const pause = (ms) => new Promise(r => setTimeout(r, ms));"
            "  await pause(300);"
            "  let spins = 0;"
            "  while (spins < 30) {"
            "    const txt = (document.body?.innerText || '').trim().toLowerCase();"
            "    if (!txt.includes('loading') && txt.length > 120) break;"
            "    await pause(200);"
            "    spins += 1;"
            "  }"
            "}"
        ),
        "cache_mode": CacheMode.ENABLED,
    }

    run_config = CrawlerRunConfig(**_safe_build_kwargs(CrawlerRunConfig, run_kwargs))

    crawler_kwargs = _safe_build_kwargs(
        AsyncWebCrawler,
        {
            # Run browser in background/non-headful mode to preserve GPU/VRAM budget.
            "bypass_headful": True,
        },
    )

    async with AsyncWebCrawler(**crawler_kwargs) as crawler:
        result = await crawler.arun(url=url, config=run_config)

    raw_markdown, fit_markdown = _extract_markdown(result)
    rag_markdown = fit_markdown if len(fit_markdown) > fit_min_chars else raw_markdown
    rag_source = "fit_markdown" if rag_markdown == fit_markdown else "raw_markdown"

    title = str(getattr(result, "title", "") or "").strip() or url
    return CrawledPage(
        url=url,
        title=title,
        raw_markdown=raw_markdown,
        fit_markdown=fit_markdown,
        rag_markdown=rag_markdown,
        rag_source=rag_source,
        metadata={"ingested_via": ingested_via},
    )

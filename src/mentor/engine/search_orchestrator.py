"""Tiered search orchestrator for Cereal-Killer.

Strategy
--------
1.  Always retrieve from the Redis Vector DB first (IppSec / HackTricks).
2.  If the best similarity score from Redis is below *vector_threshold*
    **or** the caller explicitly opts in via *force_web=True*, fall through
    to a live SearXNG query.
3.  Return a SearchResult that records which path was taken so callers
    (Brain, UI) can react accordingly.

The threshold is deliberately generous (default 0.7) so the web is only
reached when local knowledge is clearly insufficient.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)

try:
    from asyncio import TimeoutError as _AsyncioTimeoutError
except ImportError:
    _AsyncioTimeoutError = asyncio.CancelledError  # type: ignore[misc,assignment]

from cereal_killer.config import Settings
from mentor.kb.query import (
    RAG_NOT_EMPTY_SIMILARITY_THRESHOLD,
    RAGSnippet,
    format_reference_material,
    retrieve_reference_material,
    top_similarity_scores,
)
from mentor.tools.web_search import WebResult, format_web_results, search as web_search


# Cosine distance ≥ this means the local index has no useful material.
# redisvl returns vector_distance where 0 = identical, 1 = orthogonal.
# We invert: score = 1 - distance; so a score < threshold → go to web.
_DEFAULT_VECTOR_THRESHOLD = RAG_NOT_EMPTY_SIMILARITY_THRESHOLD
_DEFAULT_INDEX_PRIORITY = ["ippsec", "gtfobins", "lolbas", "hacktricks", "payloads"]
_REFERENCE_TOKEN_BUDGET = 1500
_GENERIC_METHOD_HINTS = (
    "nmap",
    "gobuster",
    "dirsearch",
    "enumeration",
    "methodology",
    "cheatsheet",
    "general",
    "recon",
)


def _resolve_index_priority(settings: Settings) -> list[str]:
    """Return index order with configured Redis index guaranteed to be included.

    This prevents silent empty retrievals when REDIS_INDEX differs from legacy
    hardcoded names (e.g., `ippsec_idx`).
    """
    configured = (settings.redis_index or "").strip()
    ordered = [*_DEFAULT_INDEX_PRIORITY]
    if configured and configured not in ordered:
        ordered.append(configured)
    return ordered


@dataclass(slots=True)
class SearchResult:
    """Unified result from the tiered search pipeline."""

    # Formatted block ready for injection into an LLM system-prompt.
    reference_block: str

    # Which path was taken.
    used_web: bool = False

    # Raw artefacts for inspection / testing.
    vector_snippets: list[RAGSnippet] = field(default_factory=list)
    web_results: list[WebResult] = field(default_factory=list)
    top_similarity_scores: list[float] = field(default_factory=list)


def _best_vector_score(snippets: list[RAGSnippet]) -> float:
    """Return the highest similarity score from *snippets*.

    redisvl's ``vector_distance`` is a cosine *distance* (0 = identical).
    We convert to *similarity* = 1 - distance so higher is better.
    An empty list → 0.0 (no knowledge → almost certainly want the web).
    """
    if not snippets:
        return 0.0
    # The snippets are sorted ascending by score in query.py, so the first
    # element is the closest match — but let's be explicit.
    best_distance = min(s.score for s in snippets)
    return 1.0 - best_distance


def _snippet_token_cost(snippet: RAGSnippet) -> int:
    text = "\n".join(
        [
            snippet.title or "",
            snippet.url or "",
            snippet.content or "",
        ]
    )
    # Fast approximation is sufficient for dynamic context budgeting.
    return max(1, len(text) // 4)


def _snippet_priority(snippet: RAGSnippet, target_machine: str | None) -> tuple[int, float, int]:
    target = (target_machine or "").strip().lower()
    machine = (snippet.machine or "").strip().lower()
    searchable = " ".join(
        [
            machine,
            (snippet.title or "").lower(),
            (snippet.content or "").lower(),
        ]
    )
    is_target_specific = bool(target) and (machine == target or target in searchable)
    looks_generic = any(hint in searchable for hint in _GENERIC_METHOD_HINTS)
    priority = 2 if is_target_specific else 0
    if looks_generic:
        priority -= 1
    similarity = 1.0 - float(snippet.score or 0.0)
    return priority, similarity, -_snippet_token_cost(snippet)


def _trim_snippets_to_budget(
    snippets: list[RAGSnippet],
    *,
    target_machine: str | None,
    token_budget: int,
) -> list[RAGSnippet]:
    if not snippets or token_budget <= 0:
        return snippets

    ranked = sorted(
        snippets,
        key=lambda snippet: _snippet_priority(snippet, target_machine),
        reverse=True,
    )

    kept: list[RAGSnippet] = []
    used_tokens = 0
    for snippet in ranked:
        cost = _snippet_token_cost(snippet)
        if used_tokens + cost > token_budget and kept:
            continue
        kept.append(snippet)
        used_tokens += cost
        if used_tokens >= token_budget:
            break

    return kept or ranked[:1]


async def tiered_search(
    query: str,
    settings: Settings,
    history_commands: list[str] | None = None,
    *,
    target_machine: str | None = None,
    vector_threshold: float = _DEFAULT_VECTOR_THRESHOLD,
    force_web: bool = False,
    allow_web: bool = True,
    max_web_results: int = 5,
    top_k: int = 3,
    source_filters: list[str] | None = None,
    reference_token_budget: int = _REFERENCE_TOKEN_BUDGET,
    rag_timeout: float | None = None,
) -> SearchResult:
    """Run the tiered search pipeline.

    Parameters
    ----------
    query:
        The user prompt or tool command to search for.
    settings:
        Application settings (Redis URL, SearXNG URL, …).
    history_commands:
        Recent shell commands for expanded query context.
    vector_threshold:
        Minimum similarity score to consider the local index sufficient.
    force_web:
        Skip the threshold check and always hit SearXNG.
    max_web_results:
        Cap on live results forwarded to the LLM.
    """
    # --- Timeout wrapper ---------------------------------------------------
    if rag_timeout is None:
        rag_timeout = getattr(settings, "rag_timeout", 10.0)

    _search_result: SearchResult | None = None
    _timed_out = False

    async def _run_pipeline() -> SearchResult:
        # --- Tier 1: Local Redis Vector DB -----------------------------------
        vector_snippets = retrieve_reference_material(
            settings,
            command_or_prompt=query,
            context_commands=history_commands,
            top_k=top_k,
            target_machine=target_machine,
            index_order=_resolve_index_priority(settings),
            source_filters=source_filters,
        )
        nonlocal _search_result
        _search_result = SearchResult(
            reference_block=format_reference_material(vector_snippets) if vector_snippets else "Reference Material: none",
            vector_snippets=vector_snippets,
            web_results=[],
            top_similarity_scores=top_similarity_scores(vector_snippets, top_n=3),
        )
        vector_snippets = _trim_snippets_to_budget(
            vector_snippets,
            target_machine=target_machine,
            token_budget=reference_token_budget,
        )
        # Update partial result with trimmed snippets
        _search_result.vector_snippets = vector_snippets
        best_score = _best_vector_score(vector_snippets)

        needs_web = force_web or (best_score < vector_threshold)
        web_results: list[WebResult] = []

        # --- Tier 2: Live SearXNG (last resort) — only when pedagogy permits ---
        if needs_web and allow_web and settings.searxng_base_url:
            web_results = await web_search(
                query,
                base_url=settings.searxng_base_url,
                max_results=max_web_results,
            )

        # --- Compose reference block -----------------------------------------
        parts: list[str] = []

        if vector_snippets:
            parts.append(format_reference_material(vector_snippets))

        if web_results:
            no_local = not bool(vector_snippets)
            preamble = (
                "I've checked local Gibson sources (IppSec, GTFOBins/LOLBAS, HackTricks, Payloads). "
                "We're in uncharted territory. I'm hitting the web for this one.\n"
                if no_local else
                "Zero Cool had to check the live web for this one.\n"
            )
            parts.append(
                preamble
                + "Cite the URLs below if you use any of this information.\n\n"
                + format_web_results(web_results)
            )

        reference_block = "\n\n".join(parts) if parts else "Reference Material: none"

        return SearchResult(
            reference_block=reference_block,
            used_web=bool(web_results),
            vector_snippets=vector_snippets,
            web_results=web_results,
            top_similarity_scores=top_similarity_scores(vector_snippets, top_n=3),
        )

    try:
        _search_result = await asyncio.wait_for(_run_pipeline(), timeout=rag_timeout)
    except (_AsyncioTimeoutError, TimeoutError):
        logger.warning("RAG search timed out after %.1fs", rag_timeout)
        _timed_out = True
        _search_result = SearchResult(
            reference_block=(
                "Reference Material (partial): "
                "Search timed out. Consider increasing RAG_TIMEOUT setting."
            ),
            vector_snippets=_search_result.vector_snippets if _search_result else [],
            web_results=_search_result.web_results if _search_result else [],
            top_similarity_scores=(
                top_similarity_scores(_search_result.vector_snippets, top_n=3)
                if _search_result else []
            ),
        )

    return _search_result

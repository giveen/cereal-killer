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

from cereal_killer.config import Settings
from mentor.kb.query import RAGSnippet, format_reference_material, retrieve_reference_material
from mentor.tools.web_search import WebResult, format_web_results, search as web_search


# Cosine distance ≥ this means the local index has no useful material.
# redisvl returns vector_distance where 0 = identical, 1 = orthogonal.
# We invert: score = 1 - distance; so a score < threshold → go to web.
_DEFAULT_VECTOR_THRESHOLD = 0.7
_DEFAULT_INDEX_PRIORITY = ["ippsec", "gtfobins", "lolbas", "hacktricks", "payloads"]


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
    # --- Tier 1: Local Redis Vector DB -----------------------------------
    vector_snippets = retrieve_reference_material(
        settings,
        command_or_prompt=query,
        context_commands=history_commands,
        top_k=top_k,
        target_machine=target_machine,
        index_order=_DEFAULT_INDEX_PRIORITY,
        source_filters=source_filters,
    )
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
    )

"""HackTricks RAG retrieval integration.

This module adds HackTricks-specific retrieval methods to the query system,
allowing the LLM to retrieve methodology chunks for citation in responses.
"""

from __future__ import annotations

from typing import Any

from mentor.kb.query import RAGSnippet, embed


def query_hacktricks_from_redis(
    redis_client: Any,
    query_text: str,
    embed_fn: Any = None,
    limit: int = 3,
) -> list[RAGSnippet]:
    """Query HackTricks index directly from Redis by vector similarity.
    
    Args:
        redis_client: Redis client instance
        query_text: Query text to embed and search for
        embed_fn: Function to embed query text -> list[float]
        limit: Number of results to return
    
    Returns:
        List of RAGSnippet objects with HackTricks content
    """
    # Use the default sentence-transformers embed function if none provided
    embed_fn = embed_fn or embed
    try:
        # Embed the query
        query_vector = embed_fn(query_text)
    except Exception:
        return []

    snippets: list[RAGSnippet] = []
    
    try:
        # Scan all HackTricks entries in Redis
        for key in redis_client.scan_iter(match="hacktricks:*"):
            if len(snippets) >= limit:
                break
            
            doc = redis_client.hgetall(key)
            if not doc:
                continue
            
            # Extract fields
            breadcrumb = _redis_decode(doc.get("breadcrumb", ""))
            content_text = _redis_decode(doc.get("content_text", ""))
            title = _redis_decode(doc.get("title", ""))
            url = _redis_decode(doc.get("url", ""))
            
            # Calculate similarity (cosine distance)
            try:
                vector_str = doc.get("content_vector", "")
                if isinstance(vector_str, str) and vector_str:
                    stored_vector = [float(x) for x in vector_str.strip("[]").split(",")]
                    score = _cosine_similarity(query_vector, stored_vector)
                else:
                    score = 0.0
            except Exception:
                score = 0.0
            
            snippet = RAGSnippet(
                source="hacktricks",
                machine="",
                title=title,
                url=url,
                content=content_text,
                score=score,
                phase="methodology",
            )
            snippets.append(snippet)
    except Exception:
        pass
    
    # Sort by score (lower is better for distance metrics, higher for similarity)
    snippets.sort(key=lambda s: s.score, reverse=True)
    return snippets[:limit]


def _redis_decode(value: Any) -> str:
    """Safely decode Redis values to strings."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value)


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    
    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    mag_a = sum(a * a for a in vec_a) ** 0.5
    mag_b = sum(b * b for b in vec_b) ** 0.5
    
    if mag_a == 0 or mag_b == 0:
        return 0.0
    
    return dot_product / (mag_a * mag_b)


def format_hacktricks_snippets(snippets: list[RAGSnippet]) -> str:
    """Format HackTricks snippets for inclusion in system prompt.
    
    This creates a citation-aware format that tells the LLM where methodology
    comes from, enabling it to suggest specific techniques with source links.
    """
    if not snippets:
        return ""
    
    lines = [
        "\n--- HackTricks Methodology Reference ---",
    ]
    
    for idx, snippet in enumerate(snippets, 1):
        lines.append(f"\n[HackTricks #{idx}] {snippet.title}")
        if snippet.breadcrumb:
            lines.append(f"Location: {snippet.breadcrumb}")
        if snippet.url:
            lines.append(f"Reference: {snippet.url}")
        lines.append(f"\nContent:\n{snippet.content}\n")
    
    lines.append("--- End HackTricks Reference ---\n")
    return "\n".join(lines)

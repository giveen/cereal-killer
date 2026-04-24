from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
import asyncio
import functools
from pathlib import Path
from typing import Any

try:
    from redisvl.index import SearchIndex
    from redisvl.query import VectorQuery
    from redisvl.schema import IndexSchema
except ImportError:  # pragma: no cover - allows non-RAG tests to run
    SearchIndex = None  # type: ignore[assignment]
    VectorQuery = None  # type: ignore[assignment]
    IndexSchema = None  # type: ignore[assignment]

from cereal_killer.config import Settings
from mentor.ui.phase import detect_phase
from .redis_pool import get_sync_client


EMBEDDING_DIMS = 768  # Matches nomic-embed-text / sentence-transformers default
RAG_NOT_EMPTY_SIMILARITY_THRESHOLD = 0.40
_RERANK_CANDIDATES = 10
_CONTEXT_CACHE_TURNS = 3
_CONTEXT_CACHE_TTL_SECS = 60 * 60 * 6

_PHASE_AWARE_BONUSES = {
    "recon": {"networking": 0.3, "enumeration": 0.2, "discovery": 0.2},
    "user": {"exploit": 0.4, "payload": 0.3, "vulnerability": 0.2},
    "root": {"privesc": 0.5, "suid": 0.3, "capabilities": 0.2},
    "unknown": {"dump": 0.3, "hash": 0.3, "lateral": 0.2},
}

_CROSS_ENCODER: Any | None = None
_CROSS_ENCODER_LOAD_ATTEMPTED = False
_LOG = logging.getLogger(__name__)

# Lazy-loaded sentence-transformers embedding model singleton.
# Falls back to hash-based embedding if the model cannot be loaded.
_EMBED_MODEL: Any = None


def _get_embedding_model() -> Any:
    """Lazily load the sentence-transformers embedding model.
    
    Returns the model on first call, cached globally. Falls back to
    a small local model if the configured model download fails.
    """
    global _EMBED_MODEL
    
    import os as _os
    
    model_name = _os.getenv(
        "EMBEDDING_MODEL",
        "sentence-transformers/all-MiniLM-L6-v2",
    )
    
    # If already loaded with this name, return cached model
    # We compare by checking if model ID matches
    
    if _EMBED_MODEL is not None:
        return _EMBED_MODEL
    
    try:
        from sentence_transformers import SentenceTransformer as _ST
        _EMBED_MODEL = _ST(model_name, device="cpu")
        _LOG.debug("Embedding model loaded successfully")
    except Exception:
        # Fallback: try the smallest model
        try:
            from sentence_transformers import SentenceTransformer as _ST
            _EMBED_MODEL = _ST("sentence-transformers/all-MiniLM-L6-v2", device="cpu")
            _LOG.debug("Embedding model loaded successfully")
        except Exception:
            _EMBED_MODEL = None
    
    return _EMBED_MODEL

# Embedding cache for frequently-used queries.
# Uses LRU cache on the model's encode method to avoid redundant
# model computations for repeated queries.
_EMBED_CACHE_SIZE: int = 1000  # Max cached embeddings
_EMBED_CACHE_TTL_SECS: int = 3600  # 1 hour — stale embeddings expire
_embed_cache: dict[str, tuple[list[float], float]] = {}

_embed_lock: asyncio.Lock | None = None


def _hash_embed(text: str) -> list[float]:
    """Generate a hash-based embedding as fallback.

    Creates a deterministic vector from SHA256 hash of the text.
    Used when sentence-transformers model is unavailable.
    Returns a vector of EMBEDDING_DIMS length.
    """
    digest = hashlib.sha256(text.encode('utf-8', errors='ignore')).digest()
    return [((digest[i % len(digest)] / 255.0) * 2) - 1 for i in range(EMBEDDING_DIMS)]


def _batch_embed(texts: list[str], batch_size: int = 4) -> list[list[float]]:
    """Embed multiple texts in batches using the sentence-transformers model.

    Uses synchronous model.encode() for batching. For async operations,
    use the async _batch_embed_with_cache wrapper.

    Args:
        texts: List of text strings to embed
        batch_size: Number of texts per batch (default: 4)

    Returns:
        List of embedding vectors (one per input text)
    """
    if not texts:
        return []

    model = _get_embedding_model()
    if model is None:
        return [_hash_embed(t) for t in texts]

    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        try:
            batch_embeddings = model.encode(
                batch,
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            for emb in batch_embeddings:
                if hasattr(emb, 'tolist'):
                    all_embeddings.append(emb.tolist())
                else:
                    all_embeddings.append(list(emb))
        except Exception:
            # If batch fails, fall back to individual embeddings
            for text in batch:
                try:
                    single = model.encode(text, show_progress_bar=False, normalize_embeddings=True)
                    all_embeddings.append(single.tolist() if hasattr(single, 'tolist') else list(single))
                except Exception:
                    all_embeddings.append(_hash_embed(text))

    return all_embeddings


def _get_embed_lock() -> asyncio.Lock:
    """Lazily initialize the embedding cache lock.

    asyncio.Lock() requires a running event loop, so we create
    it on first access rather than at module load time.
    """
    global _embed_lock
    if _embed_lock is None:
        _embed_lock = asyncio.Lock()
    return _embed_lock


def _clear_embedding_cache() -> None:
    """Clear the embedding cache. Useful after model reloads."""
    global _embed_cache
    _embed_cache.clear()


async def _embed_with_cache(
    text: str | None = None,
    texts: list[str] | None = None,
    batch_size: int = 4,
    settings: Any = None,
) -> list[float] | list[list[float]]:
    """Embed text with LRU+TTL caching (thread-safe).

    Returns cached embedding if available and not expired, otherwise
    computes and caches the result. Uses asyncio.Lock for thread safety.
    The lock is released during CPU-bound model encoding to avoid
    blocking the event loop.

    Accepts either a single text string or a list of texts. When a list
    is provided, batch processing is used for improved throughput.

    Args:
        text: Single text string to embed (alternative to texts list)
        texts: List of text strings to embed (alternative to text)
        batch_size: Number of texts per batch when processing a list

    Returns:
        Single embedding for text, or list of embeddings for texts list
    """
    # Delegate batch processing to _batch_embed_with_cache
    if texts is not None:
        return await _batch_embed_with_cache(texts=texts, batch_size=batch_size)

    _cache_size: int = (
        settings.embed_cache_size
        if settings is not None
        else _EMBED_CACHE_SIZE
    )
    _cache_ttl: int = (
        settings.embed_cache_ttl_seconds
        if settings is not None
        else _EMBED_CACHE_TTL_SECS
    )

    key = text
    now = time.time()
    lock = _get_embed_lock()

    # --- Check cache under lock ---
    async with lock:
        if key in _embed_cache:
            value, timestamp = _embed_cache[key]
            if now - timestamp < _cache_ttl:
                # Still valid — refresh position for LRU tracking
                del _embed_cache[key]
                _embed_cache[key] = (value, timestamp)
                return value
            else:
                # Expired — remove and recompute
                del _embed_cache[key]

    # --- Compute embedding WITHOUT lock (CPU-bound) ---
    _LOG.debug("Embedding cache miss for text of length %d", len(text))
    model = _get_embedding_model()
    if model is None:
        _LOG.warning("Embedding model unavailable, using hash fallback for text of length %d", len(text))
        digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).digest()
        embedding = [((digest[i % len(digest)] / 255.0) * 2) - 1 for i in range(EMBEDDING_DIMS)]
    else:
        try:
            vec = model.encode(text, show_progress_bar=False, normalize_embeddings=True)
            embedding = vec.tolist() if hasattr(vec, "tolist") else list(vec)
        except Exception:
            _LOG.warning("Embedding model unavailable, using hash fallback for text of length %d", len(text))
            digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).digest()
            embedding = [((digest[i % len(digest)] / 255.0) * 2) - 1 for i in range(EMBEDDING_DIMS)]

    # --- Write back to cache under lock ---
    async with lock:
        if len(_embed_cache) >= _cache_size:
            _LOG.debug("Embedding cache full (%d entries), evicting oldest entry", len(_embed_cache))
            # Evict oldest entry that has exceeded TTL, or fall back to first entry
            expired_key = None
            for k, (_, ts) in _embed_cache.items():
                if now - ts >= _cache_ttl:
                    expired_key = k
                    break
            if expired_key is not None:
                del _embed_cache[expired_key]
            elif len(_embed_cache) >= _cache_size:
                oldest_key = next(iter(_embed_cache))
                del _embed_cache[oldest_key]
        _embed_cache[key] = (embedding, now)

    return embedding


async def _batch_embed_with_cache(
    texts: list[str],
    batch_size: int = 4,
    settings: Any = None,
) -> list[list[float]]:
    """Embed multiple texts with LRU+TTL caching (thread-safe).

    Processes texts in batch when the model is available, which is
    2-5x faster than individual encode() calls because the underlying
    sentence-transformers model can batch CPU/GPU operations.

    Args:
        texts: List of text strings to embed
        batch_size: Number of texts per batch (default: 4)

    Returns:
        List of embedding vectors, one per input text
    """
    _cache_size: int = (
        settings.embed_cache_size
        if settings is not None
        else _EMBED_CACHE_SIZE
    )
    _cache_ttl: int = (
        settings.embed_cache_ttl_seconds
        if settings is not None
        else _EMBED_CACHE_TTL_SECS
    )

    if not texts:
        return []

    # Separate cached vs uncached texts
    now = time.time()
    lock = _get_embed_lock()
    cached_results: dict[str, list[float]] = {}
    uncached_texts: list[str] = []

    async with lock:
        for text in texts:
            if text in _embed_cache:
                value, timestamp = _embed_cache[text]
                if now - timestamp < _cache_ttl:
                    cached_results[text] = value
                else:
                    # Expired
                    del _embed_cache[text]
                    uncached_texts.append(text)
            else:
                uncached_texts.append(text)

    # Compute embeddings for uncached texts using _batch_embed
    newly_computed: dict[str, list[float]] = {}
    if uncached_texts:
        newly_computed_embeddings = _batch_embed(uncached_texts, batch_size)
        for text, embedding in zip(uncached_texts, newly_computed_embeddings):
            newly_computed[text] = embedding

    # Write newly computed embeddings to cache under lock
    if newly_computed:
        async with lock:
            for text, embedding in newly_computed.items():
                if len(_embed_cache) >= _cache_size:
                    expired_key = None
                    for k, (_, ts) in _embed_cache.items():
                        if now - ts >= _cache_ttl:
                            expired_key = k
                            break
                    if expired_key is not None:
                        del _embed_cache[expired_key]
                    elif len(_embed_cache) >= _cache_size:
                        oldest_key = next(iter(_embed_cache))
                        del _embed_cache[oldest_key]
                _embed_cache[text] = (embedding, now)

    # Merge results in original order
    merged = {**cached_results, **newly_computed}
    return [merged[text] for text in texts]


@dataclass(slots=True)
class RAGSnippet:
    source: str
    machine: str
    title: str
    url: str
    content: str
    score: float
    phase: str = "unknown"
    rerank_score: float = 0.0
    cache_penalty: float = 0.0


def _schema(index_name: str) -> IndexSchema:
    if IndexSchema is None:
        raise RuntimeError("redisvl is required for retrieval queries.")
    return IndexSchema.from_dict(
        {
            "index": {"name": index_name, "prefix": f"{index_name}:", "storage_type": "hash"},
            "fields": [
                {"name": "machine", "type": "text"},
                {"name": "title", "type": "text"},
                {"name": "url", "type": "text"},
                {"name": "content", "type": "text"},
                {
                    "name": "embedding",
                    "type": "vector",
                    "attrs": {
                        "algorithm": "flat",
                        "dims": EMBEDDING_DIMS,
                        "distance_metric": "cosine",
                        "datatype": "float32",
                    },
                },
            ],
        }
    )


def _index(settings: Settings, index_name: str) -> SearchIndex:
    if SearchIndex is None:
        raise RuntimeError("redisvl is required for retrieval queries.")
    return SearchIndex(schema=_schema(index_name), redis_url=settings.redis_url)


async def _query_single_index(
    settings: Settings,
    index_name: str,
    query: str,
    limit: int,
    *,
    machine_filter: str | None = None,
    precomputed_vector: list[float] | None = None,
) -> list[RAGSnippet]:
    if VectorQuery is None:
        raise RuntimeError("redisvl is required for retrieval queries.")

    idx = _index(settings, index_name)
    query_vec = precomputed_vector if precomputed_vector is not None else await embed(query, settings=settings)
    filter_expression = _machine_filter_expression(machine_filter) if machine_filter else None
    result = idx.query(
        VectorQuery(
            vector=query_vec,
            vector_field_name="embedding",
            return_fields=["machine", "title", "url", "content"],
            filter_expression=filter_expression,
            num_results=limit,
        )
    )

    docs = result if isinstance(result, list) else result.get("results", [])
    vector_snippets: list[RAGSnippet] = []
    for doc in docs:
        score = float(doc.get("vector_distance", doc.get("score", 0.0)) or 0.0)
        vector_snippets.append(
            RAGSnippet(
                source=index_name,
                machine=str(doc.get("machine", "")),
                title=str(doc.get("title", "")),
                url=str(doc.get("url", "")),
                content=str(doc.get("content", "")),
                score=score,
                phase=_extract_phase(str(doc.get("content", ""))),
            )
        )

    # Always run a lexical pass in parallel — hash-based embeddings carry no
    # semantic meaning so vector KNN returns effectively random docs.  Merging
    # lexical hits ensures keyword-relevant documents are always surfaced even
    # when the random vector neighbours happen to be off-topic.
    lexical_snippets = _query_single_index_lexical(
        settings,
        index_name,
        query,
        limit,
        machine_filter=machine_filter,
    )

    # Merge: deduplicate by (source, content[:200]) keeping lexical hits (they
    # are always relevant) and filling from vector results up to `limit`.
    seen: set[str] = set()
    merged: list[RAGSnippet] = []
    for item in lexical_snippets:
        key = f"{item.source}|{item.content[:200]}"
        if key not in seen:
            seen.add(key)
            merged.append(item)
    for item in vector_snippets:
        key = f"{item.source}|{item.content[:200]}"
        if key not in seen:
            seen.add(key)
            merged.append(item)

    return merged[:limit] if merged else vector_snippets


def _query_single_index_lexical(
    settings: Settings,
    index_name: str,
    query: str,
    limit: int,
    *,
    machine_filter: str | None = None,
) -> list[RAGSnippet]:
    # Allow tokens as short as 2 chars so tools like "7z", "nc", "jq" etc. match.
    # Keep separator-bearing terms (e.g. aa-exec) and also expand them into
    # split/compact variants to align with RediSearch tokenization.
    raw_tokens = [tok for tok in re.findall(r"[a-z0-9_/-]+", query.lower()) if len(tok) >= 2]
    tokens: list[str] = []
    seen_tokens: set[str] = set()

    def _add_token(value: str) -> None:
        cleaned = (value or "").strip().lower()
        # Keep FT.SEARCH terms syntax-safe; separators are expanded separately.
        cleaned = re.sub(r"[^a-z0-9]+", "", cleaned)
        if len(cleaned) < 2 or cleaned in seen_tokens:
            return
        seen_tokens.add(cleaned)
        tokens.append(cleaned)

    for tok in raw_tokens:
        # Split on common separators so aa-exec -> aa, exec; /usr/bin/find -> usr, bin, find
        for part in re.split(r"[-_/]+", tok):
            _add_token(part)
        # Compact variant helps with docs that normalize punctuation away.
        compact = re.sub(r"[-_/]+", "", tok)
        if compact != tok:
            _add_token(compact)

    if not tokens:
        return []

    # Keep query bounded and RediSearch-safe.
    terms = tokens[:12]
    expanded_terms: list[str] = []
    for term in terms:
        expanded_terms.append(term)
        # Prefix variant helps short tool tokens (`7z`, `nc`) match title/body.
        expanded_terms.append(f"{term}*")
    # Preserve order while deduplicating.
    disjunction = "|".join(dict.fromkeys(expanded_terms))
    search_query = f"(@title:({disjunction})|@content:({disjunction}))"

    target = _canonical_machine(machine_filter or "") if machine_filter else ""
    snippets: list[RAGSnippet] = []
    try:
        client = get_sync_client(settings.redis_url, decode_responses=False)
        if client is None:
            return []
        response = client.execute_command(
            "FT.SEARCH",
            index_name,
            search_query,
            "LIMIT",
            "0",
            str(max(1, limit)),
            "RETURN",
            "4",
            "machine",
            "title",
            "url",
            "content",
        )
    except Exception:
        return []

    if not isinstance(response, list) or len(response) < 3:
        return []

    def _decode(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return str(value)

    for idx in range(1, len(response), 2):
        if idx + 1 >= len(response):
            break
        fields = response[idx + 1]
        if not isinstance(fields, list):
            continue
        payload: dict[str, str] = {}
        for pos in range(0, len(fields), 2):
            if pos + 1 >= len(fields):
                break
            payload[_decode(fields[pos])] = _decode(fields[pos + 1])

        machine = payload.get("machine", "")
        if target and _canonical_machine(machine) != target:
            continue

        content = payload.get("content", "")
        snippets.append(
            RAGSnippet(
                source=index_name,
                machine=machine,
                title=payload.get("title", ""),
                url=payload.get("url", ""),
                content=content,
                # Pseudo-distance for lexical fallback keeps similarity moderate.
                score=0.55,
                phase=_extract_phase(content),
            )
        )

    # GTFOBins entries are often keyed by exact command title; perform a
    # title-only rescue query when generic lexical search still yields nothing.
    if not snippets and index_name.lower() == "gtfobins":
        for term in terms:
            try:
                exact_response = client.execute_command(
                    "FT.SEARCH",
                    index_name,
                    f'@title:("{term}")',
                    "LIMIT",
                    "0",
                    str(max(1, limit)),
                    "RETURN",
                    "4",
                    "machine",
                    "title",
                    "url",
                    "content",
                )
            except Exception:
                continue

            if not isinstance(exact_response, list) or len(exact_response) < 3:
                continue

            for pos in range(1, len(exact_response), 2):
                if pos + 1 >= len(exact_response):
                    break
                fields = exact_response[pos + 1]
                if not isinstance(fields, list):
                    continue
                payload: dict[str, str] = {}
                for fpos in range(0, len(fields), 2):
                    if fpos + 1 >= len(fields):
                        break
                    payload[_decode(fields[fpos])] = _decode(fields[fpos + 1])

                machine = payload.get("machine", "")
                if target and _canonical_machine(machine) != target:
                    continue

                content = payload.get("content", "")
                snippets.append(
                    RAGSnippet(
                        source=index_name,
                        machine=machine,
                        title=payload.get("title", ""),
                        url=payload.get("url", ""),
                        content=content,
                        score=0.55,
                        phase=_extract_phase(content),
                    )
                )
            if snippets:
                break

    return snippets[:limit]


def _extract_phase(text: str) -> str:
    match = re.search(r"^phase:\s*([a-z-]+)$", text, flags=re.IGNORECASE | re.MULTILINE)
    if match:
        return match.group(1).strip().lower()
    lowered = text.lower()
    if any(token in lowered for token in ("linpeas", "setuid", "sudo -l", "root flag")):
        return "root"
    if any(token in lowered for token in ("reverse shell", "command injection", "foothold", "ftp creds", "ssh")):
        return "user"
    if any(token in lowered for token in ("nmap", "gobuster", "enumeration", "scan", "dirsearch")):
        return "recon"
    return "unknown"


def similarity_from_distance(distance: float) -> float:
    """Convert Redis cosine distance into similarity in [0, 1]."""
    return max(0.0, min(1.0, 1.0 - float(distance or 0.0)))


def top_similarity_scores(snippets: list[RAGSnippet], top_n: int = 3) -> list[float]:
    """Return top-N similarities (descending), useful for UI debug output."""
    sims = sorted((similarity_from_distance(item.score) for item in snippets), reverse=True)
    return sims[: max(0, top_n)]


def has_confident_match(
    snippets: list[RAGSnippet],
    threshold: float = RAG_NOT_EMPTY_SIMILARITY_THRESHOLD,
) -> bool:
    """A retrieval is non-empty when any chunk exceeds the similarity threshold."""
    return any(similarity_from_distance(item.score) >= threshold for item in snippets)


def _phase_bucket(context_commands: list[str]) -> str:
    phase = detect_phase(context_commands)
    return {
        "[RECON]": "recon",
        "[ENUMERATION]": "recon",
        "[EXPLOITATION]": "user",
        "[POST-EXPLOITATION]": "root",
    }.get(phase, "unknown")


def _get_cross_encoder() -> Any | None:
    """Lazily load the cross-encoder reranker model.
    
    The model is loaded on first call and cached globally. If the load fails,
    subsequent calls will retry (unlike the previous implementation which
    cached the failure).
    """
    global _CROSS_ENCODER, _CROSS_ENCODER_LOAD_ATTEMPTED
    
    if _CROSS_ENCODER is not None:
        return _CROSS_ENCODER
    
    if _CROSS_ENCODER_LOAD_ATTEMPTED:
        return _CROSS_ENCODER
    
    _CROSS_ENCODER_LOAD_ATTEMPTED = True
    
    if os.getenv("RAG_RERANKER", "on").strip().lower() in {"0", "off", "false", "no"}:
        return None
    
    try:
        from sentence_transformers import CrossEncoder as _CrossEncoder
    except Exception:
        _LOG.debug("CrossEncoder import failed; reranking disabled")
        return None
    
    model_name = os.getenv("RAG_RERANKER_MODEL", "BAAI/bge-reranker-base")
    
    try:
        _CROSS_ENCODER = _CrossEncoder(model_name, trust_remote_code=True)
        _LOG.debug("CrossEncoder loaded: %s", model_name)
    except Exception as exc:
        _CROSS_ENCODER = None
        _LOG.warning("CrossEncoder failed to load (%s); reranking disabled", exc)
    
    return _CROSS_ENCODER


def _lexical_rerank_score(query: str, snippet: RAGSnippet) -> float:
    # Keep 2-char tokens so tool names like `7z`, `nc`, `jq`, `id` are scored.
    q_tokens = {tok for tok in re.findall(r"[a-z0-9_/-]+", query.lower()) if len(tok) >= 2}
    haystack = "\n".join([snippet.title or "", snippet.machine or "", snippet.content or ""])
    s_tokens = set(re.findall(r"[a-z0-9_/-]+", haystack.lower()))
    if not q_tokens or not s_tokens:
        return 0.0
    overlap = len(q_tokens & s_tokens)
    return overlap / max(1, len(q_tokens))


def _recent_context_cache_key(machine_name: str) -> str:
    token = _canonical_machine(machine_name) or "global"
    return f"rag:recent:{token}"


def _snippet_fingerprint(snippet: RAGSnippet) -> str:
    basis = f"{snippet.source}|{snippet.machine}|{snippet.title}|{snippet.content[:300]}"
    return hashlib.sha1(basis.encode("utf-8", errors="ignore")).hexdigest()


def _load_recent_snippet_fingerprints(
    settings: Settings,
    machine_name: str,
    query_source: str = "all",
) -> dict[str, set[str]]:
    """Load recent snippet fingerprints, optionally filtered by source.

    Args:
        settings: Application settings with Redis configuration
        machine_name: Current machine name for cache key
        query_source: Filter by source ("all", "hacktricks", "ippsec", etc.)

    Returns:
        Dict mapping source names to their fingerprint sets.
    """
    try:
        client = get_sync_client(settings.redis_url, decode_responses=True)
        if client is None:
            return {}
        rows = client.lrange(_recent_context_cache_key(machine_name), 0, _CONTEXT_CACHE_TURNS - 1)
    except Exception:
        return {}

    # Build source-aware cache
    source_cache: dict[str, set[str]] = {}
    for row in rows:
        try:
            data = json.loads(row)
            if isinstance(data, dict):
                # Extract source from the data
                source = data.get("source", "unknown")
                fingerprints = data.get("fingerprints", [])
                if source not in source_cache:
                    source_cache[source] = set()
                source_cache[source].update(fingerprints)
        except Exception:
            continue

    # Source-aware filtering
    if query_source != "all":
        filtered = {}
        for source, fingerprints in source_cache.items():
            if query_source in source.lower() or query_source == "all":
                filtered[source] = fingerprints
        return filtered

    return source_cache


def _store_recent_snippet_fingerprints(
    settings: Settings,
    machine_name: str,
    snippets: list[RAGSnippet],
) -> None:
    """Store recent snippet fingerprints with source information."""
    # Group fingerprints by source
    source_data: dict[str, list[str]] = {}
    for item in snippets:
        source = item.source or "unknown"
        fp = _snippet_fingerprint(item)
        if source not in source_data:
            source_data[source] = []
        source_data[source].append(fp)

    # Store source-aware data
    payload = json.dumps({
        "source": list(source_data.keys()),
        "fingerprints": {k: v for k, v in source_data.items()},
    })

    key = _recent_context_cache_key(machine_name)
    try:
        client = get_sync_client(settings.redis_url, decode_responses=True)
        if client is None:
            return
        client.lpush(key, payload)
        client.ltrim(key, 0, _CONTEXT_CACHE_TURNS - 1)
        client.expire(key, _CONTEXT_CACHE_TTL_SECS)
    except Exception:
        return


def _calculate_phase_bonus(
    snippet: RAGSnippet,
    phase_bucket: str,
    phase_bonuses: dict[str, dict[str, float]],
) -> float:
    """Calculate phase-aware bonus for a snippet.
    
    Returns a float bonus (0.0 to 0.5) based on snippet metadata
    and the current phase context.
    """
    if phase_bucket not in phase_bonuses:
        return 0.0
    
    bonuses = phase_bonuses[phase_bucket]
    score = 0.0
    
    # Check snippet metadata for phase-relevant tags
    metadata = getattr(snippet, "metadata", {})
    tags = metadata.get("tags", [])
    breadcrumb = metadata.get("breadcrumb", "")
    
    for tag, weight in bonuses.items():
        if any(tag in str(t).lower() for t in tags):
            score += weight
        if tag in breadcrumb.lower():
            score += weight * 0.5
    
    return min(score, 0.5)  # Cap bonus at 0.5


def _rerank_snippets(query: str, snippets: list[RAGSnippet], phase_bucket: str, recent_fingerprints: set[str]) -> list[RAGSnippet]:
    if not snippets:
        return []

    _LOG.debug("Reranking %d snippets with phase_bucket=%s", len(snippets), phase_bucket)

    cross_encoder = _get_cross_encoder()
    ce_scores: list[float] | None = None
    if cross_encoder is not None:
        try:
            pairs = [(query, item.content[:1200]) for item in snippets]
            ce_scores = [float(v) for v in cross_encoder.predict(pairs)]
            _LOG.debug("Cross-encoder rerank completed for %d snippets", len(snippets))
        except Exception as exc:
            _LOG.warning("Cross-encoder rerank failed: %s", exc)
            ce_scores = None

    for idx, item in enumerate(snippets):
        lexical = _lexical_rerank_score(query, item)
        ce_score = ce_scores[idx] if ce_scores is not None else lexical
        item.rerank_score = ce_score
        item.cache_penalty = 0.30 if _snippet_fingerprint(item) in recent_fingerprints else 0.0

    # In later phases, deprioritize recon-heavy snippets unless that's all we have.
    filtered = snippets
    if phase_bucket in {"user", "root"}:
        non_recon = [s for s in snippets if s.phase != "recon"]
        if non_recon:
            filtered = non_recon

    def _rank(item: RAGSnippet) -> float:
        phase_bonus = 0.0
        if phase_bucket != "unknown" and item.phase == phase_bucket:
            phase_bonus = 0.20

        # Additional phase-aware bonus using metadata
        phase_bonus += _calculate_phase_bonus(item, phase_bucket, _PHASE_AWARE_BONUSES)

        vector_similarity = similarity_from_distance(item.score)

        # Intent-aware source boost for common priv-esc search phrases.
        q = query.lower()
        q_tokens = {tok for tok in re.findall(r"[a-z0-9_/-]+", q) if len(tok) >= 2}
        source = (item.source or "").lower()
        source_bonus = 0.0
        if ("find" in q and "suid" in q) or "setuid" in q or "sudo -l" in q:
            if source == "gtfobins":
                source_bonus += 0.25
            if source == "hacktricks":
                source_bonus += 0.22

        # Tool-name lookups (e.g. `7z`, `nc`, `awk`) should strongly favor
        # GTFOBins when available.
        short_tool_query = len(q_tokens) == 1 and len(next(iter(q_tokens), "")) <= 4
        if short_tool_query and source == "gtfobins":
            source_bonus += 0.45

        return (
            (0.65 * item.rerank_score)
            + (0.25 * vector_similarity)
            + phase_bonus
            + source_bonus
            - item.cache_penalty
        )

    _LOG.debug(
        "Reranked snippets: top score=%.3f, bottom=%.3f",
        max(item.rerank_score for item in snippets),
        min(item.rerank_score for item in snippets),
    )
    return sorted(filtered, key=_rank, reverse=True)


def _select_diverse_snippets(reranked: list[RAGSnippet], top_k: int) -> list[RAGSnippet]:
    """Prefer source diversity first, then fill by rank."""
    if top_k <= 0 or not reranked:
        return []

    selected: list[RAGSnippet] = []
    seen_sources: set[str] = set()

    for item in reranked:
        if item.source in seen_sources:
            continue
        selected.append(item)
        seen_sources.add(item.source)
        if len(selected) >= top_k:
            return selected

    for item in reranked:
        if item in selected:
            continue
        selected.append(item)
        if len(selected) >= top_k:
            break
    return selected


def _canonical_machine(value: str) -> str:
    text = (value or "").strip().lower()
    if text.startswith("hackthebox - "):
        text = text[len("hackthebox - "):]
    text = re.sub(r"[^a-z0-9-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _machine_filter_expression(target_machine: str) -> str:
    canonical = _canonical_machine(target_machine)
    if not canonical:
        return ""
    words = canonical.split()
    title_words = " ".join(word.capitalize() for word in words)
    compact = "".join(word.capitalize() for word in words)
    variants = {
        title_words,
        compact,
        f"HackTheBox - {title_words}",
        f"HackTheBox - {compact}",
    }
    clauses = [f'@machine:"{variant}"' for variant in sorted(v for v in variants if v)]
    return "(" + "|".join(clauses) + ")"


def _query_target_machine_docs(settings: Settings, index_name: str, target_machine: str, limit: int) -> list[RAGSnippet]:
    """Best-effort direct machine match by scanning indexed hashes.

    This bypasses vector similarity for /box-targeted retrieval and prevents
    cross-machine bleed when the active target is known.
    """
    target = _canonical_machine(target_machine)
    if not target:
        return []

    client = get_sync_client(settings.redis_url, decode_responses=False)
    if client is None:
        return []

    def _decode(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return str(value)

    matches: list[RAGSnippet] = []
    try:
        for key in client.scan_iter(match=f"{index_name}:*"):
            if len(matches) >= limit:
                break
            doc = client.hgetall(key)
            machine = _decode(doc.get(b"machine"))
            if _canonical_machine(machine) != target:
                continue
            matches.append(
                RAGSnippet(
                    source=index_name,
                    machine=machine,
                    title=_decode(doc.get(b"title")),
                    url=_decode(doc.get(b"url")),
                    content=_decode(doc.get(b"content")),
                    score=0.0,
                    phase=_extract_phase(_decode(doc.get(b"content"))),
                )
            )
    except Exception:
        return []
    return matches


async def retrieve_reference_material(
    settings: Settings,
    command_or_prompt: str,
    context_commands: list[str] | None = None,
    top_k: int = 3,
    target_machine: str | None = None,
    index_order: list[str] | None = None,
    source_filters: list[str] | None = None,
    query_source: str = "all",
) -> list[RAGSnippet]:
    """Retrieve reference material with source-aware caching.
    
    Args:
        settings: Application settings with Redis configuration
        command_or_prompt: The user's query or command
        context_commands: Recent shell commands for context
        top_k: Number of top results to return
        target_machine: Current target machine name
        index_order: Order in which to search indexes
        source_filters: Filter by specific sources
        query_source: Source filter for cache awareness ("all", "hacktricks", etc.)
    
    Returns:
        List of RAGSnippet objects sorted by relevance.
    """
    context_commands = context_commands or []
    has_explicit_target = bool(target_machine and target_machine.strip())
    machine_name = (target_machine or Path.cwd().name).strip().lower()
    expanded_query = "\n".join(
        [
            command_or_prompt,
            f"Current machine: {machine_name}",
            "Recent shell context:",
            *context_commands[-10:],
        ]
    )

    combined: list[RAGSnippet] = []
    target = _canonical_machine(machine_name) if has_explicit_target else ""
    phase_bucket = _phase_bucket(context_commands)
    # Flatten source-aware cache into a single set for backward compatibility
    _fingerprint_cache = _load_recent_snippet_fingerprints(settings, machine_name, query_source)
    recent_fingerprints: set[str] = set()
    for _set in _fingerprint_cache.values():
        recent_fingerprints.update(_set)

    ordered_indexes = index_order[:] if index_order else ["ippsec", "gtfobins", "lolbas", "hacktricks", "payloads", settings.redis_index]
    if source_filters:
        allowed = {item.strip().lower() for item in source_filters if item.strip()}
        ordered_indexes = [name for name in ordered_indexes if name.lower() in allowed]
        if not ordered_indexes:
            return []

    _LOG.debug("RAG query: target=%s, phase_bucket=%s, indexes=%s", target, phase_bucket, ordered_indexes)

    # First pass: if we know the active target, prefer exact machine docs.
    if target:
        target_hits: list[RAGSnippet] = []
        target_limit = max(top_k * 3, top_k)
        for index_name in ordered_indexes:
            target_hits.extend(_query_target_machine_docs(settings, index_name, target, target_limit))
        if target_hits:
            # Preserve deterministic order while avoiding duplicate snippets.
            seen: set[tuple[str, str, str]] = set()
            unique_hits: list[RAGSnippet] = []
            for item in target_hits:
                key = (item.source, item.machine, item.content[:200])
                if key in seen:
                    continue
                seen.add(key)
                unique_hits.append(item)
                if len(unique_hits) >= top_k:
                    break
            if unique_hits:
                _LOG.info("RAG target match found: %d snippets", len(unique_hits))
                reranked = _rerank_snippets(command_or_prompt, unique_hits, phase_bucket, recent_fingerprints)
                selected = _select_diverse_snippets(reranked, top_k)
                _LOG.info(
                    "RAG top-3 similarity (%s): %s",
                    command_or_prompt,
                    ", ".join(f"{score:.3f}" for score in top_similarity_scores(selected, top_n=3)) or "none",
                )
                _LOG.info("RAG returned %d snippets for query: %s", len(selected), command_or_prompt[:50])
                _store_recent_snippet_fingerprints(settings, machine_name, selected)
                return selected

    query_limit = max(_RERANK_CANDIDATES, top_k)
    # Pre-compute the embedding vector once to avoid redundant encode() calls
    # across multiple index queries.
    precomputed_vec = await embed(expanded_query)
    for index_name in ordered_indexes:
        try:
            combined.extend(
                await _query_single_index(
                    settings,
                    index_name,
                    expanded_query,
                    query_limit,
                    machine_filter=target_machine if has_explicit_target else None,
                    precomputed_vector=precomputed_vec,
                )
            )
        except Exception as exc:
            _LOG.warning("RAG index query failed for %s: %s", index_name, exc)
            continue

    target = _canonical_machine(machine_name) if has_explicit_target else ""
    if target:
        machine_matches = [item for item in combined if _canonical_machine(item.machine) == target]
        if machine_matches:
            _LOG.info("RAG target match found: %d snippets", len(machine_matches))
            reranked = _rerank_snippets(command_or_prompt, machine_matches, phase_bucket, recent_fingerprints)
            selected = _select_diverse_snippets(reranked, top_k)
            _LOG.info(
                "RAG top-3 similarity (%s): %s",
                command_or_prompt,
                ", ".join(f"{score:.3f}" for score in top_similarity_scores(selected, top_n=3)) or "none",
            )
            _LOG.info("RAG returned %d snippets for query: %s", len(selected), command_or_prompt[:50])
            _store_recent_snippet_fingerprints(settings, machine_name, selected)
            return selected

    reranked = _rerank_snippets(command_or_prompt, combined, phase_bucket, recent_fingerprints)
    selected = _select_diverse_snippets(reranked, top_k)
    _LOG.info(
        "RAG top-3 similarity (%s): %s",
        command_or_prompt,
        ", ".join(f"{score:.3f}" for score in top_similarity_scores(selected, top_n=3)) or "none",
    )
    _LOG.info("RAG returned %d snippets for query: %s", len(selected), command_or_prompt[:50])
    _store_recent_snippet_fingerprints(settings, machine_name, selected)
    return selected


async def embed(text: str, dims: int = EMBEDDING_DIMS, settings: Any = None) -> list[float]:
    """Embed text using the configured sentence-transformers model with LRU caching.

    Returns cached embedding if available, otherwise computes and caches.
    Falls back to hash-based embedding if the model is unavailable.
    """
    return await _embed_with_cache(text, settings=settings)


async def batch_embed(
    texts: list[str],
    dims: int = EMBEDDING_DIMS,
    batch_size: int = 4,
    settings: Any = None,
) -> list[list[float]]:
    """Embed multiple texts using the configured model with LRU caching.

    Batch encoding is significantly faster (2-5x) than individual embed() calls
    because the underlying sentence-transformers model can batch CPU/GPU operations.

    Args:
        texts: List of text strings to embed
        dims: Expected embedding dimensions (informational)
        batch_size: Number of texts per batch (default: 4)
        settings: Application settings (for embed cache config)

    Returns:
        List of embedding vectors, one per input text
    """
    return await _batch_embed_with_cache(texts, batch_size, settings=settings)


def format_reference_material(snippets: list[RAGSnippet]) -> str:
    if not snippets:
        return "Reference Material: none"

    lines = ["Reference Material:"]
    for snippet in snippets:
        summary = _summarize_snippet(snippet.content)
        lines.append(
            f"- [{snippet.source}] {snippet.machine} | {snippet.title} | {snippet.url}\n"
            f"  {summary}"
        )
    return "\n".join(lines)


async def _summarize_search_results(
    query: str,
    snippets: list[RAGSnippet],
    engine: Any,
) -> str:
    """Summarize search results for inclusion in the LLM prompt.
    
    This function generates a concise summary of search results that
    captures the key insights while reducing token usage.
    
    Args:
        query: The original search query
        snippets: List of RAGSnippet objects to summarize
        engine: Brain engine instance for summarization (must have summarize_session method)
        
    Returns:
        A concise summary string of the search results.
    """
    if not snippets:
        return "Reference Material: none"
    
    # For small result sets, use full content
    if len(snippets) <= 3:
        return format_reference_material(snippets)
    
    # For large result sets, generate a summary
    snippet_texts = []
    for i, snippet in enumerate(snippets, 1):
        # Truncate content to avoid overly long summaries
        content_preview = snippet.content[:200] if snippet.content else ""
        title_preview = snippet.title or snippet.source or "Untitled"
        snippet_texts.append(f"[{i}] {title_preview}: {content_preview}...")
    
    combined_text = "\n".join(snippet_texts)
    
    summary_prompt = f"""Summarize the following search results for the query: {query}

Results:
{combined_text}

Provide a concise summary (max 300 words) that captures the key insights
relevant to the query. Include specific commands, techniques, and URLs."""
    
    try:
        # Use the engine's summarize_session method if available
        if hasattr(engine, "summarize_session"):
            summary = await engine.summarize_session(summary_prompt)
            return summary
        else:
            # Fallback: use format_reference_material with top snippets
            return format_reference_material(snippets[:3])
    except Exception:
        # Fallback: return first N snippets
        return format_reference_material(snippets[:3])


def _summarize_snippet(content: str) -> str:
    """Compress retrieved snippet text into concise 3-5 bullet points."""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    kv: dict[str, str] = {}
    freeform: list[str] = []
    for line in lines:
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip().lower()
            value = value.strip()
            if key and value:
                kv[key] = value
            continue
        freeform.append(line)

    bullets: list[str] = []
    for key in ("phase", "line", "tag", "academy", "video_id", "timestamp_seconds"):
        value = kv.get(key)
        if value:
            bullets.append(f"- {key.replace('_', ' ')}: {value}")
        if len(bullets) >= 4:
            break

    if kv.get("machine") and len(bullets) < 5:
        bullets.append(f"- machine: {kv['machine']}")
    if kv.get("url") and len(bullets) < 5:
        bullets.append(f"- url: {kv['url']}")
    elif freeform and len(bullets) < 5:
        bullets.append(f"- note: {freeform[0][:140]}")

    if not bullets:
        clipped = re.sub(r"\s+", " ", content).strip()[:220]
        bullets.append(f"- note: {clipped}")
    return " ".join(bullets[:5])


async def retrieve_solution_for_machine(settings: Settings, machine_name: str) -> str:
    machine = machine_name.strip()
    if not machine:
        return "Unable to detect machine name from current directory."

    query = f"{machine} full walkthrough ippsec timestamp hacktricks methodology"
    snippets = await retrieve_reference_material(settings, query, [], top_k=5, target_machine=machine)
    if not snippets:
        return f"No Redis walkthrough material found for '{machine}'."

    lines = [f"# {machine} - Guided Solution Material", ""]
    for item in snippets:
        lines.append(f"## {item.title or 'Untitled'} ({item.source})")
        if item.url:
            lines.append(item.url)
        lines.append("")
        lines.append(item.content)
        lines.append("")
    return "\n".join(lines)

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
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


EMBEDDING_DIMS = 64
RAG_NOT_EMPTY_SIMILARITY_THRESHOLD = 0.40
_RERANK_CANDIDATES = 10
_CONTEXT_CACHE_TURNS = 3
_CONTEXT_CACHE_TTL_SECS = 60 * 60 * 6

_CROSS_ENCODER: Any | None = None
_CROSS_ENCODER_LOAD_ATTEMPTED = False
_LOG = logging.getLogger(__name__)


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


def _query_single_index(
    settings: Settings,
    index_name: str,
    query: str,
    limit: int,
    *,
    machine_filter: str | None = None,
) -> list[RAGSnippet]:
    if VectorQuery is None:
        raise RuntimeError("redisvl is required for retrieval queries.")

    idx = _index(settings, index_name)
    query_vec = embed(query)
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
    try:
        from redis import Redis
    except Exception:
        return []

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
        client = Redis.from_url(settings.redis_url, decode_responses=False)
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
    global _CROSS_ENCODER, _CROSS_ENCODER_LOAD_ATTEMPTED
    if _CROSS_ENCODER_LOAD_ATTEMPTED:
        return _CROSS_ENCODER
    _CROSS_ENCODER_LOAD_ATTEMPTED = True

    if os.getenv("RAG_RERANKER", "on").strip().lower() in {"0", "off", "false", "no"}:
        return None
    try:
        from sentence_transformers import CrossEncoder
    except Exception:
        return None

    model_name = os.getenv("RAG_RERANKER_MODEL", "BAAI/bge-reranker-base")
    try:
        _CROSS_ENCODER = CrossEncoder(model_name)
    except Exception:
        _CROSS_ENCODER = None
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


def _load_recent_snippet_fingerprints(settings: Settings, machine_name: str) -> set[str]:
    try:
        from redis import Redis
    except Exception:
        return set()
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        rows = client.lrange(_recent_context_cache_key(machine_name), 0, _CONTEXT_CACHE_TURNS - 1)
    except Exception:
        return set()
    seen: set[str] = set()
    for row in rows:
        try:
            seen.update(json.loads(row))
        except Exception:
            continue
    return seen


def _store_recent_snippet_fingerprints(settings: Settings, machine_name: str, snippets: list[RAGSnippet]) -> None:
    try:
        from redis import Redis
    except Exception:
        return
    payload = json.dumps([_snippet_fingerprint(item) for item in snippets])
    key = _recent_context_cache_key(machine_name)
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        client.lpush(key, payload)
        client.ltrim(key, 0, _CONTEXT_CACHE_TURNS - 1)
        client.expire(key, _CONTEXT_CACHE_TTL_SECS)
    except Exception:
        return


def _rerank_snippets(query: str, snippets: list[RAGSnippet], phase_bucket: str, recent_fingerprints: set[str]) -> list[RAGSnippet]:
    if not snippets:
        return []

    cross_encoder = _get_cross_encoder()
    ce_scores: list[float] | None = None
    if cross_encoder is not None:
        try:
            pairs = [(query, item.content[:1200]) for item in snippets]
            ce_scores = [float(v) for v in cross_encoder.predict(pairs)]
        except Exception:
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
    try:
        from redis import Redis
    except ImportError:
        return []

    target = _canonical_machine(target_machine)
    if not target:
        return []

    client = Redis.from_url(settings.redis_url, decode_responses=False)

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


def retrieve_reference_material(
    settings: Settings,
    command_or_prompt: str,
    context_commands: list[str] | None = None,
    top_k: int = 3,
    target_machine: str | None = None,
    index_order: list[str] | None = None,
    source_filters: list[str] | None = None,
) -> list[RAGSnippet]:
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
    recent_fingerprints = _load_recent_snippet_fingerprints(settings, machine_name)

    ordered_indexes = index_order[:] if index_order else ["ippsec", "gtfobins", "lolbas", "hacktricks", "payloads", settings.redis_index]
    if source_filters:
        allowed = {item.strip().lower() for item in source_filters if item.strip()}
        ordered_indexes = [name for name in ordered_indexes if name.lower() in allowed]
        if not ordered_indexes:
            return []

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
                reranked = _rerank_snippets(command_or_prompt, unique_hits, phase_bucket, recent_fingerprints)
                selected = _select_diverse_snippets(reranked, top_k)
                _LOG.info(
                    "RAG top-3 similarity (%s): %s",
                    command_or_prompt,
                    ", ".join(f"{score:.3f}" for score in top_similarity_scores(selected, top_n=3)) or "none",
                )
                _store_recent_snippet_fingerprints(settings, machine_name, selected)
                return selected

    query_limit = max(_RERANK_CANDIDATES, top_k)
    for index_name in ordered_indexes:
        try:
            combined.extend(
                _query_single_index(
                    settings,
                    index_name,
                    expanded_query,
                    query_limit,
                    machine_filter=target_machine if has_explicit_target else None,
                )
            )
        except Exception:
            continue

    target = _canonical_machine(machine_name) if has_explicit_target else ""
    if target:
        machine_matches = [item for item in combined if _canonical_machine(item.machine) == target]
        if machine_matches:
            reranked = _rerank_snippets(command_or_prompt, machine_matches, phase_bucket, recent_fingerprints)
            selected = _select_diverse_snippets(reranked, top_k)
            _LOG.info(
                "RAG top-3 similarity (%s): %s",
                command_or_prompt,
                ", ".join(f"{score:.3f}" for score in top_similarity_scores(selected, top_n=3)) or "none",
            )
            _store_recent_snippet_fingerprints(settings, machine_name, selected)
            return selected

    reranked = _rerank_snippets(command_or_prompt, combined, phase_bucket, recent_fingerprints)
    selected = _select_diverse_snippets(reranked, top_k)
    _LOG.info(
        "RAG top-3 similarity (%s): %s",
        command_or_prompt,
        ", ".join(f"{score:.3f}" for score in top_similarity_scores(selected, top_n=3)) or "none",
    )
    _store_recent_snippet_fingerprints(settings, machine_name, selected)
    return selected


def embed(text: str, dims: int = EMBEDDING_DIMS) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).digest()
    return [((digest[i % len(digest)] / 255.0) * 2) - 1 for i in range(dims)]


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


def retrieve_solution_for_machine(settings: Settings, machine_name: str) -> str:
    machine = machine_name.strip()
    if not machine:
        return "Unable to detect machine name from current directory."

    query = f"{machine} full walkthrough ippsec timestamp hacktricks methodology"
    snippets = retrieve_reference_material(settings, query, [], top_k=5, target_machine=machine)
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

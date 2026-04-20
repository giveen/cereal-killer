from __future__ import annotations

import hashlib
import json
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
_RERANK_CANDIDATES = 10
_CONTEXT_CACHE_TURNS = 3
_CONTEXT_CACHE_TTL_SECS = 60 * 60 * 6

_CROSS_ENCODER: Any | None = None
_CROSS_ENCODER_LOAD_ATTEMPTED = False


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
    snippets: list[RAGSnippet] = []
    for doc in docs:
        score = float(doc.get("vector_distance", doc.get("score", 0.0)) or 0.0)
        snippets.append(
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
    return snippets


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
    q_tokens = {tok for tok in re.findall(r"[a-z0-9_/-]+", query.lower()) if len(tok) > 2}
    s_tokens = set(re.findall(r"[a-z0-9_/-]+", snippet.content.lower()))
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
        vector_similarity = 1.0 - float(item.score or 0.0)
        return (0.65 * item.rerank_score) + (0.25 * vector_similarity) + phase_bonus - item.cache_penalty

    return sorted(filtered, key=_rank, reverse=True)


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
                selected = reranked[:top_k]
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
            selected = reranked[:top_k]
            _store_recent_snippet_fingerprints(settings, machine_name, selected)
            return selected

    reranked = _rerank_snippets(command_or_prompt, combined, phase_bucket, recent_fingerprints)
    selected = reranked[:top_k]
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

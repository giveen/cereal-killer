from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

try:
    from redisvl.index import SearchIndex
    from redisvl.query import VectorQuery
    from redisvl.schema import IndexSchema
except ImportError:  # pragma: no cover - allows non-RAG tests to run
    SearchIndex = None  # type: ignore[assignment]
    VectorQuery = None  # type: ignore[assignment]
    IndexSchema = None  # type: ignore[assignment]

from cereal_killer.config import Settings


EMBEDDING_DIMS = 64


@dataclass(slots=True)
class RAGSnippet:
    source: str
    machine: str
    title: str
    url: str
    content: str
    score: float


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


def _query_single_index(settings: Settings, index_name: str, query: str, limit: int) -> list[RAGSnippet]:
    if VectorQuery is None:
        raise RuntimeError("redisvl is required for retrieval queries.")

    idx = _index(settings, index_name)
    query_vec = embed(query)
    result = idx.query(
        VectorQuery(
            vector=query_vec,
            vector_field_name="embedding",
            return_fields=["machine", "title", "url", "content"],
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
            )
        )
    return snippets


def _canonical_machine(value: str) -> str:
    text = (value or "").strip().lower()
    if text.startswith("hackthebox - "):
        text = text[len("hackthebox - "):]
    text = re.sub(r"[^a-z0-9-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


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
) -> list[RAGSnippet]:
    context_commands = context_commands or []
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
    target = _canonical_machine(machine_name)

    # First pass: if we know the active target, prefer exact machine docs.
    if target:
        target_hits: list[RAGSnippet] = []
        target_limit = max(top_k * 3, top_k)
        for index_name in ("ippsec", "hacktricks", settings.redis_index):
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
                return unique_hits

    query_limit = max(top_k * 6, top_k)
    for index_name in ("ippsec", "hacktricks", settings.redis_index):
        try:
            combined.extend(_query_single_index(settings, index_name, expanded_query, query_limit))
        except Exception:
            continue

    target = _canonical_machine(machine_name)
    if target:
        machine_matches = [item for item in combined if _canonical_machine(item.machine) == target]
        if machine_matches:
            machine_matches.sort(key=lambda item: item.score)
            return machine_matches[:top_k]

    combined.sort(key=lambda item: item.score)
    return combined[:top_k]


def embed(text: str, dims: int = EMBEDDING_DIMS) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).digest()
    return [((digest[i % len(digest)] / 255.0) * 2) - 1 for i in range(dims)]


def format_reference_material(snippets: list[RAGSnippet]) -> str:
    if not snippets:
        return "Reference Material: none"

    lines = ["Reference Material:"]
    for snippet in snippets:
        lines.append(
            f"- [{snippet.source}] {snippet.machine} | {snippet.title} | {snippet.url}\n"
            f"  {snippet.content[:280]}"
        )
    return "\n".join(lines)


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

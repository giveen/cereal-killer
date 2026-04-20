from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import re
import struct
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cereal_killer.config import Settings

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

try:
    from redis import Redis
except ImportError:  # pragma: no cover
    Redis = None  # type: ignore[assignment]

try:
    from redisvl.index import SearchIndex
except ImportError:  # pragma: no cover
    SearchIndex = None  # type: ignore[assignment]

try:
    from redisvl.schema import IndexSchema
except ImportError:  # pragma: no cover
    IndexSchema = None  # type: ignore[assignment]

from mentor.kb.query import EMBEDDING_DIMS, embed


SYNC_KEY_PREFIX = "kb:sync:"


@dataclass(slots=True)
class SourceConfig:
    name: str
    index: str
    source_type: str
    clone_url: str
    local_path: Path
    parse_mode: str
    content_glob: str


@dataclass(slots=True)
class LibraryChunk:
    index: str
    source: str
    source_type: str
    title: str
    url: str
    content: str
    machine: str
    tags: dict[str, str]

    def to_hash(self, vector: list[float], updated_at: str) -> dict[str, Any]:
        tags = dict(self.tags)
        tags.setdefault("source", self.source)
        tags.setdefault("type", self.source_type)
        return {
            "machine": self.machine,
            "title": self.title,
            "url": self.url,
            "content": self.content,
            # RediSearch VECTOR(FLOAT32, DIM 64) requires a raw binary blob.
            "embedding": struct.pack(f"<{len(vector)}f", *vector),
            "source": self.source,
            "type": self.source_type,
            "tags": ",".join(f"{k}:{v}" for k, v in sorted(tags.items())),
            "updated_at": updated_at,
        }


def _source_schema(index_name: str) -> IndexSchema:
    if IndexSchema is None:
        raise RuntimeError("redisvl is required for multi-source ingestion.")
    return IndexSchema.from_dict(
        {
            "index": {"name": index_name, "prefix": f"{index_name}:", "storage_type": "hash"},
            "fields": [
                {"name": "machine", "type": "text"},
                {"name": "title", "type": "text"},
                {"name": "url", "type": "text"},
                {"name": "content", "type": "text"},
                {"name": "source", "type": "tag"},
                {"name": "type", "type": "tag"},
                {"name": "tags", "type": "text"},
                {"name": "updated_at", "type": "text"},
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


def _ensure_index(settings: Settings, index_name: str) -> None:
    if SearchIndex is None:
        raise RuntimeError("redisvl is required for multi-source ingestion.")
    idx = SearchIndex(schema=_source_schema(index_name), redis_url=settings.redis_url)
    idx.create(overwrite=False, drop=False)


def load_sources_config(config_path: Path) -> list[SourceConfig]:
    if yaml is None:
        raise RuntimeError("PyYAML is required. Install pyyaml>=6.0.1.")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    rows = data.get("sources") or []
    sources: list[SourceConfig] = []
    for row in rows:
        sources.append(
            SourceConfig(
                name=str(row["name"]),
                index=str(row.get("index", row["name"])),
                source_type=str(row.get("type", "general")),
                clone_url=str(row["clone_url"]),
                local_path=Path(str(row["local_path"])).expanduser(),
                parse_mode=str(row.get("parse_mode", "markdown")),
                content_glob=str(row.get("content_glob", "**/*.md")),
            )
        )
    return sources


def clone_or_pull(source: SourceConfig) -> None:
    source.local_path.parent.mkdir(parents=True, exist_ok=True)
    if not source.local_path.exists():
        subprocess.run(
            ["git", "clone", "--depth=1", source.clone_url, str(source.local_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return
    subprocess.run(
        ["git", "-C", str(source.local_path), "pull", "--ff-only"],
        check=True,
        capture_output=True,
        text=True,
    )


def _match_files(root: Path, pattern: str) -> list[Path]:
    patterns = [pattern]
    brace_match = re.search(r"\{([^{}]+)\}", pattern)
    if brace_match:
        values = [item.strip() for item in brace_match.group(1).split(",") if item.strip()]
        patterns = [
            pattern[:brace_match.start()] + value + pattern[brace_match.end():]
            for value in values
        ]

    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(fnmatch.fnmatch(rel, p) for p in patterns):
            files.append(path)
    return sorted(files)


def _clean_markdown(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _markdown_title(path: Path, text: str) -> str:
    m = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    if m:
        return m.group(1).strip()
    return path.stem.replace("-", " ").replace("_", " ").title()


def _chunks_from_markdown(source: SourceConfig, path: Path, text: str) -> list[LibraryChunk]:
    cleaned = _clean_markdown(text)
    if not cleaned:
        return []
    title = _markdown_title(path, cleaned)
    url = ""
    if source.name == "hacktricks":
        rel = path.relative_to(source.local_path / "src").with_suffix("").as_posix()
        url = f"https://book.hacktricks.xyz/{rel.lower()}"

    sections = re.split(r"(?=^##\s+)|(?=^###\s+)", cleaned, flags=re.MULTILINE)
    chunks: list[LibraryChunk] = []
    for section in sections:
        body = section.strip()
        if len(body) < 40:
            continue
        chunk_title = title
        m = re.match(r"^#{2,3}\s+(.+)$", body)
        if m:
            chunk_title = f"{title} > {m.group(1).strip()}"
        tags = {"source": source.name}
        if source.name == "payloads":
            tags["source"] = "payloads"
        if source.name in {"gtfobins", "lolbas"}:
            tags["type"] = "privesc"
        chunks.append(
            LibraryChunk(
                index=source.index,
                source=source.name,
                source_type=source.source_type,
                title=chunk_title,
                url=url,
                content=body,
                machine="",
                tags=tags,
            )
        )
    return chunks


def _parse_gtfobins_yaml(source: SourceConfig, path: Path, text: str) -> list[LibraryChunk]:
    binary_name = path.stem
    front_matter = ""
    body = text
    if text.startswith("---\n"):
        split = text.split("\n---\n", 1)
        if len(split) == 2:
            front_matter, body = split
    title = binary_name.upper()
    url = f"https://gtfobins.github.io/gtfobins/{binary_name}/"

    if front_matter and yaml is not None:
        try:
            parsed = yaml.safe_load(front_matter.replace("---\n", "")) or {}
            if isinstance(parsed, dict) and parsed.get("title"):
                title = str(parsed.get("title"))
        except Exception:
            pass

    code_blocks = re.findall(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", body, flags=re.DOTALL)
    chunks: list[LibraryChunk] = []
    if not code_blocks:
        cleaned = _clean_markdown(body)
        if cleaned:
            chunks.append(
                LibraryChunk(
                    index=source.index,
                    source="gtfobins",
                    source_type="privesc",
                    title=f"{title} abuse",
                    url=url,
                    content=cleaned,
                    machine="",
                    tags={"source": "gtfobins", "type": "privesc"},
                )
            )
        return chunks

    for block in code_blocks:
        abuse_code = block.strip()
        if not abuse_code:
            continue
        content = (
            f"Binary Name: {title}\n"
            "Abuse Code:\n"
            f"```bash\n{abuse_code}\n```"
        )
        chunks.append(
            LibraryChunk(
                index=source.index,
                source="gtfobins",
                source_type="privesc",
                title=f"{title} abuse",
                url=url,
                content=content,
                machine="",
                tags={"source": "gtfobins", "type": "privesc"},
            )
        )
    return chunks


def _parse_gtfobins_yaml_file(source: SourceConfig, path: Path, text: str) -> list[LibraryChunk]:
    if yaml is None:
        return []

    try:
        payload = yaml.safe_load(text) or {}
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []

    binary_name = str(payload.get("name") or payload.get("binary") or path.stem).strip()
    title = binary_name.upper()
    url = f"https://gtfobins.github.io/gtfobins/{binary_name.lower()}/"

    abuse_candidates: list[str] = []
    for key in ("abuse", "code", "payload", "command", "commands"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            abuse_candidates.append(value.strip())
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    abuse_candidates.append(item.strip())

    if not abuse_candidates:
        # Last resort: flatten scalar string fields and use them as context.
        fallback = []
        for key, value in payload.items():
            if isinstance(value, str) and value.strip():
                fallback.append(f"{key}: {value.strip()}")
        if fallback:
            abuse_candidates.append("\n".join(fallback))

    chunks: list[LibraryChunk] = []
    for abuse_code in abuse_candidates:
        content = (
            f"Binary Name: {title}\n"
            "Abuse Code:\n"
            f"```bash\n{abuse_code}\n```"
        )
        chunks.append(
            LibraryChunk(
                index=source.index,
                source="gtfobins",
                source_type="privesc",
                title=f"{title} abuse",
                url=url,
                content=content,
                machine="",
                tags={"source": "gtfobins", "type": "privesc"},
            )
        )
    return chunks


def parse_source(source: SourceConfig) -> list[LibraryChunk]:
    files = _match_files(source.local_path, source.content_glob)
    chunks: list[LibraryChunk] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if source.parse_mode == "gtfobins":
            # GTFOBins stores entries as extensionless YAML files under _gtfobins/.
            # Try structured YAML parsing first, then fallback to markdown parsing.
            parsed = _parse_gtfobins_yaml_file(source, path, text)
            if parsed:
                chunks.extend(parsed)
            else:
                chunks.extend(_parse_gtfobins_yaml(source, path, text))
        else:
            chunks.extend(_chunks_from_markdown(source, path, text))
    return chunks


async def ingest_chunks_for_source(
    settings: Settings,
    source: SourceConfig,
    chunks: list[LibraryChunk],
    *,
    batch_size: int = 50,
) -> dict[str, int]:
    if Redis is None:
        raise RuntimeError("redis package is required for ingestion.")
    _ensure_index(settings, source.index)
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    now_iso = datetime.now(UTC).isoformat()

    # Rebuild this source from a clean slate so stale/invalid hashes from prior
    # schema bugs (e.g. wrong vector blob format) cannot poison indexing.
    existing_keys = list(client.scan_iter(match=f"{source.index}:*"))
    if existing_keys:
        # Delete in manageable chunks to avoid oversized command payloads.
        for offset in range(0, len(existing_keys), 500):
            client.delete(*existing_keys[offset : offset + 500])

    ingested = 0
    failed = 0
    for offset in range(0, len(chunks), batch_size):
        batch = chunks[offset : offset + batch_size]
        embeddings = await asyncio.gather(
            *[asyncio.to_thread(embed, item.content) for item in batch],
            return_exceptions=True,
        )
        for item, vector in zip(batch, embeddings):
            if isinstance(vector, Exception):
                failed += 1
                continue
            key_material = f"{item.source}|{item.title}|{item.url}|{item.content[:200]}"
            doc_id = hashlib.sha256(key_material.encode("utf-8", errors="ignore")).hexdigest()[:24]
            redis_key = f"{source.index}:{doc_id}"
            try:
                client.hset(redis_key, mapping=item.to_hash(vector, now_iso))
                ingested += 1
            except Exception:
                failed += 1

    sync_key = f"{SYNC_KEY_PREFIX}{source.name}"
    client.hset(
        sync_key,
        mapping={
            "last_sync": now_iso,
            "source": source.name,
            "index": source.index,
            "count": str(ingested),
            "failed": str(failed),
        },
    )
    return {"ingested": ingested, "failed": failed}


def fetch_sync_status(settings: Settings, source_names: list[str]) -> dict[str, str]:
    statuses = {name: "never" for name in source_names}
    if Redis is None:
        return statuses
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        return statuses

    for source in source_names:
        key = f"{SYNC_KEY_PREFIX}{source}"
        try:
            payload = client.hgetall(key)
        except Exception:
            continue
        last_sync = payload.get("last_sync", "").strip()
        statuses[source] = last_sync or "never"
    return statuses


async def crawl_and_ingest_url(
    settings: Settings,
    url: str,
    *,
    index_name: str = "webcrawl",
    ingested_via: str = "manual_crawl",
    chunk_size: int = 1000,
    chunk_overlap: int = 100,
) -> dict[str, int]:
    """Crawl *url* via Crawl4AI, chunk the rag_markdown, embed, and store in Redis.

    The ``ingested_via`` value is stored as lineage metadata on every chunk
    (e.g. 'manual_crawl', 'add-source', 'searxng').
    """
    from cereal_killer.kb.web_crawler import crawl_url

    page = await crawl_url(url, ingested_via=ingested_via)
    text = page.rag_markdown.strip()
    if not text:
        return {"ingested": 0, "failed": 0}

    # Split into overlapping chunks.
    chunks: list[LibraryChunk] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        body = text[start:end].strip()
        if len(body) > 40:
            chunks.append(
                LibraryChunk(
                    index=index_name,
                    source=index_name,
                    source_type="webcrawl",
                    title=page.title or url,
                    url=url,
                    content=body,
                    machine="",
                    tags={
                        "source": index_name,
                        "type": "webcrawl",
                        "ingested_via": ingested_via,
                        "rag_source": page.rag_source,
                    },
                )
            )
        start += chunk_size - chunk_overlap

    if not chunks:
        return {"ingested": 0, "failed": 0}

    fake_source = SourceConfig(
        name=index_name,
        index=index_name,
        source_type="webcrawl",
        clone_url="",
        local_path=Path("/tmp"),
        parse_mode="markdown",
        content_glob="**/*.md",
    )
    return await ingest_chunks_for_source(settings, fake_source, chunks)


def purge_source_by_url(
    settings: Settings,
    url_fragment: str,
    *,
    index_name: str = "webcrawl",
) -> int:
    """Delete all Redis hashes under *index_name* whose ``url`` contains *url_fragment*.

    Returns the number of keys deleted.
    """
    if Redis is None:
        return 0
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        return 0

    deleted = 0
    for key in client.scan_iter(match=f"{index_name}:*"):
        try:
            stored_url = client.hget(key, "url") or ""
            if url_fragment.lower() in stored_url.lower():
                client.delete(key)
                deleted += 1
        except Exception:
            continue
    return deleted


async def sync_all_sources(settings: Settings, config_path: Path) -> dict[str, dict[str, int]]:
    sources = load_sources_config(config_path)
    summary: dict[str, dict[str, int]] = {}
    for source in sources:
        clone_or_pull(source)
        chunks = parse_source(source)
        summary[source.name] = await ingest_chunks_for_source(settings, source, chunks)
    return summary

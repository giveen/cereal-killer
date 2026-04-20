"""HackTricks Deep Read Ingestion Engine.

This module handles:
1. Content extraction from HackTricks markdown files
2. Semantic section-based chunking with parent header context
3. Batch vectorization and Redis ingestion
4. Citation URL generation
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HackTricksDocument:
    """Metadata for a single HackTricks file."""

    title: str
    file_path: Path
    raw_content: str
    headers: list[str]
    url: str


@dataclass(slots=True)
class HackTricksChunk:
    """A semantically coherent chunk from HackTricks for vectorization."""

    breadcrumb: str  # e.g., "Generic Hacking > Reverse Shells"
    content_text: str  # The actual Markdown
    content_vector: list[float] = field(default_factory=list)  # Will be populated by embed()
    source_file: str = ""
    title: str = ""
    url: str = ""
    tags: dict[str, str] = field(default_factory=dict)  # e.g., {"category": "network-services"}

    def to_redis_hash(self, key_id: str) -> dict[str, Any]:
        """Convert chunk to Redis hash format for ingestion."""
        return {
            "breadcrumb": self.breadcrumb,
            "content_text": self.content_text,
            "content_vector": json.dumps(self.content_vector),
            "source_file": self.source_file,
            "title": self.title,
            "url": self.url,
            "tags": ",".join(f"{k}:{v}" for k, v in self.tags.items()),
        }


def _clean_markdown(content: str) -> str:
    """Remove HTML comments and extra whitespace from Markdown."""
    # Remove HTML comments
    content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    # Normalize whitespace: collapse multiple blank lines
    content = re.sub(r"\n\n+", "\n\n", content)
    return content.strip()


def _extract_title(file_path: Path, content: str) -> str:
    """Extract title from H1 or fallback to filename."""
    match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return file_path.stem.replace("-", " ").replace("_", " ").title()


def _extract_headers(content: str) -> list[str]:
    """Extract all H2 and H3 headers from Markdown."""
    headers: list[str] = []
    for match in re.finditer(r"^(#{2,3})\s+(.+)$", content, re.MULTILINE):
        level = len(match.group(1))
        text = match.group(2).strip()
        headers.append(text)
    return headers


def _generate_hacktricks_url(file_path: Path) -> str:
    """Generate book.hacktricks.xyz URL from file path.
    
    Example: src/network-services/smb.md -> book.hacktricks.xyz/network-services/smb
    """
    parts = file_path.parts
    if "src" in parts:
        src_index = parts.index("src")
        relative = Path(*parts[src_index + 1 :])
    else:
        relative = Path(file_path.name)
    # Convert to URL-safe path and remove .md extension
    url_path = str(relative.with_suffix("")).replace("\\", "/").lower()
    return f"https://book.hacktricks.xyz/{url_path}"


def _split_by_headers(content: str, title: str) -> list[tuple[str, str]]:
    """Split content into sections by H2/H3 headers.
    
    Returns list of (header_breadcrumb, section_content) tuples.
    """
    sections: list[tuple[str, str]] = []
    current_h2 = None
    current_h3 = None
    buffer: list[str] = []

    lines = content.split("\n")

    for line in lines:
        h2_match = re.match(r"^##\s+(.+)$", line)
        h3_match = re.match(r"^###\s+(.+)$", line)

        if h2_match:
            # Save previous section
            if buffer and current_h2:
                breadcrumb = f"{title} > {current_h2}"
                if current_h3:
                    breadcrumb += f" > {current_h3}"
                sections.append((breadcrumb, "\n".join(buffer)))
                buffer = []
            current_h2 = h2_match.group(1).strip()
            current_h3 = None
            buffer.append(line)
        elif h3_match:
            # Save previous section if it had content
            if buffer and current_h2 and current_h3:
                breadcrumb = f"{title} > {current_h2} > {current_h3}"
                sections.append((breadcrumb, "\n".join(buffer)))
                buffer = []
            current_h3 = h3_match.group(1).strip()
            buffer.append(line)
        else:
            buffer.append(line)

    # Save final section
    if buffer and current_h2:
        breadcrumb = f"{title} > {current_h2}"
        if current_h3:
            breadcrumb += f" > {current_h3}"
        sections.append((breadcrumb, "\n".join(buffer)))

    # If no headers found, return the entire content as one section
    if not sections and content.strip():
        sections.append((title, content))

    return sections


def _extract_tags_from_breadcrumb(breadcrumb: str) -> dict[str, str]:
    """Extract metadata tags from breadcrumb for filtering.
    
    This is a simple heuristic - in production, you'd want more sophisticated tagging.
    """
    breadcrumb_lower = breadcrumb.lower()
    tags: dict[str, str] = {}

    # Service detection
    services = {
        "ssh": "ssh", "ftp": "ftp", "http": "http", "https": "https",
        "smb": "smb", "nfs": "nfs", "dns": "dns", "ldap": "ldap",
        "snmp": "snmp", "mysql": "mysql", "postgres": "postgres",
    }
    for service, tag in services.items():
        if service in breadcrumb_lower:
            tags["service"] = tag
            break

    # Category detection
    if any(word in breadcrumb_lower for word in ["reverse", "shell", "payload", "exploit"]):
        tags["category"] = "exploitation"
    elif any(word in breadcrumb_lower for word in ["enum", "scan", "recon", "nmap", "gobuster"]):
        tags["category"] = "recon"
    elif any(word in breadcrumb_lower for word in ["privilege", "privesc", "sudo", "suid"]):
        tags["category"] = "privilege-escalation"
    elif any(word in breadcrumb_lower for word in ["reverse shell", "port forward", "tunnel", "ssh"]):
        tags["category"] = "networking"
    else:
        tags["category"] = "general"

    return tags


def extract_document(file_path: Path) -> HackTricksDocument:
    """Extract metadata and content from a single HackTricks Markdown file."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logger.warning(f"Failed to read {file_path}: {e}")
        return HackTricksDocument(
            title="[ERROR]",
            file_path=file_path,
            raw_content="",
            headers=[],
            url="",
        )

    content = _clean_markdown(content)
    title = _extract_title(file_path, content)
    headers = _extract_headers(content)
    url = _generate_hacktricks_url(file_path)

    return HackTricksDocument(
        title=title,
        file_path=file_path,
        raw_content=content,
        headers=headers,
        url=url,
    )


def chunk_document(doc: HackTricksDocument) -> list[HackTricksChunk]:
    """Split a document into semantic chunks by H2/H3 headers."""
    sections = _split_by_headers(doc.raw_content, doc.title)
    chunks: list[HackTricksChunk] = []

    for breadcrumb, section_text in sections:
        # Only create chunks for sections with meaningful content
        if len(section_text.strip()) < 50:
            continue

        chunk = HackTricksChunk(
            breadcrumb=breadcrumb,
            content_text=section_text,
            source_file=str(doc.file_path),
            title=doc.title,
            url=doc.url,
            tags=_extract_tags_from_breadcrumb(breadcrumb),
        )
        chunks.append(chunk)

    return chunks


def discover_markdown_files(hacktricks_dir: Path) -> list[Path]:
    """Recursively find all .md files in HackTricks src directory."""
    src_dir = hacktricks_dir / "src"
    if not src_dir.exists():
        logger.warning(f"HackTricks src directory not found: {src_dir}")
        return []

    return sorted(src_dir.rglob("*.md"))


async def ingest_hacktricks_batch(
    chunks: list[HackTricksChunk],
    embed_fn,
    redis_client,
    index_name: str = "hacktricks",
    batch_size: int = 50,
) -> dict[str, Any]:
    """Batch ingest chunks into Redis with vectorization.
    
    Args:
        chunks: List of HackTricksChunk objects to ingest
        embed_fn: Async function to embed text (returns list[float])
        redis_client: Redis client for storage
        index_name: Name of Redis index
        batch_size: Number of chunks to process per batch
    
    Returns:
        Stats dict with ingestion counts
    """
    stats = {
        "total_chunks": len(chunks),
        "ingested": 0,
        "failed": 0,
        "batches": 0,
    }

    for batch_idx in range(0, len(chunks), batch_size):
        batch = chunks[batch_idx : batch_idx + batch_size]
        stats["batches"] += 1

        # Embed batch in parallel
        try:
            embeddings = await asyncio.gather(
                *[embed_fn(chunk.content_text) for chunk in batch],
                return_exceptions=True,
            )
        except Exception as e:
            logger.error(f"Batch embedding failed: {e}")
            stats["failed"] += len(batch)
            continue

        # Store in Redis
        for chunk, embedding in zip(batch, embeddings):
            if isinstance(embedding, Exception):
                logger.error(f"Embedding failed for chunk {chunk.breadcrumb}: {embedding}")
                stats["failed"] += 1
                continue

            try:
                chunk.content_vector = embedding
                key_id = hashlib.sha256(
                    f"{chunk.breadcrumb}|{chunk.source_file}".encode()
                ).hexdigest()[:16]
                redis_key = f"{index_name}:{key_id}"

                redis_client.hset(redis_key, mapping=chunk.to_redis_hash(key_id))
                redis_client.expire(redis_key, 60 * 60 * 24 * 30)  # 30 day TTL
                stats["ingested"] += 1
            except Exception as e:
                logger.error(f"Redis storage failed for chunk {chunk.breadcrumb}: {e}")
                stats["failed"] += 1

        logger.info(f"Batch {stats['batches']}: {stats['ingested']} ingested, {stats['failed']} failed")

    return stats


def build_hacktricks_schema() -> dict[str, Any]:
    """Generate Redis VectorStore schema for HackTricks index."""
    return {
        "index": {
            "name": "hacktricks",
            "prefix": "hacktricks:",
            "storage_type": "hash",
        },
        "fields": [
            {"name": "breadcrumb", "type": "text"},
            {"name": "content_text", "type": "text"},
            {"name": "source_file", "type": "tag"},
            {"name": "title", "type": "text"},
            {"name": "url", "type": "text"},
            {"name": "tags", "type": "tag"},
            {
                "name": "content_vector",
                "type": "vector",
                "attrs": {
                    "algorithm": "flat",
                    "dims": 1536,  # OpenAI embedding dimension (or adjust for your model)
                    "distance_metric": "cosine",
                    "datatype": "float32",
                },
            },
        ],
    }

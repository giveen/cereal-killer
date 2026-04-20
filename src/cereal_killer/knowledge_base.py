from __future__ import annotations

import hashlib
import os
import re
from array import array
from dataclasses import dataclass
from typing import Any

import httpx
from redisvl.index import SearchIndex
from redisvl.query import VectorQuery
from redisvl.schema import IndexSchema

from cereal_killer.config import Settings, get_settings

IPPSEC_DATASET_URL = "https://raw.githubusercontent.com/IppSec/ippsec.github.io/master/dataset.json"
# Lightweight deterministic embedding size for hash-based fallback vectors.
# 64 dims keeps storage/query overhead low while still producing stable ordering.
EMBEDDING_DIMS = 64


def _vector_to_bytes(values: list[float]) -> bytes:
    # Redis hash vector fields expect a binary float buffer.
    return array("f", values).tobytes()


@dataclass(slots=True)
class KnowledgeBase:
    settings: Settings

    def _schema(self) -> IndexSchema:
        return IndexSchema.from_dict(
            {
                "index": {"name": self.settings.redis_index, "prefix": f"{self.settings.redis_index}:", "storage_type": "hash"},
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

    def _index(self) -> SearchIndex:
        return SearchIndex(schema=self._schema(), redis_url=self.settings.redis_url)

    def index(self) -> SearchIndex:
        return self._index()

    @staticmethod
    def embed(text: str, dims: int = EMBEDDING_DIMS) -> list[float]:
        # Deterministic hash embedding fallback for environments without embedding models.
        digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).digest()
        vals = [((digest[i % len(digest)] / 255.0) * 2) - 1 for i in range(dims)]
        return vals

    def lookup_walkthrough(self, query: str = "full machine walkthrough") -> str:
        try:
            idx = self.index()
            query_vec = self.embed(query)
            result = idx.query(
                VectorQuery(
                    vector=query_vec,
                    vector_field_name="embedding",
                    return_fields=["machine", "title", "url", "content"],
                    num_results=1,
                )
            )
        except Exception as exc:
            return f"Knowledge base unavailable: {exc}"

        docs = result if isinstance(result, list) else result.get("results", [])
        if not docs:
            return "No walkthrough found. Run sync-ippsec first."
        doc = docs[0]
        return f"{doc.get('machine', 'Unknown')} - {doc.get('title', 'Walkthrough')}\n{doc.get('url', '')}\n\n{doc.get('content', '')}"


def transform_dataset(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []

    def _normalize_machine(raw: str) -> str:
        name = (raw or "").strip()
        lowered = name.lower()
        if lowered.startswith("hackthebox - "):
            name = name[len("HackTheBox - "):]
        name = re.sub(r"\s+", " ", name).strip()
        return name

    def _timestamp_to_seconds(ts: Any) -> int:
        if isinstance(ts, dict):
            minutes = int(ts.get("minutes", 0) or 0)
            seconds = int(ts.get("seconds", 0) or 0)
            return max(0, minutes * 60 + seconds)
        return 0

    def _infer_phase(line_text: str) -> str:
        lowered = (line_text or "").lower()
        if any(token in lowered for token in ("linpeas", "setuid", "sudo", "root", "privesc")):
            return "root"
        if any(token in lowered for token in ("command injection", "reverse shell", "foothold", "ftp creds", "sshing", "exploit")):
            return "user"
        if any(token in lowered for token in ("nmap", "enum", "gobuster", "scan", "dirb", "ferox")):
            return "recon"
        return "recon"

    entries: list[dict[str, Any]] = []

    for idx, item in enumerate(data):
        machine_raw = str(item.get("machine", item.get("name", "unknown")))
        machine = _normalize_machine(machine_raw)
        video_id = str(item.get("videoId", "")).strip()
        timestamp_secs = _timestamp_to_seconds(item.get("timestamp"))
        line = str(item.get("line", "")).strip()
        tag = str(item.get("tag", "")).strip()
        academy = str(item.get("academy", "")).strip()
        title = str(item.get("title") or item.get("name") or "walkthrough")
        url = str(item.get("url") or item.get("link") or "")
        if not url and video_id:
            url = f"https://www.youtube.com/watch?v={video_id}&t={timestamp_secs}s"

        entries.append(
            {
                "idx": idx,
                "machine": machine,
                "machine_raw": machine_raw,
                "title": title,
                "url": url,
                "line": line,
                "tag": tag,
                "academy": academy,
                "video_id": video_id,
                "timestamp_seconds": timestamp_secs,
                "phase": _infer_phase(line),
            }
        )

    # Semantic sliding chunking by machine+phase to keep retrieval focused.
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in entries:
        grouped.setdefault((item["machine"], item["phase"]), []).append(item)

    doc_id = 0
    window_size = 4
    stride = 2
    for (machine, phase), group_items in grouped.items():
        group_items.sort(key=lambda x: (int(x.get("timestamp_seconds", 0)), int(x.get("idx", 0))))
        if not group_items:
            continue
        for start in range(0, len(group_items), stride):
            window = group_items[start:start + window_size]
            if not window:
                continue
            first = window[0]
            url = str(first.get("url", ""))
            title = str(first.get("title", "walkthrough"))
            lines_blob = "\n".join(f"- {w.get('line','').strip()}" for w in window if str(w.get("line", "")).strip())
            tags = ", ".join(sorted({str(w.get("tag", "")).strip() for w in window if str(w.get("tag", "")).strip()}))
            min_ts = min(int(w.get("timestamp_seconds", 0)) for w in window)
            max_ts = max(int(w.get("timestamp_seconds", 0)) for w in window)
            video_id = str(first.get("video_id", "")).strip()
            academy = str(first.get("academy", "")).strip()
            content_parts = [
                f"machine: {machine}",
                f"source_machine: {first.get('machine_raw', machine)}",
                f"phase: {phase}",
                f"title: {title}",
                f"tag: {tags}" if tags else "",
                f"academy: {academy}" if academy else "",
                f"video_id: {video_id}" if video_id else "",
                f"timestamp_seconds: {min_ts}",
                f"timestamp_end_seconds: {max_ts}",
                f"line: {lines_blob}" if lines_blob else "",
                url,
            ]
            content = "\n".join(part for part in content_parts if part).strip()
            docs.append(
                {
                    "id": f"ippsec-{doc_id}",
                    "machine": machine,
                    "title": title,
                    "url": url,
                    "content": content,
                    "embedding": _vector_to_bytes(KnowledgeBase.embed(content)),
                }
            )
            doc_id += 1

            if start + window_size >= len(group_items):
                break
    return docs


def sync_ippsec_dataset() -> None:
    settings = get_settings()
    kb = KnowledgeBase(settings)
    source = os.getenv("IPPSEC_DATASET_URL", IPPSEC_DATASET_URL)
    with httpx.Client(timeout=60.0) as client:
        response = client.get(source)
        response.raise_for_status()
        payload = response.json()

    if isinstance(payload, dict):
        data = payload.get("videos", [])
    else:
        data = payload

    docs = transform_dataset(data)
    index = kb.index()
    # Keep existing index configuration/data unless it does not exist yet.
    index.create(overwrite=False, drop=False)
    # Replace document set on each sync so schema/content upgrades take effect.
    try:
        # redisvl exposes a redis-py compatible client on .client
        stale_keys = list(index.client.scan_iter(match=f"{settings.redis_index}:*"))
        if stale_keys:
            index.client.delete(*stale_keys)
    except Exception:
        pass
    if docs:
        index.load(docs, id_field="id")
    print(f"Synced {len(docs)} entries into {settings.redis_index}")

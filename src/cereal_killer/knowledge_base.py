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

        content_parts = [
            f"machine: {machine}",
            f"source_machine: {machine_raw}",
            f"title: {title}",
            f"line: {line}" if line else "",
            f"tag: {tag}" if tag else "",
            f"academy: {academy}" if academy else "",
            f"video_id: {video_id}" if video_id else "",
            f"timestamp_seconds: {timestamp_secs}" if timestamp_secs else "",
            url,
        ]
        content = "\n".join(part for part in content_parts if part).strip()

        docs.append(
            {
                "id": f"ippsec-{idx}",
                "machine": machine,
                "title": title,
                "url": url,
                "content": content,
                "embedding": _vector_to_bytes(KnowledgeBase.embed(content)),
            }
        )
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
    if docs:
        index.load(docs, id_field="id")
    print(f"Synced {len(docs)} entries into {settings.redis_index}")

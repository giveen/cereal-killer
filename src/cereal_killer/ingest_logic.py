from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
DOCUMENT_SUFFIXES = {".log", ".txt", ".json"}
MAX_DOC_CHARS = 24000

JSON_SYSTEM_INSTRUCTION = (
    "The following is a structured log from the user's operation. "
    "Analyze for anomalies, specific error codes, or successful exploitation signatures. "
    "Prioritize JSON keys for context."
)


@dataclass(slots=True)
class DocumentIngestPayload:
    file_path: Path
    prompt: str
    is_json: bool


def is_image_path(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES


def is_document_path(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in DOCUMENT_SUFFIXES


def _truncate(text: str, *, max_chars: int = MAX_DOC_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[...truncated for context budget...]"


def build_document_prompt(file_path: Path) -> DocumentIngestPayload:
    suffix = file_path.suffix.lower()
    if suffix not in DOCUMENT_SUFFIXES:
        raise ValueError(f"Unsupported document type: {suffix}")

    raw_text = file_path.read_text(encoding="utf-8", errors="replace")

    if suffix == ".json":
        parsed = json.loads(raw_text)
        structured = json.dumps(parsed, indent=2, sort_keys=True)
        body = _truncate(structured)
        prompt = (
            f"{JSON_SYSTEM_INSTRUCTION}\n\n"
            "[User Document]\n"
            f"filename: {file_path.name}\n"
            "format: json\n"
            "content:\n"
            f"```json\n{body}\n```\n"
        )
        return DocumentIngestPayload(file_path=file_path, prompt=prompt, is_json=True)

    body = _truncate(raw_text)
    prompt = (
        "The following is a user document from the current operation. "
        "Analyze for anomalies, specific error codes, suspicious indicators, and successful exploitation signatures."
        "\n\n"
        "[User Document]\n"
        f"filename: {file_path.name}\n"
        f"format: {suffix.lstrip('.')}\n"
        "content:\n"
        f"```text\n{body}\n```\n"
    )
    return DocumentIngestPayload(file_path=file_path, prompt=prompt, is_json=False)

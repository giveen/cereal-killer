from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    """Best-effort .env loader for local runs that bypass Makefile exports."""
    candidates = [Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"]
    dotenv_path = next((path for path in candidates if path.exists()), None)
    if dotenv_path is None:
        return

    for raw in dotenv_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


@dataclass(slots=True)
class Settings:
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_index: str = os.getenv("REDIS_INDEX", "ippsec_idx")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://host.docker.internal:8000/v1")
    # Dedicated multimodal endpoint (llama-swap / OpenAI-compatible).
    llm_vision_base_url: str = os.getenv("LLM_VISION_BASE_URL", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", "not-needed")
    llm_model: str = os.getenv("LLM_MODEL", "qwen3.6")
    llm_vision_model: str = os.getenv("LLM_VISION_MODEL", "")
    github_token: str = os.getenv("GITHUB_TOKEN", "")
    reasoning_parser: str = os.getenv("REASONING_PARSER", "qwen3")
    max_model_len: int = int(os.getenv("MAX_MODEL_LEN", "262144"))
    # SearXNG last-resort web search.  Empty string disables the feature.
    searxng_base_url: str = os.getenv("SEARXNG_BASE_URL", "http://localhost:18080")
    # Minimum Redis vector similarity (0-1) before falling back to web search.
    searxng_vector_threshold: float = float(os.getenv("SEARXNG_VECTOR_THRESHOLD", "0.7"))
    # Snark calibration: 1 (polite) to 10 (abusive but brilliant). Default 8.
    snark_level: int = int(os.getenv("SNARK_LEVEL", "8"))
    # Loot report storage location.
    loot_report_dir: str = os.getenv("LOOT_REPORT_DIR", "data/loot_reports")
    # Backend LLM request/response trace logging for troubleshooting hangs.
    backend_trace_enabled: bool = os.getenv("BACKEND_TRACE_ENABLED", "1").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    backend_trace_path: str = os.getenv("BACKEND_TRACE_PATH", "logs/llm_backend.log")
    backend_trace_max_chars: int = int(os.getenv("BACKEND_TRACE_MAX_CHARS", "8000"))


HISTORY_CONTEXT_LIMIT = 50


def get_settings() -> Settings:
    return Settings()

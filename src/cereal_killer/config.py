from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class Settings:
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_index: str = os.getenv("REDIS_INDEX", "ippsec_idx")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://host.docker.internal:8000/v1")
    # Dedicated multimodal endpoint (llama-swap / OpenAI-compatible).
    llm_vision_base_url: str = os.getenv("LLM_VISION_BASE_URL", "http://localhost:8000/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "not-needed")
    llm_model: str = os.getenv("LLM_MODEL", "qwen3.6")
    llm_vision_model: str = os.getenv("LLM_VISION_MODEL", "")
    reasoning_parser: str = os.getenv("REASONING_PARSER", "qwen3")
    max_model_len: int = int(os.getenv("MAX_MODEL_LEN", "262144"))
    # SearXNG last-resort web search.  Empty string disables the feature.
    searxng_base_url: str = os.getenv("SEARXNG_BASE_URL", "http://localhost:18080")
    # Minimum Redis vector similarity (0-1) before falling back to web search.
    searxng_vector_threshold: float = float(os.getenv("SEARXNG_VECTOR_THRESHOLD", "0.7"))


HISTORY_CONTEXT_LIMIT = 50


def get_settings() -> Settings:
    return Settings()

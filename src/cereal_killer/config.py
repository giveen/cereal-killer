from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class Settings:
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379")
    redis_index: str = os.getenv("REDIS_INDEX", "ippsec_idx")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://host.docker.internal:8000/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "not-needed")
    llm_model: str = os.getenv("LLM_MODEL", "qwen3.6")


HISTORY_CONTEXT_LIMIT = 50


def get_settings() -> Settings:
    return Settings()

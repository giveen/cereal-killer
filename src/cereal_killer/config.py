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


def _normalise_reasoning_parser(raw_value: str) -> str:
    """Keep reasoning parser values backend-compatible.

    Users sometimes paste model names into REASONING_PARSER. Fall back to qwen3
    when the value clearly looks like a model identifier instead of a parser key.
    """
    value = (raw_value or "").strip().strip('"').strip("'")
    if not value:
        return "qwen3"

    lowered = value.lower()
    if lowered in {"qwen3", "qwen", "qwen-3"}:
        return "qwen3"

    looks_like_model_name = (
        any(token in lowered for token in {"uncensored", "instruct", "gguf", "a3b", "b-instruct"})
        or ("qwen" in lowered and any(ch.isdigit() for ch in lowered))
        or any(ch in value for ch in {"/", " ", ":"})
    )
    if looks_like_model_name:
        return "qwen3"

    return value


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
    reasoning_parser: str = _normalise_reasoning_parser(os.getenv("REASONING_PARSER", "qwen3"))
    # Disable backend thought preservation by default to avoid leaking internal context.
    preserve_thinking: bool = os.getenv("PRESERVE_THINKING", "0").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
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
    # RAG search timeout in seconds. If the tiered search takes longer than this,
    # partial results are returned to avoid blocking the UI.
    rag_timeout: float = float(os.getenv("RAG_TIMEOUT", "10"))
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    session_cache_ttl: int = int(os.getenv("SESSION_CACHE_TTL", "300"))
    # LLM response cache configuration
    enable_llm_cache: bool = os.getenv("LLM_CACHE_ENABLED", "1").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    llm_cache_maxsize: int = int(os.getenv("LLM_CACHE_MAXSIZE", "100"))
    llm_cache_ttl: int = int(os.getenv("LLM_CACHE_TTL", "300"))
    enable_streaming: bool = os.getenv("STREAMING_ENABLED", "0").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    # Whether to use LiteLLM as the LLM provider instead of the OpenAI client.
    use_litellm: bool = os.getenv("USE_LITELLM", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    # Redis connection pool configuration
    redis_pool_max_connections: int = int(os.getenv("REDIS_POOL_MAX_CONNECTIONS", "10"))
    redis_pool_socket_timeout: float = float(os.getenv("REDIS_POOL_SOCKET_TIMEOUT", "5.0"))
    # RAG batch embedding: number of queries to batch together before encoding.
    # Set to 1 to disable batching (use individual embed() calls).
    rag_batch_size: int = int(os.getenv("RAG_BATCH_SIZE", "4"))

    # Tuple of security tool names available in the environment.
    tech_tools: tuple[str, ...] = (
        "nmap",
        "gobuster",
        "feroxbuster",
        "ffuf",
        "nikto",
        "sqlmap",
        "dirsearch",
        "wfuzz",
        "smbclient",
        "smbmap",
        "enum4linux",
        "msfconsole",
        "netexec",
        "crackmapexec",
        "hydra",
        "john",
        "hashcat",
        "tcpdump",
        "wireshark",
        "nuclei",
    )

    # Subset of tech_tools that use a prefix-based naming convention
    # (e.g. tool-name-something) rather than a full name match.
    tech_tool_prefixes: tuple[str, ...] = ("nmap", "gobuster", "smb", "enum4linux")

    # Number of seconds to wait before re-issuing feedback for the same issue.
    feedback_cooldown_seconds: int = 30

    # Maximum number of tool commands to retain in context for the agent.
    command_context_limit: int = 20

    # Number of consecutive turns without progress before the agent is considered stuck.
    stuck_turn_limit: int = 5

    # Maximum number of prompts that can be pinned simultaneously.
    max_pinned_prompts: int = 50

    # Internal application name aliases used for identification.
    app_internal_names: tuple[str, ...] = ("cereal-killer", "cereal_killer", "gibson")

    # Maximum number of embeddings to cache.
    embed_cache_size: int = 1000

    # TTL in seconds for entries in the embedding cache.
    embed_cache_ttl_seconds: int = 3600


HISTORY_CONTEXT_LIMIT = 50


def get_settings() -> Settings:
    return Settings()

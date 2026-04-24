"""Centralized logging configuration for cereal-killer.

Configures structured logging with:
- File handler writing to /app/logs/cereal-killer.log
- Console (stream) handler for real-time output
- DEBUG-level filtering based on the DEBUG environment variable
- Consistent timestamped formatting across all modules
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

LOG_DIR = Path("/app/logs")
LOG_FILE = LOG_DIR / "cereal-killer.log"
FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Sentinel logger name so callers don't need to worry about namespacing.
_DEFAULT_LOGGER_NAME = "cereal-killer"

_logger: logging.Logger | None = None


def _is_debug_mode() -> bool:
    """Return True when the DEBUG environment variable is set."""
    return os.getenv("DEBUG", "0").lower() in {"1", "true", "yes", "on"}


def setup_logging() -> logging.Logger:
    """Configure and return the application root logger.

    This function is safe to call multiple times; it is idempotent.
    Subsequent calls return the already-configured logger without
    duplicating handlers.

    Returns:
        The configured root ``logging.Logger`` instance (named
        ``"cereal-killer"`` by default).
    """
    global _logger

    if _logger is not None:
        return _logger

    logger = logging.getLogger(_DEFAULT_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)  # let handlers filter via their levels.

    debug = _is_debug_mode()

    formatter = logging.Formatter(fmt=FORMAT, datefmt=DATE_FORMAT)

    # ── File handler (always present) ────────────────────────────────
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(LOG_FILE),
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # ── Console handler ──────────────────────────────────────────────
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # Only set the logger itself to DEBUG when we want verbose output.
    logger.setLevel(logging.DEBUG)

    _logger = logger
    return logger

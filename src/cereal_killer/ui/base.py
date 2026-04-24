"""Shared UI utilities and base resolution helpers."""
from __future__ import annotations

from typing import Any

__all__ = ["resolve_dashboard", "require_dashboard", "update_llm_cache_metrics"]


def resolve_dashboard(app: Any) -> Any:
    """Return MainDashboard from active screen or stack, or None if not found.

    Centralized replacement for the duplicated _dashboard() pattern
    found across worker/manager classes.

    Args:
        app: The CerealKillerApp instance (or any object with .screen and .screen_stack).

    Returns:
        The MainDashboard instance if found, or None.
    """
    from .screens import MainDashboard

    active = app.screen
    if isinstance(active, MainDashboard):
        return active
    for screen in reversed(getattr(app, "screen_stack", [])):
        if isinstance(screen, MainDashboard):
            return screen
    return None


def require_dashboard(app: Any) -> Any:
    """Return MainDashboard from active screen or stack, raising if not found.

    Variant of resolve_dashboard() that raises RuntimeError instead of returning None.
    Use when the dashboard MUST be available (e.g., boot sequence).

    Args:
        app: The CerealKillerApp instance.

    Returns:
        The MainDashboard instance.

    Raises:
        RuntimeError: If MainDashboard is not active or in the screen stack.
    """
    dashboard = resolve_dashboard(app)
    if dashboard is None:
        raise RuntimeError("MainDashboard is not active")
    return dashboard


def update_llm_cache_metrics(app: Any, backend_meta: dict[str, object] | None) -> None:
    """Update the dashboard LLM cache metrics display.

    Centralised replacement for the copy-pasted _update_llm_cache_metrics
    found in ChatWorkerManager, IngestWorkerManager, and MiscWorkerManager.
    """
    if not backend_meta:
        return
    dashboard = resolve_dashboard(app)
    if dashboard is None:
        return
    latency_obj = backend_meta.get("latency_ms")
    cached_obj = backend_meta.get("tokens_cached")
    from_cache_obj = backend_meta.get("from_cache", False)
    latency_ms = latency_obj if isinstance(latency_obj, int) else None
    tokens_cached = cached_obj if isinstance(cached_obj, int) else None
    from_cache = bool(from_cache_obj) if isinstance(from_cache_obj, (bool, int, str)) else False
    dashboard.set_llm_cache_metrics(latency_ms, tokens_cached, from_cache=from_cache)

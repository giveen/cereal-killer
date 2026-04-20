from __future__ import annotations

import asyncio
import json
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from cereal_killer.config import Settings


_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.IGNORECASE)
_GITHUB_API_VERSION = "2026-03-10"
_GITHUB_CVE_URL = "https://api.github.com/advisories/{cve_id}"
_MAX_RETRIES = 3

# Global request gate for GitHub JIT requests.
_SEMAPHORE = asyncio.Semaphore(5)
_COOLDOWN_UNTIL: float = 0.0
_COOLDOWN_LOCK = asyncio.Lock()


@dataclass(slots=True)
class GitHubRateSnapshot:
    remaining: int
    limit: int
    reset_epoch: int


_RATE_SNAPSHOT: GitHubRateSnapshot | None = None


def _cache_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "cves"


def _cache_path(cve_id: str) -> Path:
    return _cache_dir() / f"{cve_id.upper()}.json"


def extract_cve_ids(text: str) -> list[str]:
    return sorted({match.group(0).upper() for match in _CVE_RE.finditer(text or "")})


def get_rate_snapshot() -> GitHubRateSnapshot | None:
    return _RATE_SNAPSHOT


def _headers(settings: Settings) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        "User-Agent": "cereal-killer-cve-jit",
    }
    token = (settings.github_token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _parse_rate_headers(headers: httpx.Headers) -> None:
    global _RATE_SNAPSHOT
    remaining = headers.get("X-RateLimit-Remaining")
    limit = headers.get("X-RateLimit-Limit")
    reset = headers.get("X-RateLimit-Reset")
    if not (remaining and limit and reset):
        return
    try:
        _RATE_SNAPSHOT = GitHubRateSnapshot(
            remaining=int(remaining),
            limit=int(limit),
            reset_epoch=int(reset),
        )
    except ValueError:
        return


async def _wait_for_cooldown() -> None:
    while True:
        wait_for = _COOLDOWN_UNTIL - time.time()
        if wait_for <= 0:
            return
        await asyncio.sleep(min(wait_for, 5.0))


async def _arm_cooldown(warn: Callable[[str], None] | None) -> None:
    global _COOLDOWN_UNTIL
    cooldown_seconds = random.randint(60, 120)
    until = time.time() + cooldown_seconds
    async with _COOLDOWN_LOCK:
        if until > _COOLDOWN_UNTIL:
            _COOLDOWN_UNTIL = until
    if warn is not None:
        warn(f"Trace Detected: GitHub throttle tripped. Cooling down for {cooldown_seconds}s.")


def _read_cache(cve_id: str) -> dict | None:
    path = _cache_path(cve_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(cve_id: str, payload: dict) -> None:
    path = _cache_path(cve_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


async def fetch_cve(settings: Settings, cve_id: str, warn: Callable[[str], None] | None = None) -> dict | None:
    normalized = (cve_id or "").strip().upper()
    if not normalized:
        return None

    # Shield: local cache first, before any limiter/cooldown checks.
    cached = _read_cache(normalized)
    if cached is not None:
        return cached

    await _wait_for_cooldown()

    async with _SEMAPHORE:
        for attempt in range(_MAX_RETRIES):
            await _wait_for_cooldown()
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.get(
                        _GITHUB_CVE_URL.format(cve_id=normalized),
                        headers=_headers(settings),
                    )
            except httpx.HTTPError:
                if attempt >= _MAX_RETRIES - 1:
                    return None
                await asyncio.sleep(2 ** (attempt + 1))
                continue

            status = response.status_code

            if status == 404:
                return None

            if status in {403, 429}:
                await _arm_cooldown(warn)
                if attempt >= _MAX_RETRIES - 1:
                    return None
                await asyncio.sleep(2 ** (attempt + 1))
                continue

            if 500 <= status <= 599:
                if attempt >= _MAX_RETRIES - 1:
                    return None
                await asyncio.sleep(2 ** (attempt + 1))
                continue

            if status >= 400:
                return None

            try:
                payload = response.json()
            except Exception:
                return None

            _parse_rate_headers(response.headers)
            if isinstance(payload, dict):
                _write_cache(normalized, payload)
            return payload if isinstance(payload, dict) else None

    return None

"""Gibson Boot Sequence — dependency health checks with hacker-aesthetic output.

Usage (from an async context):
    async for result in run_boot_sequence(settings):
        richlog.write(result.message)
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import NamedTuple

from cereal_killer.config import Settings


class CheckResult(NamedTuple):
    label: str
    ok: bool
    message: str


async def run_boot_sequence(settings: Settings) -> AsyncIterator[CheckResult]:
    """Async generator that performs all dependency checks in sequence."""
    yield CheckResult(
        label="BOOT",
        ok=True,
        message="[bold cyan]--- GIBSON SYSTEM CHECK ---[/bold cyan]",
    )
    await asyncio.sleep(0.1)

    yield await _check_redis(settings)
    await asyncio.sleep(0.12)

    yield await _check_llm_endpoint(settings)
    await asyncio.sleep(0.12)

    yield await _check_ippsec_dataset(settings)
    await asyncio.sleep(0.12)

    yield CheckResult(
        label="BOOT",
        ok=True,
        message="[bold cyan]--- BOOT SEQUENCE COMPLETE ---[/bold cyan]",
    )


async def _check_redis(settings: Settings) -> CheckResult:
    try:
        from redis.asyncio import Redis  # type: ignore[import-untyped]
    except ImportError:
        return CheckResult(
            label="Redis",
            ok=False,
            message=(
                "[red][FAIL][/red] [b]Redis[/b] — package not installed. "
                "Install redis[asyncio] or I literally cannot remember anything."
            ),
        )
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        await asyncio.wait_for(client.ping(), timeout=3.0)
        await client.aclose()
        return CheckResult(
            label="Redis",
            ok=True,
            message="[green][ OK ][/green] [b]Redis[/b] — memory banks online.",
        )
    except Exception as exc:
        return CheckResult(
            label="Redis",
            ok=False,
            message=(
                f"[red][FAIL][/red] [b]Redis[/b] — I can't think if I can't remember. "
                f"Fix the docker-compose. ({exc})"
            ),
        )


async def _check_llm_endpoint(settings: Settings) -> CheckResult:
    try:
        import httpx  # type: ignore[import-untyped]
    except ImportError:
        return CheckResult(
            label="LLM",
            ok=False,
            message=(
                "[yellow][WARN][/yellow] [b]LLM endpoint[/b] — httpx not installed, "
                "skipping pre-flight check. Hope you know what you're doing."
            ),
        )
    url = settings.llm_base_url.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(
                url, headers={"Authorization": f"Bearer {settings.llm_api_key}"}
            )
        if resp.status_code < 400:
            return CheckResult(
                label="LLM",
                ok=True,
                message="[green][ OK ][/green] [b]llama.cpp[/b] — the brain has a brain.",
            )
        return CheckResult(
            label="LLM",
            ok=False,
            message=(
                f"[red][FAIL][/red] [b]llama.cpp[/b] — HTTP {resp.status_code}. "
                "Is the server running with --jinja --reasoning-parser qwen3?"
            ),
        )
    except Exception as exc:
        return CheckResult(
            label="LLM",
            ok=False,
            message=(
                f"[red][FAIL][/red] [b]llama.cpp[/b] — can't reach {settings.llm_base_url}. "
                f"No LLM, no coaching, just vibes. ({exc})"
            ),
        )


async def _check_ippsec_dataset(settings: Settings) -> CheckResult:
    try:
        from redis.asyncio import Redis  # type: ignore[import-untyped]

        client = Redis.from_url(settings.redis_url, decode_responses=True)
        info = await asyncio.wait_for(
            client.execute_command("FT.INFO", settings.redis_index),
            timeout=3.0,
        )
        await client.aclose()

        # FT.INFO returns alternating key/value items.
        count = 0
        if isinstance(info, list):
            for i in range(0, len(info) - 1, 2):
                if str(info[i]) == "num_docs":
                    count = int(float(info[i + 1]))
                    break

        if count:
            return CheckResult(
                label="IppSec",
                ok=True,
                message=(
                    f"[green][ OK ][/green] [b]IppSec dataset[/b] — "
                    f"{count} knowledge chunks indexed. We know things."
                ),
            )
        return CheckResult(
            label="IppSec",
            ok=False,
            message=(
                "[yellow][WARN][/yellow] [b]IppSec dataset[/b] — empty. "
                "Run make sync-ippsec (or scripts/sync_ippsec.py) and enjoy being less blind."
            ),
        )
    except Exception as exc:
        if "Unknown index name" in str(exc):
            return CheckResult(
                label="IppSec",
                ok=False,
                message=(
                    "[yellow][WARN][/yellow] [b]IppSec dataset[/b] — index not found yet. "
                    "Run make sync-ippsec to initialize and persist it."
                ),
            )
        return CheckResult(
            label="IppSec",
            ok=False,
            message=(
                f"[yellow][WARN][/yellow] [b]IppSec dataset[/b] — check failed. ({exc})"
            ),
        )

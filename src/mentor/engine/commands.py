"""Slash-command router for the Cereal-Killer TUI.

Usage
-----
    result = await dispatch(raw_input, engine, settings)
    if result is not None:
        # The input was a slash command; result carries the reply.
        chat_log.write(result.message)

Adding a new command
--------------------
1.  Write an async handler `async def _cmd_foo(args, engine, settings) -> CommandResult`.
2.  Register it in `_REGISTRY` at the bottom of this module.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Awaitable, Callable

from cereal_killer.config import Settings
from mentor.engine.session import ThinkingSessionStore
from mentor.kb.query import retrieve_solution_for_machine


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CommandResult:
    """Returned by every command handler."""
    # Human-readable Rich-markup reply to show in the chat log.
    message: str
    # When set, the app should update its target machine context.
    new_target: str | None = None
    # When True the /box system-prompt block has been applied.
    context_loaded: bool = False
    # When True the /new-box exploration mode has been activated.
    exploration_mode: bool = False
    # Optional dynamic Redis index prefix created for the session.
    session_prefix: str | None = None
    # Optional updated system-prompt addendum the Brain should adopt.
    system_prompt_addendum: str | None = None
    # Phase should reset on the UI side.
    reset_phase: bool = False
    # Optional image path used by /upload flow.
    upload_image_path: str | None = None
    # Optional raw search query used by /search flow.
    search_query: str | None = None


# ---------------------------------------------------------------------------
# Handler signature type alias
# ---------------------------------------------------------------------------

_Handler = Callable[
    [list[str], "LLMEngineProtocol", Settings],
    Awaitable[CommandResult],
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_slash_command(text: str) -> bool:
    """Return True iff *text* starts with '/' and has at least one word."""
    stripped = text.strip()
    return stripped.startswith("/") and len(stripped) > 1


def parse_slash_command(text: str) -> tuple[str, list[str]]:
    """Split ``/box lame`` into ``('box', ['lame'])``.  Always lower-cases the verb."""
    parts = text.strip().lstrip("/").split()
    verb = parts[0].lower() if parts else ""
    args = parts[1:] if len(parts) > 1 else []
    return verb, args


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def _cmd_box(args: list[str], engine: object, settings: Settings) -> CommandResult:
    """Load known-box context: vector-search the IppSec index, inject system prompt block."""
    if not args:
        return CommandResult(
            message="[yellow]Usage:[/yellow] /box <machine-name>  (e.g. /box lame)"
        )

    machine = args[0].strip().lower()
    # Retrieve walkthrough material — best-effort; if Redis is down we still proceed.
    try:
        solution_md = await retrieve_solution_for_machine(settings, machine)
        has_material = "No Redis walkthrough" not in solution_md
    except Exception as exc:
        solution_md = f"[context unavailable: {exc}]"
        has_material = False

    # Build the system-prompt addendum that Brain will use for this session.
    addendum = (
        f"CURRENT TARGET: {machine.upper()}. "
        "You have access to the known IppSec solution for this machine. "
        "Use it to guide the user without spoiling, unless the Easy Button is pressed. "
        "Prioritise this target context over generic advice."
    )
    if has_material:
        # Embed a compact excerpt (first 600 chars) so Brain has grounded context.
        excerpt = solution_md[:600].replace("\n", " ")
        addendum += f"\n\nKNOWN MATERIAL EXCERPT:\n{excerpt}"

    context_loaded = has_material
    if has_material:
        reply = (
            f"[green]Context loaded for [b]{machine.upper()}[/b].[/green] "
            "I have IppSec notes on this one. Try not to make me look bad."
        )
    else:
        reply = (
            f"[yellow]No IppSec material found for [b]{machine.upper()}[/b].[/yellow] "
            "Flying blind. Run your recon and I'll improvise."
        )

    return CommandResult(
        message=reply,
        new_target=machine,
        context_loaded=context_loaded,
        system_prompt_addendum=addendum,
        reset_phase=True,
    )


async def _cmd_new_box(args: list[str], engine: object, settings: Settings) -> CommandResult:
    """Exploration mode: clear known-box context, set inquisitive persona, new Redis prefix."""
    if not args:
        return CommandResult(
            message="[yellow]Usage:[/yellow] /new-box <machine-name>  (e.g. /new-box knife)"
        )

    machine = args[0].strip().lower()
    session_prefix = f"session:{machine}"

    # Provision the Redis dynamic index prefix (best-effort).
    prefix_ready = False
    try:
        client_obj = ThinkingSessionStore(settings)
        redis_client = await client_obj._client()
        if redis_client is not None:
            # Write a metadata marker so the prefix exists in Redis.
            await redis_client.set(
                f"{session_prefix}:meta",
                f'{{"machine": "{machine}", "mode": "exploration"}}',
                ex=60 * 60 * 24 * 14,  # 14-day TTL
            )
            prefix_ready = True
    except Exception:
        pass

    addendum = (
        f"CURRENT TARGET: {machine.upper()} — EXPLORATION MODE. "
        "You do NOT have a pre-loaded walkthrough for this machine. "
        "Be inquisitive: ask the user for nmap output, feroxbuster results, and service banners. "
        "Build your mental model from scratch. "
        "Do NOT pretend to know the solution. "
        "Treat every new finding as a clue and guide with Socratic questions."
    )

    prefix_note = (
        f"  Dynamic session prefix [cyan]{session_prefix}[/cyan] ready in Redis."
        if prefix_ready
        else "  (Redis unavailable — prefix not written, but exploration mode is active.)"
    )

    return CommandResult(
        message=(
            f"[cyan]Exploration Mode activated for [b]{machine.upper()}[/b].[/cyan] "
            "No script to follow here. I'll need your recon output to work with.\n"
            + prefix_note
        ),
        new_target=machine,
        exploration_mode=True,
        session_prefix=session_prefix if prefix_ready else None,
        system_prompt_addendum=addendum,
        reset_phase=True,
    )


async def _cmd_help(args: list[str], engine: object, settings: Settings) -> CommandResult:
    lines = [
        "[b]Available commands:[/b]",
        "  [cyan]/box <name>[/cyan]              — Load IppSec context for a known box",
        "  [cyan]/new-box <name>[/cyan]          — Start exploration mode for an unknown box",
        "  [cyan]/vision[/cyan]                  — Analyze latest clipboard screenshot",
        "  [cyan]/upload <path>[/cyan]           — Analyze a specific image file",
        "  [cyan]/loot[/cyan]                    — Generate a loot report for the current box",
        "  [cyan]/victory <text>[/cyan]          — Record post-pwn explanation in learnings vault",
        "  [cyan]/search <query>[/cyan]          — Search local IppSec + HackTricks memory and synthesize results",
        "  [cyan]/add-source <url>[/cyan]        — Crawl and ingest a URL into the knowledge base",
        "  [cyan]/purge-source <fragment>[/cyan] — Delete all indexed chunks matching a URL fragment",
        "  [cyan]/sync-hacktricks[/cyan]         — Ingest HackTricks library into Redis (one-time setup)",
        "  [cyan]/sync-all[/cyan]                — Refresh all local knowledge sources from sources.yaml",
        "  [cyan]/clear[/cyan]                   — Clear the current box session from Redis",
        "  [cyan]/exit[/cyan]                    — Exit the TUI",
        "  [cyan]/help[/cyan]                    — Show this message",
    ]
    return CommandResult(message="\n".join(lines))


async def _cmd_loot(args: list[str], engine: object, settings: Settings) -> CommandResult:
    """Convenience alias — the UI's handle_pwned wires the actual call."""
    return CommandResult(
        message="[dim]Routing to loot report generator...[/dim]",
        new_target=None,
        # Signal the UI to trigger handle_pwned logic.
        context_loaded=False,
        exploration_mode=False,
        session_prefix="__loot__",
    )


async def _cmd_vision(args: list[str], engine: object, settings: Settings) -> CommandResult:
    """Analyze the latest clipboard screenshot via the multimodal model path."""
    return CommandResult(
        message="[dim]Routing clipboard screenshot to vision analysis...[/dim]",
        new_target=None,
        context_loaded=False,
        exploration_mode=False,
        session_prefix="__vision__",
    )


async def _cmd_upload(args: list[str], engine: object, settings: Settings) -> CommandResult:
    """Analyze an explicit image file path via the multimodal model path."""
    if not args:
        return CommandResult(
            message="[yellow]Usage:[/yellow] /upload <path-to-image>",
        )

    raw_path = " ".join(args).strip()
    expanded = os.path.expanduser(raw_path)
    absolute = os.path.abspath(expanded)
    if not os.path.exists(absolute):
        return CommandResult(message=f"[red]Upload failed:[/red] file not found: {absolute}")
    if not os.path.isfile(absolute):
        return CommandResult(message=f"[red]Upload failed:[/red] not a file: {absolute}")

    lowered = absolute.lower()
    if not lowered.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")):
        return CommandResult(
            message="[red]Upload failed:[/red] only image files are supported (.png/.jpg/.jpeg/.webp/.bmp/.gif)"
        )

    return CommandResult(
        message=f"[green]Image uploaded:[/green] {absolute}",
        session_prefix="__upload__",
        upload_image_path=absolute,
    )


async def _cmd_clear(args: list[str], engine: object, settings: Settings) -> CommandResult:
    """Clear the current machine's Redis session."""
    from pathlib import Path as _Path

    machine = args[0].strip().lower() if args else _Path.cwd().name.lower()
    try:
        store = ThinkingSessionStore(settings)
        await store.clear_session(machine)
        return CommandResult(
            message=(
                f"[green]Redis session cleared for [b]{machine}[/b].[/green] "
                "Fresh slate. Please try not to repeat all the same mistakes."
            )
        )
    except Exception as exc:
        return CommandResult(message=f"[red]Clear failed:[/red] {exc}")


async def _cmd_victory(args: list[str], engine: object, settings: Settings) -> CommandResult:
    """Record the user's post-pwn vulnerability explanation in the learnings vault."""
    explanation = " ".join(args).strip()

    if not explanation:
        return CommandResult(
            message=(
                "[bold green]Pwned![/bold green] "
                "Before we move on, Zero Cool needs you to prove you actually understand "
                "what just happened.\n"
                "[cyan]Usage:[/cyan] /victory <explain the vuln and how you exploited it>\n"
                "This gets stored in your personal learnings vault and will remind you "
                "of similar patterns on future boxes."
            )
        )

    if len(explanation) < 20:
        return CommandResult(
            message=(
                "[yellow]Put some effort in.[/yellow] "
                "Write at least a sentence describing the vulnerability and how you got in."
            )
        )

    from pathlib import Path as _Path
    machine = _Path.cwd().name.lower()

    # Store the learning via the engine (graceful if engine is None in tests).
    recall: list[str] = []
    try:
        if engine is not None and hasattr(engine, "store_learning"):
            await engine.store_learning(machine, explanation)
        if engine is not None and hasattr(engine, "recall_learnings"):
            recall = await engine.recall_learnings(explanation, exclude_machine=machine)
    except Exception:
        pass

    excerpt = explanation[:200] + ("…" if len(explanation) > 200 else "")
    msg = (
        f"[green]✓ Victory learning stored for [b]{machine.upper()}[/b].[/green]\n"
        f"You wrote: [italic]{excerpt}[/italic]\n"
        "That's going in the vault. You might actually be learning something."
    )
    if recall:
        msg += (
            "\n\n[cyan]Zero Cool sees a pattern:[/cyan] "
            "You've encountered something similar before:\n"
            + "\n".join(f"  • {r[:160]}" for r in recall[:3])
        )

    return CommandResult(message=msg)


async def _cmd_exit(args: list[str], engine: object, settings: Settings) -> CommandResult:
    """Exit the TUI."""
    return CommandResult(
        message="[yellow]Exiting Cereal Killer...[/yellow]",
        session_prefix="__exit__",
    )


async def _cmd_add_source(args: list[str], engine: object, settings: Settings) -> CommandResult:
    """Crawl and ingest a URL into the 'webcrawl' Redis index.

    Usage: /add-source <url>  [index-name]
    The ingested content is tagged with ingested_via='add-source' for lineage tracking.
    """
    import asyncio

    if not args:
        return CommandResult(
            message="[yellow]Usage:[/yellow] /add-source <url>  (e.g. /add-source https://book.hacktricks.xyz/...)"
        )

    raw_url = args[0].strip()
    if not raw_url.startswith(("http://", "https://")):
        return CommandResult(message="[red]Invalid URL.[/red] Must start with http:// or https://")

    index_name = args[1].strip() if len(args) > 1 else "webcrawl"

    asyncio.create_task(
        _do_crawl_and_ingest(raw_url, index_name, settings)
    )
    return CommandResult(
        message=(
            f"[cyan]Crawl started for:[/cyan] {raw_url}\n"
            f"[dim]Index: {index_name} | Tagged: ingested_via=add-source[/dim]\n"
            "[yellow]This runs in the background — check back in 15-30 seconds.[/yellow]"
        ),
        session_prefix="__add_source__",
        search_query=raw_url,
    )


async def _do_crawl_and_ingest(url: str, index_name: str, settings: Settings) -> None:
    """Background task: crawl URL and store in Redis; logs but does not re-raise."""
    import logging
    from mentor.kb.library_ingest import crawl_and_ingest_url

    log = logging.getLogger(__name__)
    try:
        stats = await crawl_and_ingest_url(
            settings,
            url,
            index_name=index_name,
            ingested_via="add-source",
        )
        log.info("add-source %s: ingested=%d failed=%d", url, stats["ingested"], stats["failed"])
    except Exception as exc:
        log.error("add-source crawl failed for %s: %s", url, exc)


async def _cmd_purge_source(args: list[str], engine: object, settings: Settings) -> CommandResult:
    """Delete all 'webcrawl' index entries whose URL contains the given fragment.

    Usage: /purge-source <url-fragment>  [index-name]
    """
    if not args:
        return CommandResult(
            message="[yellow]Usage:[/yellow] /purge-source <url-or-domain>  (e.g. /purge-source hacktricks.xyz)"
        )

    fragment = args[0].strip()
    index_name = args[1].strip() if len(args) > 1 else "webcrawl"

    try:
        from mentor.kb.library_ingest import purge_source_by_url
        deleted = purge_source_by_url(settings, fragment, index_name=index_name)
        if deleted:
            return CommandResult(
                message=f"[green]Purged {deleted} chunk(s)[/green] matching [cyan]{fragment}[/cyan] from index '{index_name}'."
            )
        return CommandResult(
            message=f"[yellow]Nothing found[/yellow] matching '{fragment}' in index '{index_name}'."
        )
    except Exception as exc:
        return CommandResult(message=f"[red]Purge failed:[/red] {exc}")


async def _cmd_sync_hacktricks(args: list[str], engine: object, settings: Settings) -> CommandResult:
    """Sync and ingest HackTricks: Clone -> Parse -> Embed -> Store in Redis.
    
    This is a one-time or occasional operation that pulls the latest HackTricks
    repository, extracts all Markdown files, chunks them semantically, embeds them,
    and stores them in Redis for instant RAG retrieval.
    
    Usage:
        /sync-hacktricks              — Use default cache directory (~/.cache/hacktricks)
        /sync-hacktricks /path/to/ht  — Use custom HackTricks directory
    """
    import asyncio
    from pathlib import Path as _Path
    
    # Determine HackTricks directory
    if args:
        hacktricks_dir = _Path(args[0]).expanduser().resolve()
    else:
        hacktricks_dir = _Path.home() / ".cache" / "hacktricks"
    
    try:
        from mentor.kb.sync_command import sync_hacktricks_command
        
        # Run the sync in the background (non-blocking)
        # This returns immediately but the sync continues
        asyncio.create_task(
            sync_hacktricks_command(
                hacktricks_dir=hacktricks_dir,
                settings=settings,
                embed_fn=None,  # Uses default from settings
            )
        )
        
        return CommandResult(
            message=(
                f"[cyan]HackTricks sync started in background.[/cyan]\n"
                f"[dim]Target: {hacktricks_dir}[/dim]\n"
                "[yellow]This may take a few minutes. Check back in 2-5 min.[/yellow]\n"
                "[green]Once complete, Zero Cool will have instant access to the entire HackTricks library.[/green]"
            )
        )
    except Exception as exc:
        return CommandResult(
            message=(
                f"[red]HackTricks sync failed:[/red] {exc}\n"
                "[yellow]Make sure:[/yellow]\n"
                "  • git is installed\n"
                "  • Redis is running\n"
                "  • Internet connection is available"
            )
        )


async def _cmd_sync_all(args: list[str], engine: object, settings: Settings) -> CommandResult:
    """Refresh all configured local knowledge sources."""
    import asyncio

    try:
        from mentor.kb.sync_command import sync_all_command

        asyncio.create_task(sync_all_command(settings=settings))
        return CommandResult(
            message=(
                "[cyan]Global library sync started.[/cyan]\n"
                "Refreshing IppSec / GTFOBins / LOLBAS / HackTricks / Payloads from sources.yaml.\n"
                "[yellow]This may take several minutes depending on repo size and embedding throughput.[/yellow]"
            ),
            session_prefix="__sync_all__",
        )
    except Exception as exc:
        return CommandResult(message=f"[red]sync-all failed:[/red] {exc}")


async def _cmd_search(args: list[str], engine: object, settings: Settings) -> CommandResult:
    """Run a direct local-memory search against IppSec + HackTricks and synthesize results."""
    query = " ".join(args).strip()
    if not query:
        return CommandResult(
            message="[yellow]Usage:[/yellow] /search <query>",
        )
    return CommandResult(
        message=f"[cyan]Queued local search for:[/cyan] {query}",
        session_prefix="__search__",
        search_query=query,
    )


# ---------------------------------------------------------------------------
# Registry  —  verb → handler
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, _Handler] = {
    "box": _cmd_box,
    "new-box": _cmd_new_box,
    "newbox": _cmd_new_box,      # typo-tolerant alias
    "vision": _cmd_vision,
    "upload": _cmd_upload,
    "loot": _cmd_loot,
    "exit": _cmd_exit,
    "quit": _cmd_exit,
    "clear": _cmd_clear,
    "victory": _cmd_victory,
    "pwned": _cmd_victory,       # alias
    "help": _cmd_help,
    "?": _cmd_help,
    "search": _cmd_search,
    "add-source": _cmd_add_source,
    "purge-source": _cmd_purge_source,
    "sync-all": _cmd_sync_all,
    "sync-hacktricks": _cmd_sync_hacktricks,
}


# ---------------------------------------------------------------------------
# Public dispatch entry point
# ---------------------------------------------------------------------------

async def dispatch(
    raw_input: str,
    engine: object,
    settings: Settings,
) -> CommandResult | None:
    """Parse *raw_input*; if it's a slash command dispatch it and return the result.

    Returns *None* when the input is not a slash command so the caller can
    fall through to normal chat handling.
    """
    if not is_slash_command(raw_input):
        return None

    verb, args = parse_slash_command(raw_input)
    handler = _REGISTRY.get(verb)
    if handler is None:
        return CommandResult(
            message=(
                f"[red]Unknown command:[/red] /{verb}  "
                "— type [cyan]/help[/cyan] for a list."
            )
        )

    return await handler(args, engine, settings)

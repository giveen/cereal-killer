"""HackTricks synchronization and ingestion CLI command."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _default_sources_path() -> Path:
    candidates = [
        Path(__file__).resolve().parents[2] / "cereal_killer" / "kb" / "sources.yaml",
        Path("/app/src/cereal_killer/kb/sources.yaml"),
        Path.cwd() / "src" / "cereal_killer" / "kb" / "sources.yaml",
    ]
    for path in candidates:
        if path.exists():
            return path
    # Return primary path for clearer error messages when nothing exists.
    return candidates[0]


async def sync_all_command(
    settings: Any | None = None,
    config_path: Path | None = None,
) -> dict[str, dict[str, int]]:
    """Refresh all configured knowledge sources from sources.yaml."""
    from mentor.kb.library_ingest import sync_all_sources

    if settings is None:
        from cereal_killer.config import Settings

        settings = Settings()

    path = config_path or _default_sources_path()
    if not path.exists():
        raise FileNotFoundError(f"sources config not found: {path}")

    summary: dict[str, dict[str, int]] = {}

    # Keep ippsec (answer key) fresh first.
    try:
        from cereal_killer.knowledge_base import sync_ippsec_dataset

        await asyncio.to_thread(sync_ippsec_dataset)
        summary["ippsec"] = {"ingested": 1, "failed": 0}
        try:
            from redis import Redis

            client = Redis.from_url(settings.redis_url, decode_responses=True)
            client.hset(
                "kb:sync:ippsec",
                mapping={
                    "last_sync": datetime.now(UTC).isoformat(),
                    "source": "ippsec",
                    "index": settings.redis_index,
                    "count": "1",
                    "failed": "0",
                },
            )
        except Exception:
            pass
    except Exception:
        summary["ippsec"] = {"ingested": 0, "failed": 1}

    source_summary = await sync_all_sources(settings, path)
    summary.update(source_summary)
    return summary


async def sync_hacktricks_command(
    hacktricks_dir: Path | None = None,
    settings: Any | None = None,
    embed_fn: Any = None,
) -> None:
    """Full sync pipeline: Clone -> Parse -> Embed -> Cleanup."""
    from mentor.kb.hacktricks_ingest import (
        discover_markdown_files,
        extract_document,
        chunk_document,
        ingest_hacktricks_batch,
    )

    if hacktricks_dir is None:
        hacktricks_dir = Path.home() / ".cache" / "hacktricks"

    if settings is None:
        from cereal_killer.config import Settings

        settings = Settings()

    if embed_fn is None:
        from mentor.kb.query import embed

        embed_fn = embed

    print("[*] HackTricks Deep Read Synchronization")
    print(f"[*] Target directory: {hacktricks_dir}")

    # Step 1: Clone/Update HackTricks repository
    print("[+] Step 1: Fetching HackTricks repository...")
    if not hacktricks_dir.exists():
        print(f"    Cloning carlospolop/hacktricks to {hacktricks_dir}...")
        try:
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth=1",
                    "https://github.com/carlospolop/hacktricks.git",
                    str(hacktricks_dir),
                ],
                check=True,
                capture_output=True,
            )
            print("    ✓ Clone complete")
        except subprocess.CalledProcessError as e:
            print(f"    ✗ Clone failed: {e.stderr.decode()}")
            sys.exit(1)
    else:
        print(f"    Updating existing clone...")
        try:
            subprocess.run(
                ["git", "-C", str(hacktricks_dir), "pull"],
                check=True,
                capture_output=True,
            )
            print("    ✓ Update complete")
        except subprocess.CalledProcessError:
            print("    ⚠ Update failed, continuing with existing files")

    # Step 2: Discover and extract all markdown files
    print("[+] Step 2: Discovering and extracting markdown files...")
    md_files = discover_markdown_files(hacktricks_dir)
    print(f"    Found {len(md_files)} markdown files")

    if not md_files:
        print("    ✗ No markdown files found in HackTricks/src")
        sys.exit(1)

    # Step 3: Parse documents and chunk
    print("[+] Step 3: Parsing and chunking documents...")
    all_chunks: list[Any] = []
    for idx, md_file in enumerate(md_files, 1):
        if idx % 100 == 0 or idx == len(md_files):
            print(f"    Processing {idx}/{len(md_files)}...")

        try:
            doc = extract_document(md_file)
            if doc.raw_content:
                chunks = chunk_document(doc)
                all_chunks.extend(chunks)
        except Exception as e:
            logger.warning(f"Failed to process {md_file}: {e}")
            continue

    print(f"    ✓ Created {len(all_chunks)} semantic chunks")

    # Step 4: Vectorize and ingest to Redis
    print("[+] Step 4: Vectorizing and ingesting to Redis...")
    print(f"    Using Redis: {settings.redis_url}")

    try:
        from redis import Redis

        redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
        redis_client.ping()
    except Exception as e:
        print(f"    ✗ Redis connection failed: {e}")
        sys.exit(1)

    # Batch ingestion with async embedding
    async def async_embed_wrapper(text: str) -> list[float]:
        """Wrapper to handle sync embed function if needed."""
        return await embed_fn(text) if asyncio.iscoroutinefunction(embed_fn) else embed_fn(text)

    try:
        stats = await ingest_hacktricks_batch(
            chunks=all_chunks,
            embed_fn=async_embed_wrapper,
            redis_client=redis_client,
            index_name="hacktricks",
            batch_size=50,
        )
    except Exception as e:
        print(f"    ✗ Ingestion failed: {e}")
        sys.exit(1)

    print(f"    ✓ Ingestion complete:")
    print(f"      - Total chunks: {stats['total_chunks']}")
    print(f"      - Ingested: {stats['ingested']}")
    print(f"      - Failed: {stats['failed']}")
    print(f"      - Batches: {stats['batches']}")

    # Step 5: Cleanup
    print("[+] Step 5: Cleanup...")
    if hacktricks_dir.parent.name == ".cache":
        # Only clean up temporary clones in .cache, not user-provided dirs
        print(f"    (Keeping {hacktricks_dir} for future updates)")

    print("[✓] HackTricks synchronization complete!")
    print("[*] Zero Cool now has the entire HackTricks library in his sub-millisecond memory.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "sync-all":
        result = asyncio.run(sync_all_command())
        print(result)
    else:
        asyncio.run(sync_hacktricks_command())

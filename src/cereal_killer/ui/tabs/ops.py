from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class SystemReadinessResult:
    ok: bool
    details: str


async def check_system_readiness(llm_base_url: str) -> SystemReadinessResult:
    _ = llm_base_url  # kept for backward-compatible call signatures
    repo_root = Path(__file__).resolve().parents[4]
    checker = repo_root / "scripts" / "setup" / "check_env.py"
    if not checker.exists():
        return SystemReadinessResult(False, "check_env.py missing")

    model_dir = os.path.expanduser(os.getenv("GIBSON_MODEL_DIR", "~/models/gibson"))

    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(checker),
            "--model-dir",
            model_dir,
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(repo_root),
        )
        stdout, stderr = await process.communicate()
    except Exception:
        return SystemReadinessResult(False, "setup check launch failed")

    text_out = (stdout or b"").decode("utf-8", errors="replace").strip()
    if not text_out:
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        return SystemReadinessResult(False, err or "empty setup check response")

    try:
        payload = json.loads(text_out)
    except json.JSONDecodeError:
        return SystemReadinessResult(False, "invalid setup check json")

    status = str(payload.get("status", "")).upper()
    details = payload.get("details", [])

    if status == "READY":
        warning_details = [
            item for item in details if str(item.get("status", "")).upper() == "WARN"
        ]
        if warning_details:
            detail_text = str(warning_details[0].get("message", ""))
            return SystemReadinessResult(True, detail_text)
        return SystemReadinessResult(True, "")

    failed = [item for item in details if str(item.get("status", "")).upper() == "FAIL"]
    if failed:
        short = ", ".join(str(item.get("name", "")).strip() for item in failed[:3])
        return SystemReadinessResult(False, short)
    return SystemReadinessResult(False, "setup check failed")

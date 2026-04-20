from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from cereal_killer.config import get_settings
from cereal_killer.engine import LLMEngine
from cereal_killer.knowledge_base import KnowledgeBase
from cereal_killer.ui import CerealKillerApp


def _run_preflight() -> tuple[bool, str]:
    repo_root = Path(__file__).resolve().parents[2]
    checker = repo_root / "scripts" / "setup" / "check_env.py"
    if not checker.exists():
        return False, "setup checker missing"

    try:
        proc = subprocess.run(
            [sys.executable, str(checker), "--json"],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )
    except Exception:
        return True, "preflight launch failed"

    try:
        payload = json.loads((proc.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        return True, "invalid preflight json"

    hard_fail_names = {"NVIDIA", "CUDA", "llama-swap"}
    failures = [
        item
        for item in payload.get("details", [])
        if str(item.get("status", "")).upper() == "FAIL"
        and str(item.get("name", "")) in hard_fail_names
    ]
    if not failures:
        return False, ""

    summary = ", ".join(str(item.get("name", "")) for item in failures)
    return True, summary


def main() -> None:
    settings = get_settings()
    hard_fail, reason = _run_preflight()
    app = CerealKillerApp(
        engine=LLMEngine(settings),
        kb=KnowledgeBase(settings),
        preflight_hard_fail=hard_fail,
        preflight_reason=reason,
    )
    app.run()


if __name__ == "__main__":
    main()

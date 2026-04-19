from __future__ import annotations

import sys
from pathlib import Path


def _ensure_src_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"
    src_str = str(src_dir)
    if src_dir.is_dir() and src_str not in sys.path:
        sys.path.insert(0, src_str)


_ensure_src_on_path()

from cereal_killer.knowledge_base import sync_ippsec_dataset


if __name__ == "__main__":
    sync_ippsec_dataset()

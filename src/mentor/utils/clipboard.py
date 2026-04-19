from __future__ import annotations

import shutil
import subprocess
from typing import Callable

try:
    import pyperclip
except ImportError:  # pragma: no cover
    pyperclip = None  # type: ignore[assignment]


def copy_text(text: str, fallback: Callable[[str], None] | None = None) -> None:
    content = text or ""

    # Try pyperclip first (works when backend is configured in container).
    if pyperclip is not None:
        try:
            pyperclip.copy(content)
            return
        except Exception:
            pass

    # Explicitly try Ubuntu-friendly host clipboard tools.
    if shutil.which("wl-copy"):
        subprocess.run(["wl-copy"], input=content, text=True, check=False)
        return
    if shutil.which("xclip"):
        subprocess.run(["xclip", "-selection", "clipboard"], input=content, text=True, check=False)
        return

    if fallback is not None:
        fallback(content)

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


def read_text() -> str:
    """Read text from the system clipboard.  Returns empty string on failure."""
    # Try pyperclip first.
    if pyperclip is not None:
        try:
            text = pyperclip.paste()
            if text:
                return text
        except Exception:
            pass

    if shutil.which("wl-paste"):
        result = subprocess.run(["wl-paste", "--no-newline"], capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout:
            return result.stdout

    if shutil.which("xclip"):
        result = subprocess.run(
            ["xclip", "-selection", "clipboard", "-o"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout

    if shutil.which("xsel"):
        result = subprocess.run(["xsel", "--clipboard", "--output"], capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout:
            return result.stdout

    return ""

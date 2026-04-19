from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path


def capture_screenshot(target_dir: str = "/tmp") -> str:
    """Capture a best-effort screenshot and return the file path.

    This is scaffolding for multimodal workflows: it attempts a real desktop capture
    via pyautogui first, then mss as a fallback.
    """
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = Path(target_dir) / f"mentor-screenshot-{timestamp}.png"

    try:
        import pyautogui
    except ImportError:
        pyautogui = None  # type: ignore[assignment]

    if pyautogui is not None:
        try:
            shot = pyautogui.screenshot()
            shot.save(path)
            return str(path)
        except Exception:
            pass

    try:
        import mss
        import mss.tools
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pyautogui or mss is required for screenshot capture scaffolding.") from exc

    with mss.mss() as sct:
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        shot = sct.grab(monitor)
        mss.tools.to_png(shot.rgb, shot.size, output=str(path))

    return str(path)

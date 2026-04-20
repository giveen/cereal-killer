from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path

from textual.message import Message

try:
    from PIL import Image, ImageGrab
except ImportError:  # pragma: no cover - allows non-vision paths to run without pillow
    Image = None  # type: ignore[assignment]
    ImageGrab = None  # type: ignore[assignment]


@dataclass(slots=True)
class ClipboardSnapshot:
    image_path: Path
    digest: str
    preview: str


class ClipboardImageDetected(Message):
    """Posted to the TUI when a new clipboard image is saved."""

    def __init__(self, snapshot: ClipboardSnapshot) -> None:
        super().__init__()
        self.snapshot = snapshot


class ClipboardImageWatcher:
    def __init__(
        self,
        output_path: Path | None = None,
        poll_interval_secs: float = 1.0,
    ) -> None:
        self.output_path = output_path or Path("data/temp/clipboard_obs.png")
        self.poll_interval_secs = max(0.2, poll_interval_secs)
        self._last_digest = ""

    async def watch(self):
        while True:
            snapshot = self.poll_once()
            if snapshot is not None:
                yield ClipboardImageDetected(snapshot)
            await asyncio.sleep(self.poll_interval_secs)

    def poll_once(self) -> ClipboardSnapshot | None:
        clipboard_image = self._grab_clipboard_image()
        if clipboard_image is None:
            return None

        digest = self._digest_image(clipboard_image)
        if digest == self._last_digest:
            return None

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        clipboard_image.save(self.output_path, format="PNG")
        self._last_digest = digest
        preview = self._ascii_preview(clipboard_image)
        return ClipboardSnapshot(image_path=self.output_path, digest=digest, preview=preview)

    @staticmethod
    def _grab_clipboard_image():
        if ImageGrab is None:
            return None
        try:
            payload = ImageGrab.grabclipboard()
        except Exception:
            return None

        if Image is not None and isinstance(payload, Image.Image):
            return payload

        # Some Linux clipboards return file paths; load first image candidate.
        if isinstance(payload, list) and payload:
            for entry in payload:
                path = Path(str(entry))
                if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"} and path.exists():
                    try:
                        if Image is None:
                            return None
                        return Image.open(path)
                    except Exception:
                        continue
        
        # Fallback: Try xclip on Linux if running under X11/Wayland.
        try:
            import subprocess
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                capture_output=True,
                timeout=2,
            )
            if result.returncode == 0 and result.stdout:
                from io import BytesIO
                return Image.open(BytesIO(result.stdout))
        except Exception:
            pass
        
        return None

    @staticmethod
    def _digest_image(image) -> str:
        normalized = image.convert("RGB")
        return hashlib.sha256(normalized.tobytes()).hexdigest()

    @staticmethod
    def _ascii_preview(image) -> str:
        # Keep preview intentionally tiny for the sidebar.
        grayscale = image.convert("L")
        width = 24
        max_height = 10
        ratio = grayscale.height / max(1, grayscale.width)
        height = max(4, min(max_height, int(width * ratio * 0.5)))
        resized = grayscale.resize((width, height))
        ramp = " .:-=+*#%@"

        rows: list[str] = []
        pixels = resized.load()
        for y in range(height):
            chars: list[str] = []
            for x in range(width):
                shade = pixels[x, y]
                idx = int((shade / 255) * (len(ramp) - 1))
                chars.append(ramp[idx])
            rows.append("".join(chars))
        return "\n".join(rows)


def clear_clipboard_buffer(path: Path | None = None) -> bool:
    target = path or Path("data/temp/clipboard_obs.png")
    try:
        target.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def ascii_preview_for_image(path: Path) -> str:
    if Image is None:
        return ""
    try:
        with Image.open(path) as image:
            return ClipboardImageWatcher._ascii_preview(image)
    except Exception:
        return ""

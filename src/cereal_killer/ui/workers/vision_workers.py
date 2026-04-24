"""Vision/image analysis worker management."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from cereal_killer.ui.app import CerealKillerApp

from textual import work

from ..base import resolve_dashboard, update_llm_cache_metrics as _update_llm_cache_metrics_helper

VISION_BUFFER_PATH = Path("data/temp/clipboard_obs.png")
VISION_PROMPT = (
    "Zero Cool, I've just pasted a screenshot. "
    "Look at the error/output and tell me where I'm failing."
)
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


class VisionWorkerManager:
    """Manages vision/image analysis and media workers."""

    def __init__(self, app: "CerealKillerApp") -> None:
        self._app = app

    _dashboard = resolve_dashboard

    def _worker_name(self, method_name: str) -> str:
        return f"{self.__class__.__name__}.{method_name}"

    @work(exclusive=True, thread=False, group="llm")
    async def run_vision_worker(
        self,
        image_path: str,
        source_label: str = "Clipboard",
        mark_context: bool = False,
    ) -> None:
        dashboard = self._dashboard()
        worker_name = self._worker_name("run_vision_worker")
        self._app._register_worker(worker_name, asyncio.current_task())
        image_file = Path(image_path)
        if not image_file.exists():
            dashboard.append_system(f"Vision input missing: {image_file}", style="yellow")
            return

        self._app._analysis_busy(True)
        dashboard.set_active_tool("Vision")
        if mark_context:
            dashboard.set_upload_progress(25, "IMAGE INGEST")
            dashboard.set_context_chip(image_file.name, ingest_type="image")
        dashboard.append_system(f"Image Uploaded: {image_file.name} ({source_label})", style="bold cyan")
        dashboard.append_system(f"Zero Cool is analyzing {image_file.name}...", style="bold green")
        try:
            await self._app._with_worker_cancellation(self._vision_body(str(image_file), mark_context))
        except Exception as exc:
            dashboard.append_system(f"Vision analysis error: {exc}", style="red")
            if mark_context:
                dashboard.set_upload_progress(0, "IMAGE INGEST")
        finally:
            dashboard.set_active_tool("Idle")
            self._app._analysis_busy(False)
            self._app._unregister_worker(worker_name)

    async def _vision_body(self, image_path: str, mark_context: bool) -> None:
        image_file = Path(image_path)
        response = await self._app.engine.chat_with_image(
            user_prompt=VISION_PROMPT,
            image_path=str(image_file),
            history_commands=self._app.active_history,
            pathetic_meter=self._app.pathetic_meter,
        )
        self._update_llm_cache_metrics(response.backend_meta)
        await self._app._consume_llm_response(response.answer, response.reasoning_content or response.thought)
        self._app._vision_analyzed_sources.add(str(image_file.expanduser().resolve()))
        dashboard = self._dashboard()
        if dashboard and mark_context:
            dashboard.set_upload_progress(100, "IMAGE INGEST")

    @work(exclusive=True, thread=False, group="media")
    async def run_remote_visual_worker(self, url: str) -> None:
        dashboard = self._dashboard()
        worker_name = self._worker_name("run_remote_visual_worker")
        self._app._register_worker(worker_name, asyncio.current_task())
        dashboard.set_active_tool("Media")
        try:
            await self._app._with_worker_cancellation(self._visual_body(url))
        except Exception as exc:
            dashboard.append_system(f"Remote image load failed: {exc}", style="red")
        finally:
            dashboard.set_active_tool("Idle")
            self._app._unregister_worker(worker_name)

    async def _visual_body(self, url: str) -> None:
        """Fetch remote image and save to buffer."""
        dashboard = self._dashboard()
        async with __import__("httpx").AsyncClient(timeout=20) as client:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()

        try:
            from PIL import Image
        except Exception:
            Image = None  # type: ignore[assignment]

        if Image is None:
            dashboard.append_system("Pillow is unavailable; cannot decode remote image.", style="red")
            return

        try:
            image = Image.open(__import__("io").BytesIO(response.content)).convert("RGB")
        except Exception as exc:
            dashboard.append_system(f"Could not decode remote image: {exc}", style="red")
            return

        remote_target = Path("data/temp/remote_visual_buffer.png")
        remote_target.parent.mkdir(parents=True, exist_ok=True)
        image.save(remote_target, format="PNG")
        image.close()

        self._app._uploaded_image_path = remote_target.resolve()
        dashboard.set_visual_buffer_image(remote_target, source="Remote")
        dashboard.append_system("Remote diagram loaded into Media Drawer.", style="bold cyan")
        self._app.notify("Remote image loaded", title="Visual Buffer", severity="information")

    def prime_uploaded_image(self, path: Path, source: str) -> None:
        resolved = path.expanduser().resolve()
        self._app._uploaded_image_path = resolved

        VISION_BUFFER_PATH.parent.mkdir(parents=True, exist_ok=True)
        if resolved != VISION_BUFFER_PATH:
            try:
                shutil.copyfile(resolved, VISION_BUFFER_PATH)
            except Exception:
                pass

        preview = self._app.ascii_preview_for_image(resolved)
        self._dashboard().set_visual_buffer_image(resolved, source=source, preview=preview)
        self._dashboard().append_system(f"Image Uploaded: {resolved.name}", style="bold cyan")

    @staticmethod
    def is_image_file(path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES

    @staticmethod
    def looks_like_image_url(url: str) -> bool:
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        if parsed.scheme not in {"http", "https"}:
            return False
        lowered_path = (parsed.path or "").lower()
        return any(lowered_path.endswith(suffix) for suffix in _IMAGE_SUFFIXES)

    @classmethod
    def extract_visual_candidate_url(cls, snippets: list[dict]) -> str | None:
        for snippet in snippets:
            url = str(snippet.get("url", "")).strip()
            if url and cls.looks_like_image_url(url):
                return url

            content = str(snippet.get("content", ""))
            import re

            IMAGE_URL_RE = re.compile(r"https?://[^\s\]\)>'\"]+\.(?:png|jpg|jpeg|webp|bmp|gif)(?:\?[^\s\]\)>'\"]*)?", re.IGNORECASE)
            match = IMAGE_URL_RE.search(content)
            if match:
                return match.group(0)
        return None

    def on_clipboard_image_detected(self, message: Any) -> None:
        snapshot = message.snapshot
        self._dashboard().set_visual_buffer_image(snapshot.image_path, source="Clipboard", preview=snapshot.preview)
        self._app._uploaded_image_path = snapshot.image_path
        self._app.notify(
            f"Clipboard image buffered as {snapshot.image_path.name}",
            title="Visual Buffer",
            severity="information",
        )

    def clear_visual_buffer(self) -> None:
        from cereal_killer.observer import clear_clipboard_buffer

        ok = clear_clipboard_buffer(VISION_BUFFER_PATH)
        self._dashboard().clear_visual_buffer()
        self._app._uploaded_image_path = None
        if ok:
            self._app.notify("Visual buffer cleared", title="Visual Buffer", severity="information")
        else:
            self._app.notify("Could not clear visual buffer", title="Visual Buffer", severity="warning")

    def on_upload_tree_file_selected(self, event: Any) -> None:
        path = Path(event.path)
        if not self.is_image_file(path):
            self._app.notify("Select an image file to analyze", title="Upload", severity="warning")
            return
        self.prime_uploaded_image(path, source="DirectoryTree")
        self.run_vision_worker(str(path), source_label="DirectoryTree")

    def queue_remote_visual_url(self, url: str) -> None:
        """Handle inline Gibson [VIEW IMAGE] links."""
        cleaned = (url or "").strip()
        if not cleaned:
            return
        self._dashboard().set_remote_image_candidate(cleaned)
        self.run_remote_visual_worker(cleaned)

    def on_visual_view_remote_pressed(self) -> None:
        url = self._dashboard().get_remote_image_candidate()
        if not url:
            self._app.notify("No remote diagram is queued.", title="Visual Buffer", severity="warning")
            return
        self.run_remote_visual_worker(url)

    def on_visual_send_zero_cool_pressed(self) -> None:
        image_path = self._dashboard().get_visual_buffer_image_path()
        if image_path is None:
            self._app.notify("Load an image first.", title="Visual Buffer", severity="warning")
            return
        source_key = str(image_path.expanduser().resolve())
        if source_key in self._app._vision_analyzed_sources:
            self._app.notify(
                "Zero Cool already analyzed this frame.",
                title="Visual Buffer",
                severity="information",
            )
            return
        self.run_vision_worker(str(image_path), source_label="Visual Buffer")

    def on_gibson_search_submitted(self, event: Any) -> None:
        query = event.value.strip()
        if not query:
            return
        self._app._run_gibson_search_worker(query)

    def on_gibson_result_selected(self, event: Any) -> None:
        snippet = self._dashboard().resolve_gibson_selection(event.option_index)
        if snippet is not None:
            self._dashboard().show_gibson_snippet(snippet)

    def on_gibson_synthesize_pressed(self) -> None:
        if not self._app._gibson_snippets:
            self._app.notify(
                "No results to synthesize — run a search first.",
                title="Gibson",
                severity="warning",
            )
            return
        self._app._run_gibson_synthesize_worker()

    def _update_llm_cache_metrics(self, backend_meta: dict[str, object] | None) -> None:
        _update_llm_cache_metrics_helper(self._app, backend_meta)

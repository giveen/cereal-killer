"""Document and image ingestion worker management."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cereal_killer.ui.app import CerealKillerApp

from textual import work

from cereal_killer.ingest_logic import build_document_prompt, is_document_path, is_image_path
from ..base import resolve_dashboard, update_llm_cache_metrics as _update_llm_cache_metrics_helper
from ..screens import IngestModal, IngestSelection


class IngestWorkerManager:
    """Manages document and image ingestion workers.

    Extracted from CerealKillerApp to encapsulate document/image
    ingestion logic.
    """

    def __init__(self, app: "CerealKillerApp") -> None:
        self._app = app

    def _worker_name(self, method_name: str) -> str:
        return f"{self.__class__.__name__}.{method_name}"

    @work(exclusive=True, thread=False, group="llm")
    async def _run_document_ingest_worker(self, file_path: str) -> None:
        dashboard = self._dashboard()
        worker_name = self._worker_name("run_document_ingest_worker")
        self._app._register_worker(worker_name, asyncio.current_task())
        path = Path(file_path)
        if not is_document_path(path):
            dashboard.append_system(f"Unsupported document type: {path.suffix}", style="yellow")
            return

        self._app._analysis_busy(True)
        dashboard.set_active_tool("Document Ingest")
        dashboard.set_upload_progress(20, "DOC INGEST")
        dashboard.set_context_chip(path.name, ingest_type="document")
        try:
            await self._app._with_worker_cancellation(self._doc_ingest_body(str(path)))
            dashboard.append_system(f"Document Uploaded: {path.name}", style="bold cyan")
            dashboard.set_upload_progress(100, "DOC INGEST")
        except json.JSONDecodeError as exc:
            dashboard.append_system(f"Invalid JSON document: {exc}", style="red")
            dashboard.set_upload_progress(0, "DOC INGEST")
        except Exception as exc:
            dashboard.append_system(f"Document ingest error: {exc}", style="red")
            dashboard.set_upload_progress(0, "DOC INGEST")
        finally:
            dashboard.set_active_tool("Idle")
            self._app._analysis_busy(False)
            dashboard.set_upload_progress(0, "")
            self._app._unregister_worker(worker_name)

    async def _doc_ingest_body(self, path_str: str) -> None:
        path = Path(path_str)
        payload = build_document_prompt(path)
        dashboard = self._dashboard()
        if dashboard:
            dashboard.set_upload_progress(55, "DOC INGEST")
        response = await self._app.engine.chat(
            payload.prompt,
            self._app.active_history,
            pathetic_meter=self._app.pathetic_meter,
        )
        self._update_llm_cache_metrics(response.backend_meta)
        await self._app._consume_llm_response(response.answer, response.reasoning_content or response.thought)

    def _open_ingest_modal(self, ingest_type: str) -> None:
        if ingest_type == "image" and Path("/screenshots").exists():
            root = Path("/screenshots")
        else:
            root = Path.cwd()
        self._app.push_screen(IngestModal(ingest_type=ingest_type, root_path=root), self._on_ingest_selection)

    def _on_ingest_selection(self, selection: Any) -> None:
        if selection is None:
            return

        chosen = selection.path.expanduser().resolve()
        dashboard = self._dashboard()
        dashboard.set_upload_progress(10, "UPLOAD")

        if selection.ingest_type == "image":
            if not is_image_path(chosen):
                self._app.notify("Choose a .png/.jpg/.jpeg file", title="Ingest", severity="warning")
                dashboard.set_upload_progress(0, "UPLOAD")
                return
            self._app._prime_uploaded_image(chosen, source="Modal")
            self._app._run_vision_worker(str(chosen), source_label="Modal", mark_context=True)
            return

        if not is_document_path(chosen):
            self._app.notify("Choose a .log/.txt/.json file", title="Ingest", severity="warning")
            dashboard.set_upload_progress(0, "UPLOAD")
            return
        self._run_document_ingest_worker(str(chosen))

    def _update_llm_cache_metrics(self, backend_meta: dict[str, object] | None) -> None:
        _update_llm_cache_metrics_helper(self._app, backend_meta)

    # Public aliases for delegation from app.py
    run_document_ingest_worker = _run_document_ingest_worker
    doc_ingest_body = _doc_ingest_body

    def open_ingest_modal(self, ingest_type: str) -> None:
        """Public alias for open_ingest_modal."""
        self._open_ingest_modal(ingest_type)

    def on_ingest_selection(self, selection: Any) -> None:
        """Public alias for on_ingest_selection."""
        self._on_ingest_selection(selection)

    _dashboard = resolve_dashboard

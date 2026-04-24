"""Ingestion UI components for the cereal-killer TUI."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Static


@dataclass(slots=True)
class IngestSelection:
    path: Path
    ingest_type: str


class FilteredIngestTree(DirectoryTree):
    def __init__(
        self, path: str, *, allowed_suffixes: set[str], **kwargs: object
    ) -> None:
        super().__init__(path, **kwargs)
        self.allowed_suffixes = {
            suffix.lower() for suffix in allowed_suffixes
        }

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        filtered: list[Path] = []
        for candidate in paths:
            try:
                if candidate.is_dir() or candidate.suffix.lower() in self.allowed_suffixes:
                    filtered.append(candidate)
            except OSError:
                continue
        return filtered


class IngestModal(ModalScreen):
    IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
    DOCUMENT_SUFFIXES = {".log", ".txt", ".json"}

    def __init__(self, ingest_type: str, root_path: Path) -> None:
        super().__init__()
        self.ingest_type = ingest_type
        self.root_path = root_path
        self._selected_path: Path | None = None

    @property
    def allowed_suffixes(self) -> set[str]:
        return (
            self.IMAGE_SUFFIXES
            if self.ingest_type == "image"
            else self.DOCUMENT_SUFFIXES
        )

    def compose(self) -> ComposeResult:
        mode_label = (
            "SCREENSHOT INGEST"
            if self.ingest_type == "image"
            else "DOCUMENT INGEST"
        )
        with Vertical(id="ingest_modal_shell"):
            yield Static(
                f"{mode_label} // Select source file",
                id="ingest_modal_title",
            )
            yield FilteredIngestTree(
                str(self.root_path),
                id="ingest_modal_tree",
                allowed_suffixes=self.allowed_suffixes,
            )
            suffix_hint = ", ".join(sorted(self.allowed_suffixes))
            yield Static(
                f"Allowed: {suffix_hint}",
                id="ingest_modal_hint",
            )
            yield Static(
                "Selected: none",
                id="ingest_modal_selected",
            )
            with Horizontal(id="ingest_modal_actions"):
                yield Button(
                    "[SUBMIT TO GIBSON]",
                    id="ingest_modal_submit",
                    variant="primary",
                )
                yield Button(
                    "Cancel",
                    id="ingest_modal_cancel",
                    variant="default",
                )

    @on(DirectoryTree.FileSelected, "#ingest_modal_tree")
    def on_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        chosen = Path(event.path)
        if chosen.suffix.lower() not in self.allowed_suffixes:
            self.notify("File type not allowed in this ingest mode", title="Ingest", severity="warning")
            return
        self._selected_path = chosen
        self.query_one("#ingest_modal_selected", Static).update(f"Selected: {chosen.name}")

    @on(Button.Pressed, "#ingest_modal_submit")
    def submit_ingest(self) -> None:
        if self._selected_path is None:
            self.notify("Select a file first", title="Ingest", severity="warning")
            return
        self.dismiss(IngestSelection(path=self._selected_path, ingest_type=self.ingest_type))

    @on(Button.Pressed, "#ingest_modal_cancel")
    def cancel_ingest(self) -> None:
        self.dismiss(None)

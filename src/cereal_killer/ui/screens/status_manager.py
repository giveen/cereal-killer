"""Status indicator manager for the dashboard."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from textual.widgets import Static

from cereal_killer.ui.widgets import SidebarStatus, VerticalProgressBar

if TYPE_CHECKING:
    from .dashboard import MainDashboard


class StatusManager:
    """Manages all dashboard status indicators."""

    def __init__(self, dashboard: MainDashboard) -> None:
        self._dashboard = dashboard

    def set_phase(self, phase: str) -> None:
        """Update phase indicator."""
        phase_widget = self._dashboard.query_one("#current_phase", Static)
        for cls in (
            "phase-idle",
            "phase-recon",
            "phase-enumeration",
            "phase-exploitation",
            "phase-post",
        ):
            phase_widget.remove_class(cls)
        phase_widget.update(f"PHASE: {phase}")
        if phase == "[RECON]":
            phase_widget.add_class("phase-recon")
        elif phase == "[ENUMERATION]":
            phase_widget.add_class("phase-enumeration")
        elif phase == "[EXPLOITATION]":
            phase_widget.add_class("phase-exploitation")
        elif phase == "[POST-EXPLOITATION]":
            phase_widget.add_class("phase-post")
        else:
            phase_widget.add_class("phase-idle")

    def set_active_tool(self, tool_name: str) -> None:
        """Update active tool display."""
        self._dashboard.query_one("#active_tool", Static).update(f"TOOL: {tool_name}")

    def set_visual_buffer(self, description: str, preview: str = "") -> None:
        """Set visual buffer (legacy)."""
        sidebar = self._dashboard.query_one("#intel_sidebar", SidebarStatus)
        sidebar.set_remote_image_candidate(description if description.startswith(("http://", "https://")) else None)

    def set_visual_buffer_image(
        self, image_path: Path, *, source: str, preview: str = ""
    ) -> None:
        """Set visual buffer with image."""
        sidebar = self._dashboard.query_one("#intel_sidebar", SidebarStatus)
        sidebar.set_visual_buffer_image(image_path, source=source, preview=preview)

    def set_remote_image_candidate(self, url: str | None) -> None:
        """Set remote image candidate."""
        sidebar = self._dashboard.query_one("#intel_sidebar", SidebarStatus)
        sidebar.set_remote_image_candidate(url)

    def get_remote_image_candidate(self) -> str | None:
        """Get remote image candidate."""
        sidebar = self._dashboard.query_one("#intel_sidebar", SidebarStatus)
        return sidebar.get_remote_image_candidate()

    def get_visual_buffer_image_path(self) -> Path | None:
        """Get visual buffer image path."""
        sidebar = self._dashboard.query_one("#intel_sidebar", SidebarStatus)
        return sidebar.get_visual_buffer_image_path()

    def clear_visual_buffer(self) -> None:
        """Clear visual buffer."""
        self._dashboard.query_one("#intel_sidebar", SidebarStatus).clear_visual_buffer()

    def set_pathetic_meter(self, value: int) -> None:
        """Update pathetic meter."""
        self._dashboard.query_one("#pathetic_meter_bar", VerticalProgressBar).set_value(value)
        self._dashboard.query_one("#pathetic_meter_value", Static).update(f"{value}/10")

    def set_terminal_link_online(self, online: bool) -> None:
        """Update terminal link status."""
        sidebar = self._dashboard.query_one("#intel_sidebar", SidebarStatus)
        sidebar.set_terminal_link_status(online)

    def set_knowledge_sync_status(self, statuses: dict[str, str]) -> None:
        """Update knowledge sync status."""
        sidebar = self._dashboard.query_one("#intel_sidebar", SidebarStatus)
        sidebar.set_knowledge_sync_status(statuses)

    def set_github_api_status(self, summary: str) -> None:
        """Update GitHub API status."""
        sidebar = self._dashboard.query_one("#intel_sidebar", SidebarStatus)
        sidebar.set_github_api_status(summary)

    def set_system_readiness(self, ok: bool, details: str = "") -> None:
        """Set system readiness."""
        sidebar = self._dashboard.query_one("#intel_sidebar", SidebarStatus)
        sidebar.set_system_readiness(ok, details)

    def set_llm_cache_metrics(
        self, latency_ms: int | None, tokens_cached: int | None
    ) -> None:
        """Update LLM cache metrics."""
        sidebar = self._dashboard.query_one("#intel_sidebar", SidebarStatus)
        sidebar.set_llm_cache_metrics(latency_ms, tokens_cached)

    def set_context_token_counter(self, current_tokens: int, max_tokens: int) -> None:
        """Update context token counter."""
        counter = self._dashboard.query_one("#context_token_counter", Static)
        counter.update(f"Active Context: {max(0, current_tokens)} / {max(0, max_tokens)}")

    async def pulse_terminal_link(self) -> None:
        """Pulse terminal link indicator."""
        sidebar = self._dashboard.query_one("#intel_sidebar", SidebarStatus)
        await sidebar.pulse_terminal_link()

    def set_upload_progress(self, value: int, label: str = "UPLOAD") -> None:
        """Set upload progress."""
        sidebar = self._dashboard.query_one("#intel_sidebar", SidebarStatus)
        sidebar.set_upload_progress(value, label)

    def set_context_chip(self, filename: str, *, ingest_type: str) -> None:
        """Set context chip."""
        chip = self._dashboard.query_one("#context_chip", Static)
        marker = "IMG" if ingest_type == "image" else "DOC"
        chip.update(f"[{marker}] {filename}")
        chip.remove_class("muted-chip")
        chip.add_class("active-chip")

    def set_boot_status(self, text: str) -> None:
        """Set boot status."""
        box = self._dashboard.query_one("#boot_status_box", Static)
        body = (text or "").strip()
        if body:
            box.update(body)
            box.styles.display = "block"
        else:
            box.update("")
            box.styles.display = "none"

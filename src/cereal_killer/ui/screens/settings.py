"""Settings screen for the cereal-killer TUI."""
from __future__ import annotations

from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static, Switch

# Settings fields that are editable in the UI.
_SETTINGS_FIELDS = [
    # (attribute_name, label, is_bool, is_slider)
    ("llm_base_url", "LLM Base URL", False, False),
    ("llm_model", "LLM Model", False, False),
    ("llm_vision_model", "LLM Vision Model", False, False),
    ("llm_api_key", "API Key", False, False),
    ("reasoning_parser", "Reasoning Parser", False, False),
    ("preserve_thinking", "Preserve Thinking", True, False),
    ("max_model_len", "Max Model Length", False, False),
    ("searxng_base_url", "SearXNG Base URL", False, False),
    ("searxng_vector_threshold", "SearXNG Vector Threshold", False, False),
    ("snark_level", "Snark Level", False, True),
    ("backend_trace_enabled", "Backend Trace Enabled", True, False),
    ("rag_timeout", "RAG Timeout", False, False),
]


class SettingsScreen(ModalScreen[bool]):
    """Modal screen for editing application settings."""

    CSS = """
    #settings_shell {
        padding: 1;
        width: 80;
        height: 90%;
    }

    #settings_title {
        text-align: center;
        dock: top;
        margin-bottom: 1;
        background: $primary;
        color: $text;
        width: 100%;
        height: 1;
        content-align: center middle;
    }

    #settings_form {
        margin: 1;
    }

    .setting-group {
        margin-bottom: 1;
        padding: 1;
        border: round $primary;
        min-width: 30;
    }

    .setting-label {
        dock: top;
        color: $accent;
        margin-bottom: 1;
        width: 100%;
    }

    .setting-input {
        width: 100%;
    }

    .setting-value {
        color: $primary;
        margin-left: 1;
    }

    #settings_actions {
        dock: bottom;
        align-horizontal: center;
        margin-top: 1;
    }

    #settings_actions Button {
        width: 20;
        margin: 0 1;
    }

    .setting-section {
        margin-top: 1;
    }

    .section-title {
        dock: top;
        color: $accent;
        margin-bottom: 1;
        height: 1;
        width: 100%;
    }
    """

    def __init__(self, settings, app: Any = None) -> None:
        """Initialize the settings screen.

        Args:
            settings: The Settings object to read from and write to.
            app: Optional reference to CerealKillerApp for settings reload.
        """
        super().__init__()
        self._settings = settings
        self._app = app  # Store app reference for reload
        self._original_values: dict[str, str] = {}
        self._input_widgets: dict[str, Input | Switch] = {}

    def compose(self) -> ComposeResult:
        """Compose the settings screen layout."""
        with Vertical(id="settings_shell"):
            yield Static("⚙ SETTINGS // CONFIGURATION", id="settings_title")

            with ScrollableContainer(id="settings_form"):
                # Group 1: LLM Settings
                yield Static("[dim]LLM SETTINGS[/dim]", classes="section-title")
                yield self._render_setting_group(self._llm_settings())

                # Group 2: Web Search
                yield Static("[dim]WEB SEARCH[/dim]", classes="section-title")
                yield self._render_search_settings()

                # Group 3: Behavior
                yield Static("[dim]BEHAVIOR[/dim]", classes="section-title")
                yield self._render_behavior_settings()

            with Horizontal(id="settings_actions"):
                yield Button("Apply & Save", id="settings_apply", variant="primary")
                yield Button("Cancel", id="settings_cancel", variant="default")

    def on_mount(self) -> None:
        """Save original values for reset on cancel."""
        self._original_values = {
            attr: str(getattr(self._settings, attr, ""))
            for attr, _, is_bool, _ in _SETTINGS_FIELDS
        }

    def _llm_settings(self) -> list[tuple[str, str, bool, bool]]:
        """Get LLM-related settings fields."""
        return [
            ("llm_base_url", "LLM Base URL", False, False),
            ("llm_model", "LLM Model", False, False),
            ("llm_vision_model", "LLM Vision Model", False, False),
            ("llm_api_key", "API Key", False, False),
            ("reasoning_parser", "Reasoning Parser", False, False),
            ("preserve_thinking", "Preserve Thinking", True, False),
            ("max_model_len", "Max Model Length", False, False),
        ]

    def _search_settings(self) -> list[tuple[str, str, bool, bool]]:
        """Get search-related settings fields."""
        return [
            ("searxng_base_url", "SearXNG Base URL", False, False),
            ("searxng_vector_threshold", "SearXNG Vector Threshold", False, False),
        ]

    def _behavior_settings(self) -> list[tuple[str, str, bool, bool]]:
        """Get behavior-related settings fields."""
        return [
            ("snark_level", "Snark Level", False, True),
            ("backend_trace_enabled", "Backend Trace Enabled", True, False),
            ("rag_timeout", "RAG Timeout", False, False),
        ]

    def _render_setting_group(
        self, fields: list[tuple[str, str, bool, bool]]
    ) -> Vertical:
        """Render a group of settings fields."""
        with Vertical(id="settings_group") as container:
            for attr, label, is_bool, is_slider in fields:
                current_value = getattr(self._settings, attr, "")
                if is_bool:
                    widget = Switch(
                        name=attr,
                        id=f"setting_{attr}",
                        value=bool(current_value),
                    )
                elif is_slider:
                    widget = Input(
                        name=attr,
                        id=f"setting_{attr}",
                        value=str(current_value),
                        placeholder=f"{label} (1-10)",
                    )
                else:
                    widget = Input(
                        name=attr,
                        id=f"setting_{attr}",
                        value=str(current_value),
                        placeholder=label,
                    )
                self._input_widgets[attr] = widget

                with Vertical(classes="setting-group"):
                    yield Static(f"[{label}] // {attr}", classes="setting-label")
                    yield widget

        return container

    def _render_search_settings(self) -> Vertical:
        """Render search settings as a combined Vertical."""
        with Vertical(id="settings_group"):
            for attr, label, is_bool, is_slider in self._search_settings():
                current_value = getattr(self._settings, attr, "")
                if is_bool:
                    widget = Switch(name=attr, id=f"setting_{attr}", value=bool(current_value))
                elif is_slider:
                    widget = Input(
                        name=attr,
                        id=f"setting_{attr}",
                        value=str(current_value),
                        placeholder=f"{label} (1-10)",
                    )
                else:
                    widget = Input(
                        name=attr,
                        id=f"setting_{attr}",
                        value=str(current_value),
                        placeholder=label,
                    )
                self._input_widgets[attr] = widget
                yield Static(f"[{label}] // {attr}", classes="setting-label")
                yield widget

        return self._input_widgets.get("searxng_base_url", Static("")).parent.parent

    def _render_behavior_settings(self) -> Vertical:
        """Render behavior settings as a combined Vertical."""
        with Vertical(id="settings_group"):
            for attr, label, is_bool, is_slider in self._behavior_settings():
                current_value = getattr(self._settings, attr, "")
                if is_bool:
                    widget = Switch(name=attr, id=f"setting_{attr}", value=bool(current_value))
                elif is_slider:
                    widget = Input(
                        name=attr,
                        id=f"setting_{attr}",
                        value=str(current_value),
                        placeholder=f"{label} (1-10)",
                    )
                else:
                    widget = Input(
                        name=attr,
                        id=f"setting_{attr}",
                        value=str(current_value),
                        placeholder=label,
                    )
                self._input_widgets[attr] = widget
                yield Static(f"[{label}] // {attr}", classes="setting-label")
                yield widget

        return self._input_widgets.get("snark_level", Static("")).parent.parent

    @on(Button.Pressed, "#settings_apply")
    def apply_settings(self) -> None:
        """Apply the settings from input widgets to the settings object."""
        try:
            for attr, _, is_bool, is_slider in _SETTINGS_FIELDS:
                widget = self._input_widgets.get(attr)
                if widget is None:
                    continue

                if is_bool:
                    new_value = widget.value
                elif is_slider:
                    new_value = int(widget.value)
                else:
                    new_value = str(widget.value)

                setattr(self._settings, attr, new_value)

        except Exception as exc:
            self.notify(f"Error applying settings: {exc}", title="Settings", severity="error")
            return

        # Reload engine and KB settings
        self._reload_engine_settings()

        # Notify the user
        self.notify("Settings applied successfully.", title="Settings", severity="information")
        self.dismiss(True)

    @on(Button.Pressed, "#settings_cancel")
    def cancel_settings(self) -> None:
        """Cancel and restore original values."""
        try:
            for attr, _, is_bool, is_slider in _SETTINGS_FIELDS:
                original = self._original_values.get(attr, "")
                if is_bool:
                    setattr(self._settings, attr, str(original).lower() in {"true", "1", "yes", "on"})
                elif is_slider:
                    setattr(self._settings, attr, int(original))
                else:
                    setattr(self._settings, attr, original)
        except Exception:
            pass
        self.notify("Settings cancelled.", title="Settings", severity="warning")
        self.dismiss(False)

    def _reload_engine_settings(self) -> None:
        """Reload engine and KB settings from the settings object."""
        if not self._app:
            return

        try:
            # Update engine settings if engine has update_settings method
            if hasattr(self._app.engine, "update_settings"):
                self._app.engine.update_settings(self._settings)

            # Update KB settings if kb has update_settings method
            if hasattr(self._app.kb, "update_settings"):
                self._app.kb.update_settings(self._settings)

            # Update current target if it changed
            if hasattr(self._app, "current_target") and self._settings.llm_base_url:
                self._app._update_header_target(self._app.current_target)

        except Exception as exc:
            self.notify(f"Settings reload warning: {exc}", title="Settings", severity="warning")

    def action_close(self) -> None:
        """Handle Escape key to close the modal."""
        self.dismiss(False)

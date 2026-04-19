from __future__ import annotations

import asyncio

from textual.containers import Vertical
from textual.widgets import Button, Collapsible, Input, Markdown, Static


class VerticalProgressBar(Static):
    def __init__(self, max_value: int = 10, value: int = 0, height: int = 10, id: str | None = None) -> None:
        super().__init__(id=id)
        self.max_value = max_value
        self.value = value
        self.height = height

    def on_mount(self) -> None:
        self.update(self._render_bar())

    def set_value(self, value: int) -> None:
        self.value = max(0, min(self.max_value, value))
        self.update(self._render_bar())

    def _render_bar(self) -> str:
        filled_rows = round((self.value / self.max_value) * self.height) if self.max_value else 0
        rows: list[str] = []
        for idx in range(self.height):
            is_filled = idx >= self.height - filled_rows
            rows.append("[red]█[/red]" if is_filled else "[grey37]░[/grey37]")
        return "\n".join(rows)


class PulsingEasyButton(Button):
    def __init__(self, label: str = "EASY", id: str | None = "easy_button") -> None:
        super().__init__(label, id=id)
        self.add_class("easy-pulse")

    def pulse_once(self) -> None:
        self.remove_class("easy-flash")
        self.app.call_after_refresh(lambda: self.add_class("easy-flash"))


class CommandInput(Input):
    def __init__(self) -> None:
        super().__init__(placeholder="Type /box or ask Zero Cool...", id="command_input")


class ThoughtBox(Collapsible):
    def __init__(self, *, collapsed: bool = False, id: str | None = "thought_box") -> None:
        super().__init__(title="LLM Thought Stream", collapsed=collapsed, id=id)

    def compose(self):
        yield Markdown("_No reasoning yet._", id="thought_markdown")

    def _markdown(self) -> Markdown:
        return self.query_one("#thought_markdown", Markdown)

    async def stream_thought(self, thought: str) -> None:
        content = thought.strip() or "(No <thought> output)"
        # Model outputs sometimes include literal "\\n" sequences; normalize them
        # so Markdown can wrap/display lines naturally.
        content = content.replace("\\n", "\n")
        buffer: list[str] = []
        for line in content.splitlines() or [content]:
            buffer.append(line)
            self._markdown().update("\n".join(buffer))
            await asyncio.sleep(0.01)


class SidebarStatus(Vertical):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(id="intel_sidebar", **kwargs)

    def compose(self):
        yield Static("CURRENT PHASE\n[IDLE]", id="current_phase")
        yield Static("ACTIVE TOOL\nIdle", id="active_tool")
        yield Static("PATHETIC METER", id="pathetic_meter")
        yield VerticalProgressBar(max_value=10, value=0, height=10, id="pathetic_meter_bar")
        yield Static("0/10", id="pathetic_meter_value")
        yield PulsingEasyButton()

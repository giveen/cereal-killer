from __future__ import annotations

import asyncio
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Footer, Header, Input, RichLog, Static

from cereal_killer.engine import LLMEngine
from cereal_killer.knowledge_base import KnowledgeBase
from cereal_killer.observer import observe_history


class WalkthroughModal(ModalScreen[None]):
    def __init__(self, content: str) -> None:
        super().__init__()
        self.content = content

    def compose(self) -> ComposeResult:
        with Vertical(id="walkthrough-modal"):
            yield Static("Full Machine Walkthrough", classes="modal-title")
            yield RichLog(highlight=True, markup=True, id="walkthrough-log")
            yield Button("Close", id="close-modal")

    def on_mount(self) -> None:
        self.query_one("#walkthrough-log", RichLog).write(self.content)

    @on(Button.Pressed, "#close-modal")
    def close_modal(self) -> None:
        self.dismiss(None)


class CerealKillerApp(App[None]):
    CSS = """
    #root { height: 1fr; }
    #sidebar { width: 33%; min-width: 28; border: solid red; padding: 1; }
    #chat-area { width: 1fr; }
    #chat-log { height: 1fr; border: solid #666666; }
    #prompt-input { margin-top: 1; }
    #easy-button {
        dock: bottom;
        color: white;
        text-style: bold;
        transition: background 600ms in_out_cubic;
    }
    #easy-button.easy-on { background: #ff0000; }
    #easy-button.easy-off { background: #770000; }
    #walkthrough-modal {
        width: 80%;
        height: 80%;
        border: thick red;
        background: $panel;
        padding: 1;
    }
    .modal-title { text-style: bold; color: red; margin-bottom: 1; }
    """

    BINDINGS = [("ctrl+c", "quit", "Quit")]

    def __init__(self, engine: LLMEngine, kb: KnowledgeBase) -> None:
        super().__init__()
        self.engine = engine
        self.kb = kb
        self.history_context: list[str] = []
        self.observer_task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="root"):
            with Vertical(id="chat-area"):
                yield RichLog(id="chat-log", markup=True, wrap=True, highlight=True)
                yield Input(placeholder="Prompt Zero Cool...", id="prompt-input")
            with Vertical(id="sidebar"):
                yield Static("[b]Sidebar[/b]\nHistory-aware context + reasoning", markup=True)
                with Collapsible(title="LLM Reasoning", id="thought-collapsible"):
                    yield Static("No reasoning yet.", id="thought-text")
                yield Button("EASY", id="easy-button")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#chat-log", RichLog).write("[green]Zero Cool online.[/green]")
        self.query_one("#easy-button", Button).add_class("easy-on")
        self.set_interval(0.7, self._pulse_easy_button)
        self.observer_task = asyncio.create_task(self._observe())

    async def on_unmount(self) -> None:
        if self.observer_task:
            self.observer_task.cancel()

    async def _observe(self) -> None:
        cwd = str(Path.cwd())
        async for commands in observe_history(cwd):
            self.history_context = commands

    def _pulse_easy_button(self) -> None:
        easy = self.query_one("#easy-button", Button)
        if easy.has_class("easy-on"):
            easy.remove_class("easy-on")
            easy.add_class("easy-off")
        else:
            easy.remove_class("easy-off")
            easy.add_class("easy-on")

    @on(Button.Pressed, "#easy-button")
    def show_walkthrough(self) -> None:
        walkthrough = self.kb.lookup_walkthrough("full machine walkthrough")
        self.push_screen(WalkthroughModal(walkthrough))

    @on(Input.Submitted, "#prompt-input")
    async def chat_prompt(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt:
            return
        event.input.value = ""
        log = self.query_one("#chat-log", RichLog)
        log.write(f"[cyan]You>[/cyan] {prompt}")

        try:
            response = await self.engine.chat(prompt, self.history_context)
        except Exception as exc:
            log.write(f"[red]LLM error:[/red] {exc}")
            return

        thought_widget = self.query_one("#thought-text", Static)
        thought_widget.update(response.thought or "(No <thought> output)")
        log.write(f"[magenta]Zero Cool>[/magenta] {response.answer}")

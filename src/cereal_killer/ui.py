from __future__ import annotations

import asyncio
import json
import re
import time as _time
from datetime import UTC, datetime
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Collapsible, Footer, Header, Input, Markdown, RichLog, Static

from cereal_killer.engine import LLMEngine
from cereal_killer.knowledge_base import KnowledgeBase
from cereal_killer.observer import observe_history_events
from mentor.engine.commands import CommandResult, dispatch as dispatch_command
from mentor.engine.methodology import audit_command as audit_methodology
from mentor.kb.query import retrieve_solution_for_machine
from mentor.ui.modals import SolutionModal
from mentor.ui.phase import detect_phase
from mentor.ui.startup import run_boot_sequence
from mentor.utils.clipboard import copy_text
from mentor.utils.screenshot import capture_screenshot

# How long the auto-coach waits between consecutive command-triggered responses.
# Explicit user prompts via the Ask input always bypass this.
_AUTO_COACH_COOLDOWN_SECS = 10


CODE_BLOCK_PATTERN = re.compile(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", re.DOTALL)


class VerticalProgressBar(Static):
    def __init__(self, max_value: int = 10, value: int = 0, height: int = 10, id: str | None = None) -> None:
        super().__init__(id=id)
        self.max_value = max_value
        self.value = value
        self.height = height

    def set_value(self, value: int) -> None:
        self.value = max(0, min(self.max_value, value))
        self.update(self._render_bar())

    def on_mount(self) -> None:
        self.update(self._render_bar())

    def _render_bar(self) -> str:
        filled_rows = round((self.value / self.max_value) * self.height) if self.max_value else 0
        rows: list[str] = []
        for idx in range(self.height):
            # Fill from bottom to top.
            is_filled = idx >= self.height - filled_rows
            rows.append("[red]█[/red]" if is_filled else "[grey37]░[/grey37]")
        return "\n".join(rows)


class CommandLink(Button):
    def __init__(self, command: str) -> None:
        super().__init__(f"Copy: {command}", classes="command-link")
        self.command = command


# ---------------------------------------------------------------------------
# Checklist widget
# ---------------------------------------------------------------------------

_CHECKLIST_ITEMS: list[tuple[str, str]] = [
    ("nmap initial scan",            r"\bnmap\b"),
    ("nmap full ports (-p-)",        r"nmap.*(-p-|65535)"),
    ("nmap service/version (-sV/-A)",r"nmap.*(-sV\b|-A\b|-sC\b)"),
    ("Web dir enumeration",          r"\b(gobuster|feroxbuster|dirbuster|ffuf|wfuzz|nikto)\b"),
    ("SMB enumeration",              r"\b(smbclient|smbmap|enum4linux|crackmapexec|netexec)\b"),
    ("Anonymous FTP",                r"\bftp\b"),
    ("VHost brute-force",            r"(gobuster|ffuf).*vhost"),
    ("NFS exports",                  r"\bshowmount\b"),
    ("SNMP enumeration",             r"\b(snmpwalk|onesixtyone)\b"),
    ("Credential brute-force",       r"\b(hydra|medusa|patator)\b"),
]


class ChecklistWidget(Static):
    """Auto-updating methodology checklist that ticks items as commands are detected."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(markup=True, **kwargs)
        self._items: list[tuple[str, re.Pattern[str], bool]] = [
            (label, re.compile(pattern, re.IGNORECASE), False)
            for label, pattern in _CHECKLIST_ITEMS
        ]

    def on_mount(self) -> None:
        self._render_checklist()

    def _render_checklist(self) -> None:
        lines = ["[b]Checklist[/b]"]
        for label, _, checked in self._items:
            mark = "[green]✓[/green]" if checked else "[grey42][ ][/grey42]"
            lines.append(f" {mark} {label}")
        self.update("\n".join(lines))

    def check_command(self, command: str) -> bool:
        """Tick matching items. Returns True if any item changed state."""
        changed = False
        updated: list[tuple[str, re.Pattern[str], bool]] = []
        for label, pattern, checked in self._items:
            if not checked and pattern.search(command):
                checked = True
                changed = True
            updated.append((label, pattern, checked))
        self._items = updated
        if changed:
            self._render_checklist()
        return changed

    def reset(self) -> None:
        """Uncheck all items (call on /box or /new-box)."""
        self._items = [(lbl, pat, False) for lbl, pat, _ in self._items]
        self._render_checklist()


class MainDashboard(App[None]):
    EASY_BUTTON_PULSE_SECONDS = 0.7

    CSS = """
    Screen {
        background: #0a0f14;
        color: #d7fff6;
    }
    Header {
        background: #07161d;
        color: #9af7df;
        text-style: bold;
    }
    Footer {
        background: #07161d;
        color: #8ae7cf;
    }
    #dashboard {
        height: 1fr;
        padding: 1;
        background: #0a0f14;
    }
    #left_column {
        width: 2fr;
        min-width: 60;
        height: 1fr;
        border: tall #00b894;
        background: #161a1a;
        padding: 1;
        margin-right: 1;
    }
    #intel_sidebar {
        width: 1fr;
        min-width: 34;
        height: 1fr;
        border: double #00d2d3;
        background: #151b1f;
        padding: 1;
    }
    #chat_log {
        height: 2fr;
        min-height: 16;
        border: tall #26c6da;
        background: #111716;
        color: #d8fff7;
        padding: 0 1;
    }
    #reasoning_box {
        height: 1fr;
        min-height: 10;
        border: double #1dd1a1;
        background: #081825;
        margin-top: 1;
    }
    #reasoning_markdown {
        height: 12;
        background: #081825;
        color: #9fe8ff;
        padding: 0 1;
    }
    .command-link {
        width: 100%;
        margin-top: 1;
    }
    #user_prompt {
        margin-top: 1;
        border: tall #00d2d3;
        background: #0d1318;
        color: #e9fff9;
    }
    #user_prompt.input-highlight {
        border: tall #f1c40f;
    }
    #current_phase {
        border: tall #2ecc71;
        background: #111716;
        padding: 1;
        margin-bottom: 1;
        text-style: bold;
        color: #9af7df;
    }
    #current_phase.phase-idle { border: tall #576574; color: #a5b1c2; }
    #current_phase.phase-recon { border: tall #26c6da; color: #7ed6df; }
    #current_phase.phase-enumeration { border: tall #feca57; color: #ffeaa7; }
    #current_phase.phase-exploitation { border: tall #ff6b6b; color: #ff9f9f; }
    #current_phase.phase-post { border: tall #2ed573; color: #b8ffce; }
    #web-search-indicator {
        padding: 0 1;
        margin-bottom: 1;
        text-style: bold;
    }
    #web-search-indicator.web-idle { color: #444444; }
    #web-search-indicator.web-active { color: #00aaff; }
    #terminal_feed {
        height: 10;
        min-height: 8;
        border: tall #26c6da;
        background: #111716;
        margin-bottom: 1;
        padding: 0 1;
    }
    #pathetic_meter {
        border: tall #aa2e25;
        background: #161010;
        color: #ff8c7a;
        padding: 1;
        margin-bottom: 1;
    }
    #pathetic-meter-bar {
        width: 3;
        height: 10;
        margin-top: 1;
        margin-bottom: 1;
    }
    #pathetic-meter-value {
        color: #ffb4a2;
        text-style: bold;
        margin-bottom: 1;
    }
    #easy_button {
        dock: bottom;
        min-height: 3;
        color: white;
        text-style: bold;
        border: tall #ff3b30;
        background: #770000;
        transition: background 600ms in_out_cubic;
    }
    #easy_button.easy-on { background: #ff1e1e; }
    #easy_button.easy-off { background: #770000; }
    .hidden {
        display: none;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+y", "copy_last_code_block", "Copy last code block"),
        ("ctrl+t", "toggle_thinking", "Toggle Thinking"),
        ("ctrl+s", "vision_capture", "Vision"),
        ("ctrl+b", "pulse_easy_button", "Easy Button"),
    ]

    def __init__(self, engine: LLMEngine, kb: KnowledgeBase) -> None:
        super().__init__()
        self.engine = engine
        self.kb = kb
        self.title = "CEREAL KILLER"
        self.sub_title = "TARGET: NONE"
        self.history_context: list[str] = []
        self.observer_task: asyncio.Task[None] | None = None
        self.last_code_block = ""
        self.pathetic_meter = 0
        self.easy_usage_count = 0
        self.successful_command_count = 0
        self.chat_transcript: list[dict[str, str]] = []
        self.current_target: str = ""
        # Context pruning flag — set while a summarisation LLM call is in-flight
        # so we don't start a second one concurrently.
        self._pruning_in_flight = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="dashboard"):
            with Vertical(id="left_column"):
                yield RichLog(id="chat_log", markup=True, wrap=True, highlight=True)
                with Collapsible(title="LLM Reasoning", id="reasoning_box", collapsed=False):
                    yield Markdown("_No reasoning yet._", id="reasoning_markdown")
                yield Input(placeholder="Prompt Zero Cool...", id="user_prompt")
            with Vertical(id="intel_sidebar"):
                yield Static("CURRENT PHASE\n[IDLE]", id="current_phase")
                yield Static("○ Web", id="web-search-indicator", markup=False)
                yield RichLog(id="terminal_feed", markup=True, wrap=True, highlight=False, max_lines=10)
                yield Static("PATHETIC METER", id="pathetic_meter")
                yield VerticalProgressBar(max_value=10, value=0, height=10, id="pathetic-meter-bar")
                yield Static("0/10", id="pathetic-meter-value")
                yield Button("EASY", id="easy_button")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#easy_button", Button).add_class("easy-on")
        self.query_one("#web-search-indicator", Static).add_class("web-idle")
        self._update_phase_display("[IDLE]")
        self._apply_responsive_layout(self.size.width)
        self.set_interval(self.EASY_BUTTON_PULSE_SECONDS, self._pulse_easy_button)
        self.set_interval(300, self._schedule_persist_mental_state)
        self.set_interval(60, self._schedule_context_prune)
        self.observer_task = asyncio.create_task(self._observe())
        asyncio.create_task(self._run_boot_sequence())
        # Register the web-search state callback so Brain can light up the indicator.
        self.engine.set_web_search_callback(self._on_web_search_state)

    def on_resize(self, event) -> None:
        self._apply_responsive_layout(event.size.width)

    async def on_unmount(self) -> None:
        if self.observer_task:
            self.observer_task.cancel()
        await self.engine.persist_mental_state(self.history_context)
        self._save_session_snapshot("app-close")

    def _schedule_persist_mental_state(self) -> None:
        asyncio.create_task(self.engine.persist_mental_state(self.history_context))

    def _schedule_context_prune(self) -> None:
        if not self._pruning_in_flight:
            asyncio.create_task(self._maybe_prune_transcript())

    async def _run_boot_sequence(self) -> None:
        log = self.query_one("#chat_log", RichLog)
        async for result in run_boot_sequence(self.engine._brain.settings):
            log.write(result.message)
            await asyncio.sleep(0)
        # After boot, fire the returning greeting.
        greeting = await self.engine.returning_greeting()
        if greeting:
            log.write(f"[magenta]Zero Cool>[/magenta] {greeting}")
            self._append_chat("assistant", greeting)

    async def _maybe_prune_transcript(self) -> None:
        """Summarise and prune the oldest entries if the transcript is too large."""
        if self._pruning_in_flight:
            return
        total_chars = sum(len(e.get("text", "")) for e in self.chat_transcript)
        threshold = self.engine.prune_threshold()
        if total_chars <= threshold:
            return
        self._pruning_in_flight = True
        log = self.query_one("#chat_log", RichLog)
        try:
            target = self.engine.prune_target()
            # Calculate how many chars to drop from the front.
            chars_to_drop = total_chars - target
            entries_to_summarize: list[dict[str, str]] = []
            dropped = 0
            for entry in self.chat_transcript:
                if dropped >= chars_to_drop:
                    break
                entries_to_summarize.append(entry)
                dropped += len(entry.get("text", ""))
            if not entries_to_summarize:
                return
            blob = "\n".join(
                f"{e.get('role', 'unknown')}: {e.get('text', '')}" for e in entries_to_summarize
            )
            summary = await self.engine.summarize_session(blob)
            summary_entry = {
                "role": "summary",
                "text": summary,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            remaining = self.chat_transcript[len(entries_to_summarize):]
            self.chat_transcript = [summary_entry, *remaining]
            log.write(
                f"[dim cyan][Context pruned: {len(entries_to_summarize)} entries → summary][/dim cyan]"
            )
        except Exception as exc:
            log.write(f"[yellow]Context prune error:[/yellow] {exc}")
        finally:
            self._pruning_in_flight = False

    async def _observe(self) -> None:
        cwd = str(Path.cwd())
        feed = self.query_one("#terminal_feed", RichLog)
        chat_log = self.query_one("#chat_log", RichLog)
        # Spam guard: auto-coach fires at most once per _AUTO_COACH_COOLDOWN_SECS.
        # Explicit user prompts (Input.Submitted) are not affected.
        _last_auto_coach_time: float = 0.0

        async for event in observe_history_events(cwd):
            if event.json_hint:
                self._append_chat("assistant", event.json_hint)
                chat_log.write(f"[magenta]Zero Cool>[/magenta] {event.json_hint}")
                continue

            if event.feedback_signal and not event.command:
                if event.feedback_signal == "failure":
                    try:
                        diagnosis = await self.engine.diagnose_failure(
                            event.feedback_line or "terminal output",
                            self.history_context,
                            pathetic_meter=self.pathetic_meter,
                        )
                    except Exception as exc:
                        chat_log.write(f"[red]Failure analysis error:[/red] {exc}")
                    else:
                        await self._stream_thought(diagnosis.reasoning_content or diagnosis.thought)
                        await self._refresh_command_links(diagnosis.answer)
                        self._append_chat("assistant", diagnosis.answer)
                        chat_log.write(f"[magenta]Zero Cool>[/magenta] {diagnosis.answer}")
                    continue
                if event.feedback_signal == "success":
                    msg = "Cute. Looks like something actually worked for once."
                    self._append_chat("assistant", msg)
                    chat_log.write(f"[green]Zero Cool>[/green] {msg}")
                    self._record_successful_command()
                    self._save_session_snapshot("pwned-signal")
                    continue

            if not event.command:
                continue

            feed.write(f"[yellow]>[/yellow] {event.command}")
            self.history_context = event.context_commands
            phase = detect_phase(self.history_context)
            self._update_phase_display(phase)

            # Pedagogy: notify state machine of phase change and command progress.
            self.engine.record_phase_change(phase)
            self.engine.record_command_progress()

            # Checklist: auto-tick items matching this command.
            try:
                self.query_one(ChecklistWidget).check_command(event.command)
            except NoMatches:
                pass

            # Methodology audit: warn if user jumps straight to exploitation.
            audit_warning = audit_methodology(event.command, self.history_context)
            if audit_warning:
                chat_log.write(f"[bold red]Zero Cool Warning>[/bold red] {audit_warning}")
                self._append_chat("assistant", audit_warning)

            # Auto-detect cd into an HTB box directory.
            if event.cd_target and event.cd_target != self.current_target:
                auto_cmd = f"/box {event.cd_target}"
                cmd_result = await dispatch_command(auto_cmd, self.engine, self.kb.settings)
                if cmd_result is not None:
                    await self._apply_command_result(cmd_result, chat_log)
                    chat_log.write(
                        f"[cyan]Zero Cool>[/cyan] I see we've moved to the "
                        f"[b]{event.cd_target.upper()}[/b] directory. "
                        "I've loaded the notes. Try not to embarrass us."
                    )

            if event.feedback_signal == "failure":
                try:
                    diagnosis = await self.engine.diagnose_failure(
                        event.feedback_line or event.command,
                        self.history_context,
                        pathetic_meter=self.pathetic_meter,
                    )
                except Exception as exc:
                    chat_log.write(f"[red]Failure analysis error:[/red] {exc}")
                else:
                    await self._stream_thought(diagnosis.reasoning_content or diagnosis.thought)
                    await self._refresh_command_links(diagnosis.answer)
                    self._append_chat("assistant", diagnosis.answer)
                    chat_log.write(f"[magenta]Zero Cool>[/magenta] {diagnosis.answer}")
                continue

            if event.feedback_signal == "success":
                msg = "Cute. Looks like something actually worked for once."
                self._append_chat("assistant", msg)
                chat_log.write(f"[green]Zero Cool>[/green] {msg}")
                self._record_successful_command()
                self._save_session_snapshot("pwned-signal")
                continue

            if not event.trigger_brain:
                continue

            # Spam guard: skip auto-coach if it fired too recently.
            now = _time.monotonic()
            if now - _last_auto_coach_time < _AUTO_COACH_COOLDOWN_SECS:
                feed.write(
                    f"[dim]Auto-coach throttled (cooldown {_AUTO_COACH_COOLDOWN_SECS}s)[/dim]"
                )
                continue
            _last_auto_coach_time = now

            try:
                response = await self.engine.react_to_command(
                    event.command,
                    self.history_context,
                    pathetic_meter=self.pathetic_meter,
                )
            except Exception as exc:
                chat_log.write(f"[red]Auto-coach error:[/red] {exc}")
                continue

            await self._stream_thought(response.reasoning_content or response.thought)
            await self._refresh_command_links(response.answer)
            self._append_chat("assistant", response.answer)
            self._track_code_block(response.answer)
            chat_log.write(f"[blue]Auto Coach>[/blue] {response.answer}")

    def _pulse_easy_button(self) -> None:
        easy = self.query_one("#easy_button", Button)
        if easy.has_class("easy-on"):
            easy.remove_class("easy-on")
            easy.add_class("easy-off")
        else:
            easy.remove_class("easy-off")
            easy.add_class("easy-on")

    @on(Button.Pressed, "#pwned-button")
    async def handle_pwned(self) -> None:
        log = self.query_one("#chat_log", RichLog)
        machine_name = Path.cwd().name
        log.write(f"[bold green]Generating loot report for {machine_name}...[/bold green]")
        try:
            response = await self.engine.generate_loot_report(
                history_commands=self.history_context,
                pathetic_meter=self.pathetic_meter,
            )
        except Exception as exc:
            log.write(f"[red]Loot report error:[/red] {exc}")
            return

        await self._stream_thought(response.reasoning_content or response.thought)
        self._append_chat("assistant", response.answer)
        log.write(f"[bold green]--- LOOT REPORT ---[/bold green]")
        log.write(response.answer)

        # Persist loot report to disk.
        report_dir = Path("data/loot")
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        report_path = report_dir / f"{machine_name}-loot-{timestamp}.md"
        report_path.write_text(response.answer, encoding="utf-8")
        log.write(f"[dim]Loot report saved to {report_path}[/dim]")

        # Clear Redis session cache so the next box starts fresh.
        try:
            await self.engine.clear_session(machine_name)
            log.write(f"[dim]Redis session cleared for {machine_name}.[/dim]")
        except Exception as exc:
            log.write(f"[yellow]Session clear error:[/yellow] {exc}")

        self._record_successful_command()
        self._save_session_snapshot("pwned-button")

    @on(Button.Pressed, "#easy_button")
    def show_walkthrough(self) -> None:
        self._record_easy_usage()
        machine_name = Path.cwd().name
        solution_markdown = retrieve_solution_for_machine(self.kb.settings, machine_name)
        self.push_screen(SolutionModal(solution_markdown))

    @on(Button.Pressed, ".command-link")
    def copy_command_link(self, event: Button.Pressed) -> None:
        button = event.button
        command = getattr(button, "command", "")
        if not command:
            return
        copy_text(command, fallback=self.copy_to_clipboard)

        chat_log = self.query_one("#chat_log", RichLog)
        chat_log.write(f"[green]Copied command:[/green] {command}")
        self._highlight_prompt_input()

    @on(Button.Pressed, "#copy-code-button")
    def copy_last_code_button(self) -> None:
        self.action_copy_last_code_block()

    @on(Button.Pressed, "#vision-button")
    async def analyze_screenshot(self) -> None:
        log = self.query_one("#chat_log", RichLog)
        try:
            image_path = capture_screenshot()
        except Exception as exc:
            log.write(f"[red]Screenshot capture failed:[/red] {exc}")
            return

        prompt = "Zero Cool, look at this. Describe the vulnerability or anomaly visible in this response."
        self._append_chat("user", f"Vision capture: {image_path}")
        log.write(f"[cyan]You>[/cyan] Vision capture: {image_path}")
        try:
            response = await self.engine.chat_with_image(
                prompt,
                image_path=image_path,
                history_commands=self.history_context,
                pathetic_meter=self.pathetic_meter,
            )
        except Exception as exc:
            log.write(f"[red]Multimodal LLM error:[/red] {exc}")
            return

        await self._stream_thought(response.reasoning_content or response.thought)
        await self._refresh_command_links(response.answer)
        self._append_chat("assistant", response.answer)
        self._track_code_block(response.answer)
        log.write(f"[magenta]Zero Cool>[/magenta] {response.answer}")

    def action_toggle_thinking(self) -> None:
        collapsible = self.query_one("#reasoning_box", Collapsible)
        collapsible.collapsed = not collapsible.collapsed

    async def action_vision_capture(self) -> None:
        await self.analyze_screenshot()

    def action_pulse_easy_button(self) -> None:
        easy = self.query_one("#easy_button", Button)
        # Single manual pulse for drama.
        easy.remove_class("easy-on")
        easy.add_class("easy-off")
        self.set_timer(0.2, lambda: (easy.remove_class("easy-off"), easy.add_class("easy-on")))

    def action_copy_last_code_block(self) -> None:
        log = self.query_one("#chat_log", RichLog)
        if not self.last_code_block:
            log.write("[yellow]No code block captured yet.[/yellow]")
            return
        copy_text(self.last_code_block, fallback=self.copy_to_clipboard)
        log.write("[green]Copied last code block to clipboard.[/green]")
        monitor_message = self._clipboard_monitor_feedback(self.last_code_block)
        if monitor_message:
            self._append_chat("assistant", monitor_message)
            log.write(f"[yellow]Zero Cool>[/yellow] {monitor_message}")

    @on(Input.Submitted, "#user_prompt")
    async def chat_prompt(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt:
            return
        event.input.value = ""
        log = self.query_one("#chat_log", RichLog)
        log.write(f"[cyan]You>[/cyan] {prompt}")
        self._append_chat("user", prompt)

        # --- Slash-command intercept ---
        cmd_result = await dispatch_command(prompt, self.engine, self.kb.settings)
        if cmd_result is not None:
            await self._apply_command_result(cmd_result, log)
            # /loot is a convenience alias — delegate to the Pwned handler.
            if cmd_result.session_prefix == "__loot__":
                await self.handle_pwned()
            return

        if self._is_direct_hint_request(prompt):
            self._record_easy_usage(weight=2)

        try:
            response = await self.engine.chat(
                prompt,
                self.history_context,
                pathetic_meter=self.pathetic_meter,
            )
        except Exception as exc:
            log.write(f"[red]LLM error:[/red] {exc}")
            return

        await self._stream_thought(response.reasoning_content or response.thought)
        await self._refresh_command_links(response.answer)
        phase = detect_phase(self.history_context)
        self._update_phase_display(phase)
        self._track_code_block(response.answer)
        self._append_chat("assistant", response.answer)
        log.write(f"[magenta]Zero Cool>[/magenta] {response.answer}")
        if "pwned" in prompt.lower() or "owned" in prompt.lower():
            self._save_session_snapshot("pwned-manual")

    def _on_web_search_state(self, active: bool) -> None:
        """Called by Brain when SearXNG last-resort search starts/stops."""
        indicator = self.query_one("#web-search-indicator", Static)
        if active:
            indicator.update("\u25cf Web \u2014 searching...")
            indicator.remove_class("web-idle")
            indicator.add_class("web-active")
        else:
            indicator.update("\u25cb Web")
            indicator.remove_class("web-active")
            indicator.add_class("web-idle")

    def _track_code_block(self, response_text: str) -> None:
        matches = CODE_BLOCK_PATTERN.findall(response_text)
        if matches:
            self.last_code_block = matches[-1].strip()

    async def _apply_command_result(self, result: CommandResult, log: RichLog) -> None:
        """Apply a CommandResult: update state, header, phase display, and show notifications."""
        log.write(result.message)
        self._append_chat("assistant", result.message)

        if result.system_prompt_addendum is not None:
            self.engine.set_system_prompt_addendum(result.system_prompt_addendum)

        if result.new_target:
            self.current_target = result.new_target
            self._update_header_target(result.new_target)
            mode = "Exploration" if result.exploration_mode else "Known Box"
            self.notify(
                f"Context switched → {result.new_target.upper()} [{mode}]",
                title="Target Loaded",
                severity="information",
            )
            # Reset checklist and pedagogy timer for the new box.
            try:
                self.query_one(ChecklistWidget).reset()
            except NoMatches:
                pass
            self.engine.record_phase_change("[IDLE]")

        if result.reset_phase:
            self._update_phase_display("[IDLE]")

    def _adjust_pathetic_meter(self) -> None:
        total = self.easy_usage_count + self.successful_command_count
        if total <= 0:
            self.pathetic_meter = 0
        else:
            ratio = self.easy_usage_count / total
            self.pathetic_meter = max(0, min(10, round(ratio * 10)))
        self.query_one("#pathetic-meter-bar", VerticalProgressBar).set_value(self.pathetic_meter)
        self.query_one("#pathetic-meter-value", Static).update(f"{self.pathetic_meter}/10")

    def _record_easy_usage(self, weight: int = 1) -> None:
        self.easy_usage_count += max(1, weight)
        self._adjust_pathetic_meter()

    def _record_successful_command(self) -> None:
        self.successful_command_count += 1
        self._adjust_pathetic_meter()

    def _update_header_target(self, target: str | None = None) -> None:
        active_target = (target or self.current_target or "NONE").upper()
        self.title = "CEREAL KILLER"
        self.sub_title = f"TARGET: {active_target}"

    def _update_phase_display(self, phase: str) -> None:
        phase_widget = self.query_one("#current_phase", Static)
        for phase_class in (
            "phase-idle",
            "phase-recon",
            "phase-enumeration",
            "phase-exploitation",
            "phase-post",
        ):
            phase_widget.remove_class(phase_class)
        phase_widget.update(f"CURRENT PHASE\n{phase}")
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

    def _apply_responsive_layout(self, width: int) -> None:
        sidebar = self.query_one("#intel_sidebar", Vertical)
        if width < 120:
            sidebar.add_class("hidden")
        else:
            sidebar.remove_class("hidden")

    async def _stream_thought(self, thought: str) -> None:
        thought_log = self.query_one("#reasoning_markdown", Markdown)
        content = thought.strip() or "(No <thought> output)"
        buffer: list[str] = []
        for line in content.splitlines() or [content]:
            buffer.append(line)
            thought_log.update(f"```text\n{'\n'.join(buffer)}\n```")
            await asyncio.sleep(0.01)

    async def _refresh_command_links(self, answer: str) -> None:
        commands = self._extract_suggested_commands(answer)
        try:
            container = self.query_one("#command_links", Vertical)
        except NoMatches:
            return
        for child in list(container.children):
            if isinstance(child, CommandLink):
                child.remove()
        for command in commands[:5]:
            await container.mount(CommandLink(command))

    def _append_chat(self, role: str, text: str) -> None:
        self.chat_transcript.append(
            {
                "role": role,
                "text": text,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

    def _save_session_snapshot(self, reason: str) -> None:
        session_dir = Path("data/sessions")
        session_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        payload = {
            "reason": reason,
            "timestamp": datetime.now(UTC).isoformat(),
            "cwd": str(Path.cwd()),
            "phase": detect_phase(self.history_context),
            "pathetic_meter": self.pathetic_meter,
            "history_context": self.history_context,
            "last_code_block": self.last_code_block,
            "chat": self.chat_transcript,
        }
        target = session_dir / f"zero-cool-session-{timestamp}.json"
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _highlight_prompt_input(self) -> None:
        prompt = self.query_one("#user_prompt", Input)
        prompt.add_class("input-highlight")

        def remove_highlight() -> None:
            prompt.remove_class("input-highlight")

        self.set_timer(0.5, remove_highlight)

    @staticmethod
    def _extract_suggested_commands(answer: str) -> list[str]:
        blocks = CODE_BLOCK_PATTERN.findall(answer)
        suggestions: list[str] = []
        for block in blocks:
            for line in block.splitlines():
                cmd = line.strip()
                if not cmd or cmd.startswith("#"):
                    continue
                suggestions.append(cmd)
        return suggestions

    @staticmethod
    def _clipboard_monitor_feedback(command: str) -> str:
        lowered = command.lower()
        if "rm -rf" in lowered:
            return "Good luck. That command has no undo button, genius."
        if "nmap" in lowered and "-p-" in lowered and "-t5" in lowered:
            return "Good luck melting your target with -T5 and -p-. Consider throttling first."
        if "sqlmap" in lowered and "--risk=3" in lowered:
            return "Bold choice with sqlmap --risk=3. Double-check scope before you go loud."
        if command.strip():
            return "Good luck. Read the flags before you paste and pray."
        return ""

    @staticmethod
    def _is_direct_hint_request(prompt: str) -> bool:
        lowered = prompt.lower()
        hint_markers = (
            "just tell me",
            "give me the answer",
            "direct hint",
            "what is the flag",
            "spoiler",
            "step by step exploit",
        )
        return any(marker in lowered for marker in hint_markers)


class CerealKillerApp(MainDashboard):
    """Backward-compatible app name."""

# CerealKillerApp Architecture

## Overview

`CerealKillerApp` is the central application class for the Cereal Killer terminal UI, built on the [Textual](https://textual.textualize.io/) framework. It serves as the main orchestrator, coordinating the LLM engine, knowledge base, context manager, clipboard watcher, observer, and the UI dashboard. The app implements a "Zero Cool" persona theme and manages the full lifecycle from boot sequence through active session to graceful shutdown.

## Table of Contents

- [Dependencies & Imports](#dependencies--imports)
- [Class Definition & Initialization](#class-definition--initialization)
  - [State Variables](#state-variables)
- [Lifecycle Methods](#lifecycle-methods)
  - [on_mount](#on_mount)
  - [on_unmount](#on_unmount)
  - [on_resize](#on_resize)
- [Worker Methods](#worker-methods)
  - [_run_chat_worker](#_run_chat_worker)
  - [_run_loot_worker](#_run_loot_worker)
  - [_run_vision_worker](#_run_vision_worker)
  - [_run_document_ingest_worker](#_run_document_ingest_worker)
  - [_run_autocoach_worker](#_run_autocoach_worker)
  - [_run_search_worker](#_run_search_worker)
  - [_run_gibson_search_worker](#_run_gibson_search_worker)
  - [_run_gibson_synthesize_worker](#_run_gibson_synthesize_worker)
  - [_run_remote_visual_worker](#_run_remote_visual_worker)
- [Event Handlers](#event-handlers)
- [Command Handling](#command-handling)
- [Context Management](#context-management)
- [Supporting Methods](#supporting-methods)
- [Key Features](#key-features)

---

## Dependencies & Imports

The app depends on:

| Module | Purpose |
|---|---|
| `textual.app.App` | Base framework class |
| `textual.work` / `textual.on` | Worker decorator and event system |
| `cereal_killer.context_manager.ContextManager` | Context window management |
| `cereal_killer.engine.LLMEngine` | LLM interaction (chat, vision, summarization) |
| `cereal_killer.knowledge_base.KnowledgeBase` | Local knowledge storage |
| `cereal_killer.observer.*` | Terminal observer & clipboard watcher |
| `mentor.engine.commands` | Slash command dispatch |
| `mentor.engine.methodology` | Methodology audit |
| `mentor.engine.search_orchestrator` | Tiered RAG search |
| `mentor.kb.query` | Knowledge base retrieval |
| `mentor.ui.phase` | Phase detection logic |
| `mentor.ui.startup` | Boot sequence |

---

## Class Definition & Initialization

```python
class CerealKillerApp(App[None]):
    def __init__(
        self,
        engine: LLMEngine,
        kb: KnowledgeBase,
        preflight_hard_fail: bool = False,
        preflight_reason: str = "",
    ) -> None:
```

The constructor accepts an `LLMEngine`, `KnowledgeBase`, and optional preflight results. It sets up all internal state but defers most initialization to `on_mount()`.

### State Variables

| Variable | Type | Purpose |
|---|---|---|
| `engine` | `LLMEngine` | Primary LLM interaction engine |
| `kb` | `KnowledgeBase` | Local knowledge base instance |
| `title` | `str` | Window title (always "CEREAL KILLER") |
| `sub_title` | `str` | Dynamic subtitle showing current target |
| `history_context` | `list[str]` | Shell command history for context |
| `observer_task` | `asyncio.Task \| None` | Background observer task handle |
| `clipboard_task` | `asyncio.Task \| None` | Background clipboard watcher task handle |
| `clipboard_watcher` | `ClipboardImageWatcher` | Watches clipboard for images |
| `last_code_block` | `str` | Most recently extracted code block |
| `pathetic_meter` | `int` | Ratio tracking easy-button usage vs. independent actions |
| `easy_usage_count` | `int` | Count of easy-button uses |
| `successful_command_count` | `int` | Count of commands that led to successful outcomes |
| `chat_transcript` | `list[dict[str, str]]` | Full chat history with roles and timestamps |
| `_context_manager` | `ContextManager` | Manages context window pruning and summarization |
| `current_target` | `str` | Active target machine name |
| `_pruning_in_flight` | `bool` | Guards against concurrent pruning operations |
| `_analysis_jobs` | `int` | Counter for in-flight analysis tasks |
| `_uploaded_image_path` | `Path \| None` | Currently loaded image path |
| `_gibson_snippets` | `list[dict]` | Current Gibson search result snippets |
| `_vision_analyzed_sources` | `set[str]` | Resolved paths of already-analyzed images (dedup) |
| `_preflight_hard_fail` | `bool` | Hard preflight failure flag |
| `_preflight_reason` | `str` | Human-readable preflight failure reason |

---

## Lifecycle Methods

### on_mount

Initializes the entire application when it first loads. Key responsibilities:

1. **Pushes `MainDashboard`** as the active screen
2. **Configures dashboard defaults** — active view, phase, upload root, loading state
3. **Starts background intervals:**
   | Interval | Period | Purpose |
   |---|---|---|
   | Easy button pulse | 0.7s | Visual pulse on the easy button widget |
   | Persist mental state | 300s (5 min) | Save `history_context` to engine |
   | Context prune | 60s | Check if context exceeds budget |
   | Sync status refresh | 60s | Refresh knowledge base sync indicators |
   | GitHub API status | 15s | Refresh API rate limit display |
4. **Launches async tasks:**
   - `_observe()` — Terminal command observer
   - `_watch_clipboard()` — Clipboard image watcher
   - `_run_boot_sequence()` — Initial boot messages
   - `_run_system_readiness_check()` — Validates system health
5. **Triggers preflight failure modal** if `preflight_hard_fail` is `True`
6. **Wires web search callback** if the engine supports it
7. **Updates context token counter** for initial state

### on_unmount

Gracefully shuts down when the application exits:

1. **Cancels** observer and clipboard watcher async tasks
2. **Persists mental state** by calling `engine.persist_mental_state()` if supported
3. **Saves a session snapshot** with reason `"app-close"` to `data/sessions/`

### on_resize

Handles terminal resize events by forwarding the event width to `MainDashboard.apply_responsive_layout()`. Silently catches errors to tolerate resize events occurring while modals are active.

---

## Worker Methods

All worker methods are decorated with `@work()` to run in the Textual worker pool. They follow a common pattern: set busy state → set active tool → perform work → handle errors → reset state.

### _run_chat_worker

```python
@work(exclusive=True, thread=False, group="llm")
async def _run_chat_worker(self, prompt: str) -> None:
```

The primary chat interaction flow:

1. Sets analysis busy flag and active tool to "Brain"
2. Calls `engine.chat(prompt, history_context, pathetic_meter)`
3. Updates LLM cache metrics from response metadata
4. Consumes the LLM response via `_consume_llm_response()`
5. Detects current phase via `detect_phase(history_context)` and updates the dashboard
6. Saves a session snapshot with reason `"pwned-manual"` if the prompt contains "pwned" or "owned"

### _run_loot_worker

```python
@work(exclusive=True, thread=False, group="llm")
async def _run_loot_worker(self) -> None:
```

Generates a loot report from the shell history:

1. Extracts `machine_name` from `Path.cwd().name`
2. Calls `engine.generate_loot_report(history_context, pathetic_meter)`
3. Consumes the response via `_consume_llm_response()`

### _run_vision_worker

```python
@work(exclusive=True, thread=False, group="llm")
async def _run_vision_worker(
    self,
    image_path: str,
    source_label: str = "Clipboard",
    mark_context: bool = False,
) -> None:
```

Analyzes images using multimodal LLM capabilities:

1. Validates that the image file exists at `image_path`
2. Sets busy state and active tool to "Vision"
3. Optionally sets upload progress and context chip if `mark_context=True`
4. Displays status messages: "Image Uploaded" and "Zero Cool is analyzing..."
5. Calls `engine.chat_with_image(VISION_PROMPT, image_path, history_context, pathetic_meter)`
6. Adds the resolved image path to `_vision_analyzed_sources` for deduplication
7. Updates upload progress to 100% on success

`VISION_PROMPT` defaults to: *"Zero Cool, I've just pasted a screenshot. Look at the error/output and tell me where I'm failing."*

### _run_document_ingest_worker

```python
@work(exclusive=True, thread=False, group="llm")
async def _run_document_ingest_worker(self, file_path: str) -> None:
```

Ingests text, JSON, or log documents:

1. Validates the file is a supported document type via `is_document_path()`
2. Sets busy state and active tool to "Document Ingest"
3. Builds document payload via `build_document_prompt(path)`
4. Calls `engine.chat(payload, history_context, pathetic_meter)`
5. Handles `json.JSONDecodeError` separately with a user-friendly error message
6. Updates upload progress through stages (20% → 55% → 100%)

### _run_autocoach_worker

```python
@work(exclusive=True, thread=False, group="coach")
async def _run_autocoach_worker(self, command: str) -> None:
```

Provides reactive coaching based on shell commands:

1. Sets busy state and active tool to "Brain"
2. Calls `engine.react_to_command(command, history_context, pathetic_meter)`
3. Consumes the response via `_consume_llm_response()`

This worker runs with `group="coach"`, allowing it to execute in parallel with other non-coach workers.

### _run_search_worker

```python
@work(exclusive=True, thread=False, group="llm")
async def _run_search_worker(self, query: str) -> None:
```

Implements tiered RAG (Retrieval-Augmented Generation) search:

1. Extracts source filters from the query using `_extract_source_filters()`
2. Calls `tiered_search()` with the query and source filters
3. Displays similarity scores and match count to the user
4. If matches found:
   - Calls `engine.synthesize_search_results(query, chunks)`
   - Displays results with "[SEARCH RESULT]" prefix
   - Shows summary in Gibson tab
   - Populates `_gibson_snippets` and sets remote image candidate
5. If no matches found: displays fallback message and checks external datasets
6. Switches to the "Gibson" view after search completes

### _run_gibson_search_worker

```python
@work(exclusive=True, thread=False, group="search")
async def _run_gibson_search_worker(self, query: str) -> None:
```

Direct search from the Gibson tab with grouped results and auto summary:

1. Enables Gibson loading indicator and sets active tool to "Gibson"
2. Calls `tiered_search()` with `top_k=15`
3. Populates `_gibson_snippets` from search results
4. Extracts visual image candidates from snippets
5. Synthesizes a summary using `engine.synthesize_search_results()`
6. Shows the summary in the Gibson viewer

This worker uses `group="search"` (non-exclusive), allowing it to run concurrently with LLM workers.

### _run_gibson_synthesize_worker

```python
@work(exclusive=True, thread=False, group="llm")
async def _run_gibson_synthesize_worker(self) -> None:
```

Synthesizes all current Gibson snippets into a master cheat sheet:

1. Extracts the search query (or defaults to "summarize")
2. Converts `_gibson_snippets` to `RAGSnippet` objects
3. Calls `engine.synthesize_search_results()` with all snippets
4. Displays the resulting "MASTER CHEAT SHEET" in the Gibson viewer

### _run_remote_visual_worker

```python
@work(exclusive=True, thread=False, group="media")
async def _run_remote_visual_worker(self, url: str) -> None:
```

Downloads and displays remote images (diagrams, screenshots):

1. Sets active tool to "Media"
2. Downloads the image via `httpx.AsyncClient` (20-second timeout)
3. Validates Pillow is available
4. Opens and converts the image to RGB
5. Saves to `data/temp/remote_visual_buffer.png`
6. Sets the visual buffer image in the dashboard with source "Remote"
7. Displays notification upon success or failure

---

## Event Handlers

The app handles various Textual events to drive the UI:

| Handler | Trigger | Description |
|---|---|---|
| `on_input_submitted` | `CommandInput.Submitted` | Routes commands (`/`) to `_handle_command`, regular text to `_run_chat_worker` |
| `on_upload_tree_file_selected` | `DirectoryTree.FileSelected` | Validates image selection, primes path, runs vision worker |
| `on_system_readiness_tag_pressed` | `Button.Pressed` on readiness tag | Opens setup guide in Gibson tab |
| `on_gibson_search_submitted` | `Input.Submitted` | Triggers `_run_gibson_search_worker` |
| `on_gibson_result_selected` | `OptionList.OptionSelected` | Shows snippet details in Gibson viewer |
| `on_gibson_synthesize_pressed` | `Button.Pressed` on synthesize | Triggers `_run_gibson_synthesize_worker` |
| `on_visual_view_remote_pressed` | `Button.Pressed` on remote view | Runs `_run_remote_visual_worker` |
| `on_visual_send_zero_cool_pressed` | `Button.Pressed` on send | Runs `_run_vision_worker` with dedup check |
| `on_clipboard_image_detected` | Custom `ClipboardImageDetected` | Updates visual buffer from clipboard |
| `clear_visual_buffer` | `Button.Pressed` on clear buffer | Clears clipboard buffer and visual buffer |
| `show_walkthrough` | `Button.Pressed` on easy button | Opens solution modal and IppSec link |

### Input Submission Routing

```
Input received
    │
    ├─ Starts with "/" → _handle_command() → CommandProcessor
    │
    └─ Regular text  → _run_chat_worker(prompt) → LLM chat
```

---

## Command Handling

The `_handle_command()` method dispatches slash commands and special prefixes:

### Slash Commands

| Command | Destination | Description |
|---|---|---|
| `/box <target>` | `dispatch()` | Switch target context |
| `/loot` | `__loot__` prefix | Generates loot report |
| `/vision` | `__vision__` prefix | Analyzes clipboard screenshot |
| `/upload` | `__upload__` prefix | Uploads image for vision analysis |
| `/search <query>` | `__search__` prefix | Triggers RAG search |
| `/sync-all` | `__sync_all__` prefix | Refreshes knowledge sync status |
| `/add_source <url>` | `__add_source__` prefix | Crawls a new knowledge source |
| `/exit` | `__exit__` prefix | Closes the application |

### Special Prefixes (non-slash)

| Prefix | Action |
|---|---|
| `__exit__` | Calls `self.exit()` |
| `__loot__` | Runs `_run_loot_worker()` |
| `__vision__` | Runs `_run_vision_worker()` with clipboard buffer |
| `__upload__` | Runs `_run_vision_worker()` with uploaded image |
| `__search__` | Runs `_run_search_worker()` with extracted query |
| `__sync_all__` | Refreshes sync status display |
| `__add_source__` | Notifies user and sets remote image candidate if applicable |

---

## Context Management

### _schedule_persist_mental_state

Runs every 300 seconds (via `set_interval`). Saves `history_context` to the engine via `persist_mental_state()`. Runs asynchronously via `asyncio.create_task()`.

### _schedule_context_prune

Runs every 60 seconds (via `set_interval`). Triggers `_maybe_prune_transcript()` if no pruning is already in flight (guarded by `_pruning_in_flight`).

### _maybe_prune_transcript

The core context pruning logic:

1. **Checks two conditions:**
   - `needs_budget_prune`: Total transcript character count exceeds `engine.prune_threshold()`
   - `needs_turn_condense`: `context_manager.should_condense()` determines transcript should be summarized

2. **If condensation is needed:**
   - Selects entries for summarization via `context_manager.select_entries_for_condense()`
   - Builds summary blob and calls `engine.summarize_session()`
   - Replaces selected entries with a summary entry in `chat_transcript`

3. **If budget pruning is needed:**
   - Calculates chars to drop (`total_chars - target`)
   - Identifies oldest entries to summarize
   - Calls `engine.summarize_session()` on the blob
   - Inserts a summary entry at the front of `chat_transcript`

4. **Updates token counter** and resets `_pruning_in_flight` in a `finally` block

### _append_chat

Maintains the chat transcript with timestamps:

```python
{
    "role": "user" | "assistant" | "system" | "summary",
    "text": "...",
    "timestamp": "2024-01-01T00:00:00+00:00"
}
```

After appending, it updates the token counter and triggers context prune.

### _update_context_token_counter

Estimates active context tokens using `_context_manager.estimate_active_context_tokens()` and displays the counter on the dashboard with the configured `max_model_len`.

---

## Supporting Methods

| Method | Purpose |
|---|---|
| `_dashboard()` | Returns the active `MainDashboard` instance, raising `RuntimeError` if wrong screen is active |
| `_try_dashboard()` | Safely returns `MainDashboard` or `None` from screen stack |
| `_consume_llm_response()` | Orchestrates response processing: code block tracking, repetition detection, transcript appending, UI updates |
| `_track_code_block()` | Extracts code blocks using regex `CODE_BLOCK_PATTERN` and stores the last one |
| `_warn_if_repetitive_response()` | Uses `difflib.SequenceMatcher` to detect near-duplicate responses (≥90% similarity) |
| `_analysis_busy()` | Increments/decrements `_analysis_jobs` counter; dashboard shows busy state when count > 0 |
| `_strip_rich_tags()` | Regex-based stripper of Textual rich markup tags |
| `_update_llm_cache_metrics()` | Displays LLM cache hit/latency data on dashboard |
| `_safe_stream_thought()` | Best-effort streaming of LLM thought/reasoning content to the dashboard |
| `_prime_uploaded_image()` | Copies image to buffer path, sets preview, updates dashboard |
| `_is_image_file()` | Validates file extension against `_IMAGE_SUFFIXES` set |
| `_looks_like_image_url()` | Validates URL scheme and path for image suffixes |
| `_extract_visual_candidate_url()` | Scans snippets for image URLs matching `_IMAGE_URL_RE` |
| `_open_ingest_modal()` | Pushes `IngestModal` for image or document selection |
| `_on_ingest_selection()` | Handles modal selection: routes to vision worker (images) or document ingest worker |
| `_open_ippsec_link()` | Opens IppSec YouTube video for current machine using `xdg-open` / `open` |
| `_update_header_target()` | Updates app title and subtitle to reflect current target |
| `_save_session_snapshot()` | Saves full session state to `data/sessions/zero-cool-session-{timestamp}.json` |

### Session Snapshot Payload

```json
{
    "reason": "pwned-manual | app-close | ...",
    "timestamp": "ISO-8601 timestamp",
    "cwd": "/current/working/directory",
    "phase": "[IDLE] | [RECON] | ...",
    "pathetic_meter": 3,
    "history_context": [...],
    "last_code_block": "...",
    "chat": [...]
}
```

---

## Key Features

### Auto-Coaching with Cooldown

The auto-coaching system monitors shell commands through the `_observe()` coroutine and triggers `_run_autocoach_worker()` after a configurable cooldown period (`_AUTO_COACH_COOLDOWN_SECS = 10` seconds). This prevents coach interruptions from occurring too frequently.

### Pathetic Meter

The `pathetic_meter` tracks the ratio of easy-button usage to total interactions:

```
pathetic_meter = easy_usage_count / (easy_usage_count + successful_command_count) * 10
```

Rounded to 0–10, displayed on the dashboard. A higher meter indicates reliance on walkthrough guidance.

### Session Snapshots

Sessions are automatically saved to `data/sessions/` at:

- App close (`reason: "app-close"`)
- Manual pwnage detection (`reason: "pwned-manual"`)
- Any other explicit trigger via `_save_session_snapshot()`

Snapshots are timestamped JSON files containing full chat history, phase state, pathetic meter, and last code block.

### Repetition Detection

The `_warn_if_repetitive_response()` method uses Python's `difflib.SequenceMatcher` to compare consecutive assistant responses. If the similarity ratio exceeds 0.90, a warning notification is shown:

> "[System] Zero Cool is repeating himself. Try providing more specific tool output."

### Code Block Extraction

The `CODE_BLOCK_PATTERN` regex extracts code blocks from LLM responses:

```python
r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```"
```

The last matched code block is stored in `last_code_block` for later retrieval. This supports downstream tools that may want to execute or display extracted code.

### Context Pruning with Summarization

When the chat transcript exceeds configured thresholds:

1. The oldest entries are selected for summarization
2. The `engine.summarize_session()` method produces a condensed summary
3. The original entries are replaced by a single summary entry
4. This preserves conversational context while fitting within token limits

Two pruning triggers operate independently:

| Trigger | Condition |
|---|---|
| Budget pruning | Total characters > `engine.prune_threshold()` |
| Turn condensation | `context_manager.should_condense()` returns `True` |

### Terminal Observer & Vision Pipeline

The terminal observer (`_observe()`) listens for shell command events via `observe_history_events()`:

1. Extracts commands, CD targets, and feedback lines
2. Updates `history_context` with context commands
3. Detects phase changes and records them
4. Runs methodology audit checks
5. Auto-sets the target via `/box` when CD or host changes
6. Triggers CVE JIT extraction (`_run_cve_jit_worker`) for any CVE IDs found
7. Triggers auto-coach after cooldown

The clipboard watcher (`_watch_clipboard()`) monitors for image paste events and triggers vision analysis when clipboard images are detected.

---

## Data Flow Diagram

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  Terminal     │    │   Clipboard   │    │    User      │
│  Observer     │    │   Watcher     │    │   Input      │
└──────┬───────┘    └──────┬───────┘    └──────┬───────┘
       │                    │                    │
       ▼                    ▼                    ▼
┌──────────────────────────────────────────────────────┐
│              CerealKillerApp                         │
│                                                      │
│  ┌────────────┐  ┌────────────┐  ┌───────────────┐ │
│  │  _observe() │  │ _watch_    │  │ on_input_     │ │
│  │             │  │ clipboard  │  │ submitted     │ │
│  └─────┬──────┘  └─────┬──────┘  └───────┬───────┘ │
│        │               │                   │         │
│        ▼               ▼                   ▼         │
│  ┌────────────┐  ┌────────────┐  ┌───────────┐    │
│  │ _run_auto- │  │ _run_vision │  │ _run_chat │    │
│  │ coach_     │  │ _worker     │  │ _worker   │    │
│  └─────┬──────┘  └─────┬──────┘  └───────┬───┘    │
│        │               │                   │         │
│        └───────┬───────┴───────────────────┘         │
│                    ▼                                   │
│            ┌───────────────┐                          │
│            │ LLM Engine    │                          │
│            │ (chat/chat_  │                          │
│            │  with_image) │                          │
│            └───────┬───────┘                          │
│                    ▼                                   │
│            ┌───────────────┐                          │
│            │ MainDashboard │                          │
│            └───────┬───────┘                          │
│                    ▼                                   │
│            ┌───────────────┐                          │
│            │ Context       │                          │
│            │ Manager       │                          │
│            └───────────────┘                          │
└──────────────────────────────────────────────────────┘
```

---

## Worker Groups

Workers are organized into groups that control concurrency:

| Group | Exclusive? | Concurrent With |
|---|---|---|
| `llm` | Yes | Only one at a time |
| `coach` | No | All groups except itself (exclusive) |
| `search` | No | All groups except `llm` exclusive |
| `cve-jit` | No | Parallel with most workers |
| `media` | No | Parallel with most workers |

The `llm` group is exclusive (`exclusive=True`), meaning only one LLM worker runs at a time. Other groups can run concurrently as long as they don't conflict.

---

*Document generated from `src/cereal_killer/ui/app.py`*

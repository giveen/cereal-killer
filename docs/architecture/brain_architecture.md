# Brain Engine Architecture

## Overview

The **Brain** is the LLM orchestration engine at the core of Cereal Killer. It wraps the "Older Zero Cool" persona — a sarcastic, experienced, practical Socratic coach — and manages the full prompt assembly, LLM completion, response parsing, and session memory lifecycle for HackTheBox-style penetration testing coaching.

Brain is responsible for:

- **Prompt assembly**: Combining the pinned system prompt, thinking buffer, runtime directives, tool output, and pedagogical context into a single LLM-ready prompt.
- **LLM completion**: Routing requests through either the AsyncOpenAI client or LiteLLM, handling both text and vision (multimodal) paths.
- **Response parsing**: Extracting `<thought>...</thought>` reasoning and the visible answer from LLM output with multiple fallback strategies.
- **Session memory**: Persisting and retrieving cumulative reasoning traces via `ThinkingSessionStore` (backed by Redis).
- **Advanced features**: Stuck detection, input similarity, tool upgrade suggestions, backend tracing, and progressive pedagogy.

---

## Table of Contents

- [Data Structures](#data-structures)
- [System Prompts](#system-prompts)
- [Core Methods](#core-methods)
  - [`ask()` — Main Chat Flow](#ask--main-chat-flow)
  - [`react_to_command()` — Coaching Shortcut](#react_to_command--coaching-shortcut)
  - [`diagnose_failure()` — Failure Analysis Shortcut](#diagnose_failure--failure-analysis-shortcut)
  - [`ask_with_image()` — Multimodal Flow](#ask_with_image--multimodal-flow)
- [LLM Completion Methods](#llm-completion-methods)
  - [`_chat_completion()` — Text Completion](#_chat_completion--text-completion)
  - [`_chat_completion_with_image()` — Vision Completion](#_chat_completion_with_image--vision-completion)
- [Session Management](#session-management)
- [Advanced Features](#advanced-features)
- [Parse & Normalization](#parse--normalization)
- [Backend Tracing](#backend-tracing)
- [Prompt Assembly Pipeline](#prompt-assembly-pipeline)
- [Data Flow Diagram](#data-flow-diagram)

---

## Data Structures

### `BrainResponse`

```python
@dataclass
class BrainResponse:
    thought: str
    answer: str
    raw_content: str
    reasoning_content: str = ""
    backend_meta: dict[str, Any] = field(default_factory=dict)
```

| Field | Description |
|---|---|
| `thought` | The LLM's internal reasoning extracted from `<thought>...</thought>` tags |
| `answer` | The visible response intended for the user |
| `raw_content` | The unmodified content string returned by the LLM provider |
| `reasoning_content` | Structured reasoning from providers that emit it separately (e.g., O1, R1 models) |
| `backend_meta` | Metrics including latency, token usage, and cache hit status |

---

## System Prompts

### `OLDER_ZERO_COOL_PROMPT`

The core persona definition:

```
You are Older Zero Cool: sarcastic, experienced, practical.
You coach users using guided questions and progressive hints, not direct spoilers.
Keep answers concise, tactical, and safe.
Use retrieved reference material explicitly when relevant, e.g., call out
IppSec box parallels and methodology pivots.
When a CURRENT TARGET is set, keep guidance grounded to that target and avoid
drifting to unrelated machines.
If you mention another box, explain exactly why it is relevant and keep the
focus on the current target.
When producing internal reasoning, put it inside <thought>...</thought>.
If commands are needed, return them in fenced code blocks.
IMPORTANT CONTEXT: 'Gibson' is the name of the knowledge-base panel in this
TUI application, NOT a HackTheBox machine.
'cereal-killer' and 'cereal_killer' are this application's project names, NOT
HTB targets.
Never treat these names as boxes to hack. The CURRENT TARGET (if set) is the
actual HTB machine.
```

### Snark Addenda (`_SNARK_ADDENDA`)

A 10-level tone calibration dictionary. The default is level 8 ("Very sarcastic and mocking").

| Level | Tone |
|---|---|
| 1 | Professional and polite. Answer directly without sarcasm. |
| 2 | Helpful and friendly, with occasional dry humor. |
| 3 | Matter-of-fact with light sarcasm. |
| 4 | Slightly sarcastic but still helpful. |
| 5 | Balanced: mix of sarcasm and genuine guidance. |
| 6 | Sarcastic but still constructive. |
| 7 | Heavy sarcasm; mock the user's mistakes but stay useful. |
| **8** | **Very sarcastic and mocking; borderline insulting but technically brilliant. (Default)** |
| 9 | Harsh and cutting sarcasm; roast the user's poor decisions. |
| 10 | Abusive and brutal; tear into every mistake. Technically correct but caustic. |

### `_WEB_SEARCH_ADDENDUM`

Dynamically injected when live web search results are included in the prompt. Tells the LLM to acknowledge the web search with sarcasm and cite actual URLs.

---

## Core Methods

### `ask()` — Main Chat Flow

This is the primary entry point for Brain's interaction with the user. It orchestrates the full pipeline:

```
ask(
    user_prompt: str,
    history_commands: list[str],
    tool_output: str | None = None,
    tool_command: str | None = None,
    pathetic_meter: int = 0
) -> BrainResponse
```

**Execution Steps:**

1. **Context extraction** (lines 163–168):
   - Determines `machine_name` from `Path.cwd().name`
   - Filters out app-internal names (`cereal-killer`, `cereal_killer`, `gibson`)
   - Takes the last 20 commands from `history_commands` as context

2. **Tool output minification** (line 171):
   - Calls `minify_terminal_output()` to reduce verbosity

3. **Thinking buffer retrieval** (line 172):
   - Fetches cumulative reasoning from the session store

4. **Thinking flush detection** (lines 173–178):
   - If user asks "what am i doing wrong", clears previous reasoning to encourage fresh analysis

5. **Progress tracking** (lines 180–197):
   - Computes SHA-256 signature of last 5 commands
   - Resets stall counter when progress is detected
   - Increments stall counter when no progress
   - After 5 stalled turns, injects context-aware stuck status into reasoning

6. **Tiered search** (lines 199–217):
   - Calls `tiered_search()` with Redis VDB first, SearXNG fallback
   - Web search only enabled when pedagogy reaches DIRECT level
   - Fires `on_web_search_state_change` callback when search starts/ends

7. **Pinned system prompt** (lines 220–223):
   - Retrieves or creates a per-machine pinned system prompt via `_get_or_create_pinned_system_prompt()`
   - Caches reference material for the session

8. **Dynamic prompt assembly** (lines 225–266):
   - Constructs `dynamic_runtime_hint` from:
     - Abrasive prompt addendum (based on `pathetic_meter`)
     - Snark level addendum (based on settings)
     - Pedagogy system addendum
     - Web search addendum (if web results used)
   - Adds similarity note (if input resembles a previous failure)
   - Adds flush note (if thinking buffer was cleared)
   - Adds failure note (if tool output contains 404/Not Found)

9. **Message assembly** (lines 269–272):
   - System message: pinned system prompt
   - User message: combined thinking buffer + runtime directives + latest input + historical context + failure notes + minified tool output

10. **Deduplication** (line 274):
    - Drops consecutive duplicate role/content blocks

11. **LLM completion** (lines 276–279):
    - Calls `_chat_completion()` with messages

12. **Response parsing** (lines 280–291):
    - Parses response with `parse_brain_output()`
    - Persists reasoning to session if non-empty
    - Updates last user input

### `react_to_command()` — Coaching Shortcut

```python
async def react_to_command(
    self,
    command: str,
    history_commands: list[str],
    pathetic_meter: int = 0,
) -> BrainResponse
```

Wraps `ask()` with a pre-formulated coaching prompt: *"User just ran: {command}. Offer short coaching as guided questions."*

### `diagnose_failure()` — Failure Analysis Shortcut

```python
async def diagnose_failure(
    self,
    feedback_line: str,
    history_commands: list[str],
    pathetic_meter: int = 0,
) -> BrainResponse
```

Wraps `ask()` with a failure analysis prompt requesting root cause exploration and HackTricks-style alternative vectors.

### `ask_with_image()` — Multimodal Flow

```python
async def ask_with_image(
    self,
    user_prompt: str,
    image_path: str,
    history_commands: list[str] | None = None,
    pathetic_meter: int = 0,
) -> BrainResponse
```

Handles multimodal (vision) interactions:

1. Converts the image file to a base64 data URI via `_file_to_data_uri()`
2. Retrieves reference material via `retrieve_reference_material()` (top 3 snippets)
3. Builds pinned system prompt with reference context
4. Calls `_chat_completion_with_image()` which posts to the vision endpoint
5. Parses and persists reasoning identically to `ask()`

---

## LLM Completion Methods

### `_chat_completion()` — Text Completion

```python
async def _chat_completion(
    self,
    machine_name: str,
    messages: list[dict[str, Any]],
) -> tuple[str, str, dict[str, Any]]
```

Handles routing to either the AsyncOpenAI client or LiteLLM:

**LiteLLM Path** (when `USE_LITELLM` env var is set to `1`, `true`, or `yes`):
1. Imports `litellm.acompletion`
2. Logs request trace
3. Calls `litellm.acompletion()` with model, API base, API key, messages, temperature=0.4, and extra_body
4. Extracts content and reasoning_content from response
5. Calculates metrics (latency, cached tokens)
6. Logs response trace

**AsyncOpenAI Path** (default):
1. Verifies `self._client` is initialized
2. Builds request payload with model, base_url, messages, temperature=0.4, extra_body
3. Logs request trace
4. Calls `self._client.chat.completions.create()`
5. Logs response trace
6. Extracts content and reasoning_content
7. Handles empty completion payload case

**Common behavior:**
- Both paths include reasoning payload from session (`self._session.reasoning_payload()`)
- Both extract metrics via `_extract_completion_metrics()`
- Both normalize completion payloads via `_normalise_completion_payload()`
- Both operate with `temperature=0.4` for consistency

### `_chat_completion_with_image()` — Vision Completion

```python
async def _chat_completion_with_image(
    self,
    user_prompt: str,
    image_path: str,
    system_prompt: str,
    machine_name: str,
    context_text: str,
) -> tuple[str, str, dict[str, Any]]
```

Vision-specific completion that bypasses both the AsyncOpenAI client and LiteLLM:

1. Converts image to base64 data URI
2. Imports `httpx` (required for direct HTTP calls)
3. Constructs vision endpoint from `llm_vision_base_url` or falls back to `llm_base_url`
4. Builds messages array with system prompt + user content (text + image_url parts)
5. Posts directly via `httpx.AsyncClient` (120-second timeout)
6. Parses response data to extract content and reasoning
7. Handles errors with detailed trace logging

---

## Session Management

### `persist_mental_state()`

```python
async def persist_mental_state(self, history_commands: list[str] | None = None) -> None
```

Saves the current mental state to Redis:
- Retrieves thinking buffer from session
- Summarizes recent reconnaissance commands via `_summarize_recon()`
- Saves to session with timestamp

### `returning_greeting()`

```python
async def returning_greeting(self) -> str | None
```

Returns a personalized greeting if a mental state exists for the current machine, referencing the last recon summary.

### `get_thinking_buffer()`

```python
async def get_thinking_buffer(self, machine_name: str | None = None, max_chars: int = 6000) -> str
```

Retrieves the cumulative reasoning trace for a given machine, limited to `max_chars`. Filters out app-internal directory names.

### `persist_mental_state()` Schedule

The app schedules this call every 300 seconds (5 minutes) via `set_interval()` to keep the session store up to date.

---

## Advanced Features

### `_get_or_create_pinned_system_prompt()`

Caches per-machine system prompts to avoid regeneration:

```python
def _get_or_create_pinned_system_prompt(
    self,
    machine_name: str,
    baseline_reference: str,
) -> str
```

Combines the base system prompt, system prompt addendum (box/target context), and pinned RAG baseline. Cached in `self._pinned_system_prompt_by_machine` dictionary.

### `_progress_signature()`

Computes a SHA-256 hash of the last 5 history commands to detect when progress has changed:

```python
@staticmethod
def _progress_signature(history_commands: list[str]) -> str:
    joined = "\n".join(history_commands[-5:])
    if not joined:
        return ""
    return hashlib.sha256(joined.encode("utf-8", errors="ignore")).hexdigest()
```

### `_is_similar_input()`

Token-based similarity detection between current and previous user inputs:

```python
@staticmethod
def _is_similar_input(current: str, previous: str) -> bool:
    # Normalizes text, removes non-alphanumeric chars
    # Computes Jaccard similarity of token sets
    # Returns True if overlap >= 0.60
```

When similar input is detected, Brain is prompted to recognize the user is trying a new variation of a previously failed attempt and to provide a different hint.

### `_build_stuck_status()`

Context-aware stuck detection that generates status messages:

| Condition | Generated Message |
|---|---|
| "command injection" + "/ip" or "ip parameter" | "Status: User is stuck on command injection in the /ip parameter" |
| "command injection" | "Status: User is stuck on command injection attempts" |
| "/ip" or "ip parameter" | "Status: User is stuck on the /ip parameter behavior" |
| Default | "Status: User is stuck; prior hints did not produce progress" |

### `_abrasive_prompt_addendum()`

Dynamically adjusts snark based on `pathetic_meter`:

| Pathetic Meter Level | Snark Factor | Tone Directive |
|---|---|---|
| 0–1 | 1 | Low — sharp but mostly constructive |
| 2–4 | 4 | Moderate — mild abrasiveness and teasing |
| 5–7 | 7 | Elevated — increase sarcasm and condescension |
| 8–10 | 10 | Critical — openly abrasive, sarcastic, professor-style condescending |

### `snark_level_addendum()`

Returns the tone calibration string based on `settings.snark_level` (capped to 1–10 range), selecting from the `_SNARK_ADDENDA` dictionary.

### `suggest_tool_upgrade()`

Suggests more advanced tools when baseline tools are detected in user commands:

| Baseline Tool | Suggestion |
|---|---|
| `gobuster` | ffuf or feroxbuster |
| `dirb` | feroxbuster |
| `nikto` | nmap NSE scripts |
| `sqlmap` | Burp Suite Pro |
| `nc` | socat or ncat |
| `hydra` | medusa or custom scripts |

### `_dedupe_messages()`

Drops consecutive duplicate role/content blocks before sending to the LLM. Prevents redundant messages from inflating the context window.

---

## Parse & Normalization

### `parse_brain_output()`

```python
def parse_brain_output(content: str) -> BrainResponse
```

Extracts thought and answer from LLM content using a multi-strategy approach:

1. **`<thought>...</thought>` regex extraction** (primary):
   - Uses `THOUGHT_PATTERN = re.compile(r"<thought>(.*?)</thought>", re.DOTALL | re.IGNORECASE)`
   - Extracts all thought blocks and joins them

2. **"thought/response:" plain-text fallback**:
   - If no `<thought>` tags found, looks for "thought\n...\nresponse:...\n" format
   - Strips surrounding quotes from the answer section

3. **"Response:" within remaining content**:
   - If "response:" appears in the answer, splits on it and extracts the response section
   - Strips surrounding quotes

4. **Empty answer protection**:
   - If answer is empty but thought is present, uses thought as answer
   - Ensures Brain never returns a "silent" turn

### `_normalise_completion_payload()`

```python
@staticmethod
def _normalise_completion_payload(content: str, reasoning_content: str) -> tuple[str, str]
```

Handles providers that return reasoning-only output by swapping the fields. If `content` is empty but `reasoning_content` is populated, `reasoning_content` becomes `content` and `reasoning_content` becomes empty string.

---

## Backend Tracing

### `_backend_trace()`

```python
def _backend_trace(
    self,
    *,
    event: str,
    trace_id: str,
    provider: str,
    machine: str,
    payload: Any,
) -> None
```

Logs request/response pairs to a file with the following properties:

| Property | Details |
|---|---|
| **Storage** | JSON Lines format, one record per line |
| **Trace IDs** | UUIDs generated per request for correlation |
| **Authorization** | Headers redacted to `<redacted>` |
| **Image URIs** | Replaced with `<image-data-uri length=N>` |
| **String truncation** | Strings longer than `backend_trace_max_chars` are truncated with a byte count |
| **Provider tag** | Identifies which provider path was used (`openai-client`, `litellm`, `vision-httpx`) |
| **Event type** | `request`, `response`, or `error` |
| **Best-effort** | Trace errors are silently caught to avoid breaking model calls |

### Trace Record Format

```json
{
    "ts": "2024-01-01T00:00:00+00:00",
    "event": "request | response | error",
    "trace_id": "uuid-string",
    "provider": "openai-client | litellm | vision-httpx",
    "machine": "machine-name",
    "payload": { ... sanitized payload ... }
}
```

### `_sanitize_trace_payload()`

Recursively sanitizes payload dictionaries:
- Authorization headers (`authorization`, `api_key`, `token`, `x-api-key`) → `<redacted>`
- Image data URIs → `<image-data-uri length=N>`
- Long strings → truncated to `backend_trace_max_chars`

### `_extract_completion_metrics()`

Extracts performance metrics from LLM response payloads:

| Metric | Source |
|---|---|
| `latency_ms` | `time.perf_counter()` delta or `timings.total_ms` / `timings.latency_ms` / `timings.elapsed_ms` |
| `tokens_cached` | `usage.tokens_cached` or `usage.prompt_tokens_details.cached_tokens` or `tokens_cached` |
| `cache_hit` | Derived: `tokens_cached > 0` |
| `prompt_tokens` | `usage.prompt_tokens` |
| `completion_tokens` | `usage.completion_tokens` |
| `total_tokens` | `usage.total_tokens` |

---

## Prompt Assembly Pipeline

The full prompt sent to the LLM follows this structure:

```
┌─────────────────────────────────────────────────────────────┐
│ SYSTEM MESSAGE: pinned_system_prompt                       │
│ - Base OLDER_ZERO_COOL_PROMPT                               │
│ - system_prompt_addendum (box/target context)               │
│ - PINNED RAG BASELINE (reference material)                  │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ USER MESSAGE:                                                │
│                                                             │
│ [Optional] Thinking buffer from previous reasoning          │
│ Runtime coaching directives:                                │
│   - Abrasive prompt addendum (pathetic meter based)         │
│   - Snark level addendum (settings based)                   │
│   - Pedagogy system addendum                                │
│   - Web search addendum (if web used)                       │
│                                                             │
│ Latest Command/Input: {latest_input}                        │
│                                                             │
│ Historical Commands: {context_block}                        │
│                                                             │
│ [Optional] Failure note (if 404 detected)                   │
│                                                             │
│ Minified tool output: {minified_tool_output}                │
│                                                             │
│ User prompt: {user_prompt}                                  │
│                                                             │
│ [Optional] Similarity note (if similar input detected)      │
│ [Optional] Flush note (if thinking was cleared)             │
└─────────────────────────────────────────────────────────────┘
```

### Prompt Assembly Components

Each component is conditionally included:

| Component | Condition |
|---|---|
| Thinking buffer | `settings.preserve_thinking` AND prompt contains "show/include/send/dump" + "thought/reasoning/thinking" |
| Thinking flush note | Prompt matches "what am i doing wrong" (case-insensitive) |
| Web search addendum | `search_result.used_web == True` |
| Similarity note | `_is_similar_input()` returns `True` |
| Failure note | Tool output matches "404" or "not found" (case-insensitive) |
| Abrasive prompt addendum | Always included, tone varies by `pathetic_meter` |
| Snark addendum | Always included, tone varies by `settings.snark_level` |

---

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Brain Engine (brain.py)                       │
│                                                                      │
│  ┌──────────────┐                                                   │
│  │  ask()       │ ◄── Main entry point                             │
│  │              │                                                   │
│  │  1. Extract  │ ┌────────────┐      ┌─────────────────────┐     │
│  │     context  │ │  Session   │      │  Tiered Search      │     │
│  │     & cmd    │ │  Store     │      │  (Redis VDB +       │     │
│  │              │ │  (Redis)   │      │   SearXNG)          │     │
│  │  2. Retrieve │ └─────┬──────┘      └──────────┬──────────┘     │
│  │     thinking │        │                         │               │
│  │     buffer   │        ▼                         │               │
│  │              │  ┌──────────────┐         ┌─────────────┐      │
│  │  3. Check    │  │ _session_    │         │ _get_or_    │      │
│  │     progress │  │ machine_     │         │ create_     │      │
│  │              │  │ name()       │         │ pinned_     │      │
│  │  4. Search   │  └──────┬───────┘         │ system_     │      │
│  │     tiered   │         │                  │ prompt()    │      │
│  │              │         │                  └──────┬──────┘      │
│  │  5. Build    │         ▼                         │            │
│  │     runtime  │  ┌──────────────┐         ┌──────▼──────┐     │
│  │     hints    │  │ _summarize_  │         │ Assemble    │     │
│  │              │  │ _recon()     │         │ Messages    │     │
│  │  6. Parse    │  └──────────────┘         └──────┬──────┘     │
│  │     response   │                                 │            │
│  │              │  ┌──────────────┐    ┌───────────▼───────┐    │
│  │  7. Persist  │  │ _is_similar_ │    │   _chat_          │    │
│  │     thought  │  │ _input()     │    │   _completion()   │    │
│  └──────────────┘  └──────────────┘    └───────┬───┬───────┘    │
│                                                 │   │            │
│                                  ┌──────────────▼   │   ┌───────▼──────┐
│                                  │ _build_stuck_    │   │  LLM Provider │
│                                  │ _status()        │   │ (OpenAI/      │
│                                  │                  │   │  LiteLLM/     │
│                                  │                  │   │  Vision)      │
└─────────────────────────────────────────────────────┴───┴──────────────┘
                                                        │
                                         ┌──────────────▼──────────┐
                                         │  _backend_trace()       │
                                         │  (JSONL file logging)   │
                                         └─────────────────────────┘
```

### Component Relationships

```
Brain
  │
  ├─ ask()
  │   ├─ get_thinking_buffer() ─────────────────────────────┐
  │   ├─ _progress_signature() ─────────────────────────────┤
  │   ├─ _is_similar_input() ───────────────────────────────┤
  │   ├─ _build_stuck_status() ─────────────────────────────┤
  │   ├─ _abrasive_prompt_addendum() ───────────────────────┤
  │   ├─ snark_level_addendum() ────────────────────────────┤
  │   ├─ _get_or_create_pinned_system_prompt() ─────────────┤
  │   ├─ parse_brain_output() ──────────────────────────────┤
  │   └─ _chat_completion() ────────────────────────────────┘
  │       ├─ _normalise_completion_payload()
  │       ├─ _extract_completion_metrics()
  │       ├─ _backend_trace()
  │       │   ├─ _sanitize_trace_payload()
  │       │   └─ _ensure_trace_file()
  │       └─ _dedupe_messages()
  │
  ├─ react_to_command() ──── wraps ask()
  │
  ├─ diagnose_failure() ──── wraps ask()
  │
  ├─ ask_with_image()
  │   └─ _chat_completion_with_image()
  │       ├─ _file_to_data_uri()
  │       └─ httpx.AsyncClient (vision endpoint)
  │
  ├─ persist_mental_state() ── calls _summarize_recon()
  │
  ├─ returning_greeting()
  │
  ├─ suggest_tool_upgrade()
  │
  └─ generate_loot_report() / summarize_session() / synthesize_search_results()
```

---

*Document generated from `src/mentor/engine/brain.py`*

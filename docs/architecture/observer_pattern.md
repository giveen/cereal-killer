# Observer Pattern (Stalker)

## Overview

The **Stalker** module implements an asynchronous shell history observer that watches `.bash_history`, `.zsh_history`, Fish history files, and feedback signal files for new entries. It parses commands, classifies them, detects coaching triggers (success/failure signals, box discovery, context shifts), and yields structured `HistoryEvent` objects for the Brain engine to process.

Stalker is responsible for:

- **File watching**: Monitoring shell history and feedback signal files using `watchfiles.awatch()`.
- **Multi-format parsing**: Handling Zsh extended, Fish, and raw bash history formats.
- **Command classification**: Identifying technical tool usage (nmap, gobuster, sqlmap, etc.).
- **Signal detection**: Recognizing success/failure indicators in command output.
- **Box discovery**: Inferring the target machine from `cd` commands and `.htb` host references.
- **Context filtering**: Approximating CWD state and limiting output to relevant commands.
- **Deduplication & cooldown**: Preventing duplicate events and feedback loops.

---

## Table of Contents

- [History Resolution](#history-resolution)
- [History Parsing](#history-parsing)
- [Command Classification](#command-classification)
- [Event Detection](#event-detection)
  - [Feedback Signal Detection](#feedback-signal-detection)
  - [Box CD Detection](#box-cd-detection)
  - [Box Host Detection](#box-host-detection)
- [Context Filtering](#context-filtering)
- [Deduplication](#deduplication)
- [Cooldown](#cooldown)
- [Async Generator Pattern](#async-generator-pattern)
- [Data Flow Diagram](#data-flow-diagram)

---

## History Resolution

The `_resolve_history_path()` method selects which history file to monitor. It follows a cascading priority:

1. **`HISTORY_PATH` environment variable** — if set, use this path exclusively.
2. **Scan candidate files** — check common history file locations:
   - `.zsh_history` (Zsh)
   - `.bash_history` (Bash)
   - Fish history file locations
3. **Readability check** — each candidate is verified with `_check_history_path_readable()`:
   - File exists and is readable
   - Has non-zero content or is actively being written to
4. **Fallback** — if no candidate is found or readable, default to `~/.bash_history`.

### Resolution Flow

```
HISTORY_PATH env set? ─Yes─→ use env var value
                              │
                             No
                              │
                              ▼
              ┌─────────────────────────────┐
              │  Scan candidates:            │
              │  - ~/.zsh_history            │
              │  - ~/.bash_history           │
              │  - ~/.local/share/fish/...   │
              └─────────────────────────────┘
                              │
                              ▼
              ┌─────────────────────────────┐
              │  _check_history_path_read-  │
              │  ability(path)               │
              └─────────────────────────────┘
                              │
              ┌───────────────┤
              │               │
            exists?         no ──→ fallback
              │               ~/.bash_history
           yes  │
              │
              ▼
       use candidate path
```

---

## History Parsing

The `parse_history_lines()` method normalizes raw history lines into a clean command string, handling multiple shell formats:

### Supported Formats

| Format | Example Input | Parsed Command |
|---|---|---|
| **Zsh extended** | `: 1712345678:0;sudo nmap -sV 10.10.10.1` | `sudo nmap -sV 10.10.10.1` |
| **Fish** | `- cmd: echo Hello World` | `echo Hello World` |
| **Raw (Bash)** | `ls -la /etc/shadow` | `ls -la /etc/shadow` |

### Zsh Extended Format

Zsh with `HIST_FIND_NO_DUPS` and extended history writes lines prefixed with `: timestamp:flags;command`. The parser:

1. Splits on `;` (semicolon)
2. Extracts the command portion after the second colon-separated field
3. Strips leading/trailing whitespace

### Fish Format

Fish shell writes entries as `- cmd: command` or `- cmd: command | output`. The parser:

1. Detects the `- cmd:` prefix
2. Splits on the last `: ` delimiter
3. Extracts the command portion

### Raw Format

Plain bash history lines are returned as-is (trimmed of whitespace).

---

## Command Classification

The `is_technical_command()` method determines whether a command represents a penetration testing or technical tool invocation.

### Filtered Commands (Excluded)

- **Python source lines** — detected by looking for Python keywords (`def`, `class`, `import`, `from`, `if __name__`, `print(`, `for`, `with open`, `lambda`) that appear inside heredoc leakage (e.g., Python REPL output captured in history).

### TECHNICAL_TOOLS Set

The following tools are recognized as technical commands:

| Category | Tools |
|---|---|
| **Network Scanner** | nmap |
| **Directory Brute-force** | gobuster, ffuf, dirsearch |
| **SMB/Enum** | smbclient, enum4linux |
| **Web Fuzzing** | nikto, wfuzz, ffuf, dirsearch |
| **SQL Injection** | sqlmap |
| **Brute-force** | hydra, hashcat, john |
| **Traffic Capture** | tcpdump, wireshark |
| **Framework** | msfconsole, metasploit |
| **Post-exploitation** | netexec, crackmapexec, psexec, pth-winrm |

### Prefix Pattern Matching

Some tools are detected via command prefix patterns rather than exact matches:

| Prefix Pattern | Matched For |
|---|---|
| `nmap` | Any command starting with nmap |
| `gobuster` | Any command starting with gobuster |
| `smb` | smbclient, smbclient-ng, etc. |
| `enum4linux` | enum4linux variants |

### Classification Flow

```python
def is_technical_command(command: str) -> bool:
    # 1. Skip Python source lines (heredoc leakage)
    if has_python_source(command):
        return False

    # 2. Parse arguments safely
    try:
        args = shlex.split(command)
    except ValueError:
        return False

    # 3. Check against TECHNICAL_TOOLS set
    tool_name = args[0] if args else ""
    if tool_name in TECHNICAL_TOOLS:
        return True

    # 4. Check prefix patterns
    if any(command.startswith(prefix) for prefix in PREFIX_PATTERNS):
        return True

    return False
```

---

## Event Detection

### Feedback Signal Detection

The `detect_feedback_signal(text)` method scans output text for success or failure markers, enabling the Brain to understand whether the user's last command succeeded or failed.

#### Failure Markers

| Marker | Example Context |
|---|---|
| `permission denied` | `permission denied (publickey)` |
| `command not found` | `bash: gobuster: command not found` |
| `connection refused` | `curl: (7) Connection refused` |
| `authentication failed` | SSH/HTTPS auth failures |
| `no such file or directory` | File/directory not found |
| `nt_status_logon_failure` | SMB authentication failure |

#### Success Markers

| Marker | Example Context |
|---|---|
| `root@` | Shell prompt change to root |
| `id: uid=0` | Elevation confirmation |
| `uid=0(` | UID 0 confirmation (BSD style) |
| `pwned` | Exploitation confirmation |
| `shell spawned` | Reverse/interactive shell established |
| `whoami\nroot` | Whoami confirms root user |

#### Filtering Rules

- Lines longer than **300 characters** are treated as prose/output and ignored (feedback signals are typically short error messages).
- Lines identified as Python source code (heredoc leakage) are skipped.
- Returns `"success"` or `"failure"` string, or `None` if no signal detected.

### Box CD Detection

The `detect_box_cd(command)` method identifies `cd` commands targeting HackTheBox-style machines:

#### Detection Logic

1. **Extract path** — splits on `cd`, takes the path component.
2. **Strip quotes** — removes surrounding `'`, `"`, or `` ` `` characters.
3. **Resolve tilde** — expands `~` prefix to home directory.
4. **Take last path component** — `cd ~/htb/Lame` → `Lame`.
5. **Validate** against `HTB_NAME_RE` pattern:
   - 1–24 characters
   - Alphanumeric and hyphens only
6. **Exclude** common shell navigation tokens: `.`, `..`, `-`, `/`, `~`.

#### Example Detections

| Command | Extracted Box |
|---|---|
| `cd ~/htb/Lame` | `Lame` |
| `cd ~/htb/box-name` | `box-name` |
| `cd ~/boxes/SnakeOil` | `SnakeOil` |

#### Validation Regex

```
^([a-zA-Z][a-zA-Z0-9-]{0,23})$
```

### Box Host Detection

The `detect_box_host(command)` method identifies references to `.htb` domain hosts:

#### Detection Logic

1. Applies regex: `r"\b([a-zA-Z][a-zA-Z0-9-]{0,23})\.htb\b"`
2. Extracts the hostname component (before `.htb`).
3. Excludes common internal names: `localhost`, `local`, `host`, `gateway`.

#### Example Detections

| Command | Extracted Host |
|---|---|
| `ping 10.10.10.1` (context) | — |
| `nslookup lamer.htb` | `lamer` |
| `nmap -iL targets.htb` | `targets` |
| `curl http://10.10.10.5.lamer.htb` | `lamer` |

---

## Context Filtering

The `filter_context_commands()` method reconstructs a shell session context by tracking directory state and filtering commands relevant to the current working directory.

### How It Works

1. **Directory tracking** — maintains an internal CWD state by observing `cd` commands.
2. **Command filtering** — only keeps commands that were executed in the current CWD:
   - Commands run before the most recent `cd` are excluded from context.
   - Commands run in different directories are excluded.
3. **Context limit** — applies `HISTORY_CONTEXT_LIMIT` (default: 50 commands) to cap context size.

### Why This Matters

The Brain engine receives only the relevant command history for the current directory, preventing:
- Irrelevant commands from different directories cluttering context
- Excessive token usage from bloated history
- Misattribution of commands from unrelated sessions

---

## Deduplication

The deduplication system prevents the same command from generating multiple coaching events.

### Mechanism

- **`command_hash()`** generates a SHA-256 hash of the normalized command string.
- **Normalization** includes:
  - Stripping leading/trailing whitespace
  - Collapsing multiple spaces to single spaces
  - Converting to lowercase for comparison
- **`seen_hashes` set** stores hashes of all processed commands.

### Log Rotation Handling

When a history file is truncated (detected by a smaller file offset than previously recorded):
1. The `seen_hashes` set is **cleared**.
2. This ensures commands that appeared before the truncation point can be re-observed.
3. This is important because bash history files rotate: old entries are removed and new ones appended.

### Pseudocode

```python
def process_command(self, command: str) -> HistoryEvent | None:
    hash_value = command_hash(command)

    # Check deduplication
    if hash_value in self._seen_hashes:
        return None  # Already seen

    self._seen_hashes.add(hash_value)
    return self._create_event(command, ...)
```

---

## Cooldown

The `_FEEDBACK_COOLDOWN_SECS = 30` constant prevents feedback signal detection from generating duplicate coaching events too frequently.

### How It Works

1. When a feedback signal (success/failure) is detected, the triggering line's hash is stored in `_triggered_line_hashes`.
2. If the same line triggers again within 30 seconds, the feedback signal is suppressed.
3. `_triggered_line_hashes` has a **maximum capacity of 200 entries**.
4. When full, the oldest entries are discarded (FIFO eviction).
5. Periodic cleanup removes expired entries (older than cooldown window).

### Purpose

Prevents feedback loops where:
- The same failure message appears repeatedly (e.g., a command in a loop).
- Rapid successive commands produce redundant coaching prompts.
- High-volume history bursts overwhelm the Brain engine.

---

## Async Generator Pattern

The `observe_history()` method is an async generator that yields `HistoryEvent` objects as new shell commands are detected.

### Generator Lifecycle

```
┌───────────────────────────────────────────────────┐
│              observe_history()                     │
│                                                    │
│  ┌──────────────────────────────┐                 │
│  │ watchfiles.awatch() on:      │                 │
│  │ - resolved history file       │                 │
│  │ - feedback signal files       │                 │
│  └──────────────────────────────┘                 │
│              │                                     │
│              ▼                                     │
│  ┌──────────────────────────────┐                 │
│  │ Track file offsets per file: │                 │
│  │ - offset_by_path             │                 │
│  └──────────────────────────────┘                 │
│              │                                     │
│              ▼                                     │
│  ┌──────────────────────────────┐                 │
│  │ On file change event:        │                 │
│  │ 1. Read delta (new bytes)    │                 │
│  │ 2. Split into lines          │                 │
│  │ 3. Parse each command        │                 │
│  │ 4. Classify & filter         │                 │
│  │ 5. Detect signals/box cues   │                 │
│  │ 6. Check dedup/cooldown      │                 │
│  │ 7. Build HistoryEvent        │                 │
│  │ 8. yield event               │                 │
│  └──────────────────────────────┘                 │
│                                                    │
│  ┌──────────────────────────────┐                 │
│  │ On KeyboardInterrupt:        │                 │
│  │ 1. Cleanup watch handles     │                 │
│  │ 2. Close file handles        │                 │
│  │ 3. Return                    │                 │
│  └──────────────────────────────┘                 │
└───────────────────────────────────────────────────┘
```

### HistoryEvent Structure

Each event yielded by the generator contains:

| Field | Type | Description |
|---|---|---|
| `command` | `str` | The parsed command string |
| `context_commands` | `list[str]` | Recent commands in the current CWD |
| `trigger_brain` | `bool` | Whether to trigger a Brain coaching response |
| `feedback_signal` | `str \| None` | `"success"`, `"failure"`, or `None` |
| `feedback_line` | `str \| None` | The output line containing the signal |
| `cd_target` | `str \| None` | Box name from `cd` command (e.g., `"Lame"`) |
| `host_target` | `str \| None` | Box hostname from `.htb` reference (e.g., `"lamer"`) |

### Usage Pattern

```python
async for event in stalker.observe_history():
    if event.trigger_brain:
        response = await brain.ask(
            user_prompt=event.command,
            history_commands=event.context_commands,
            tool_command=event.command,
        )
        await brain.persist_mental_state(event.context_commands)
```

---

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Observer Pattern (Stalker)                       │
│                                                                          │
│  ┌──────────────────────┐                                               │
│  │  watchfiles.awatch()  │ ──→ File change events                       │
│  │  (history + feedback) │                                               │
│  └────────┬─────────────┘                                               │
│           │                                                              │
│           ▼                                                              │
│  ┌──────────────────────────┐                                          │
│  │  parse_history_lines()   │ ──→ Clean command string                  │
│  │  (multi-format parser)   │                                          │
│  └────────┬─────────────────┘                                          │
│           │                                                              │
│           ▼                                                              │
│  ┌──────────────────────────┐                                          │
│  │  is_technical_command()  │ ──→ Boolean: is tech command?             │
│  │                          │                                          │
│  │  Filters:                 │                                          │
│  │  - Python source lines    │                                          │
│  │  - TECHNICAL_TOOLS set    │                                          │
│  │  - Prefix patterns        │                                          │
│  └────────┬─────────────────┘                                          │
│           │                                                              │
│           ▼                                                              │
│  ┌──────────────────────────┐                                          │
│  │  detect_feedback_signal() │ ──→ "success" | "failure" | None        │
│  │  (300-char limit)        │                                          │
│  └────────┬─────────────────┘                                          │
│           │                                                              │
│           ▼                                                              │
│  ┌──────────────────────────┐                                          │
│  │  detect_box_cd()         │ ──→ Box name or None                      │
│  │  (HTB_NAME_RE)           │                                          │
│  └────────┬─────────────────┘                                          │
│           │                                                              │
│           ▼                                                              │
│  ┌──────────────────────────┐                                          │
│  │  detect_box_host()       │ ──→ Hostname or None                      │
│  │  (.htb regex)            │                                          │
│  └────────┬─────────────────┘                                          │
│           │                                                              │
│           ▼                                                              │
│  ┌──────────────────────────┐                                          │
│  │  filter_context_commands │ ──→ Context command list                  │
│  │  (CWD tracking)          │                                          │
│  └────────┬─────────────────┘                                          │
│           │                                                              │
│           ▼                                                              │
│  ┌──────────────────────────┐                                          │
│  │  Deduplication Check     │                                          │
│  │  (SHA-256 hash set)      │ ──→ Skip if duplicate                     │
│  │                          │                                          │
│  │  Log rotation detect:    │                                          │
│  │  → clear seen_hashes     │                                          │
│  └────────┬─────────────────┘                                          │
│           │                                                              │
│           ▼                                                              │
│  ┌──────────────────────────┐                                          │
│  │  Cooldown Check          │                                          │
│  │  (_triggered_line_hashes)│                                          │
│  │  (30s cooldown, 200 cap) │                                          │
│  └────────┬─────────────────┘                                          │
│           │                                                              │
│           ▼                                                              │
│  ┌──────────────────────────┐                                          │
│  │  yield HistoryEvent      │                                          │
│  │                          │                                          │
│  │  - command               │                                          │
│  │  - context_commands      │                                          │
│  │  - trigger_brain         │                                          │
│  │  - feedback_signal       │                                          │
│  │  - feedback_line         │                                          │
│  │  - cd_target             │                                          │
│  │  - host_target           │                                          │
│  └──────────────────────────┘                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

### Component Relationships

```
Observer (Stalker)
  │
  ├─ _resolve_history_path()
  │   ├─ HISTORY_PATH env var
  │   ├─ _check_history_path_readable()
  │   └─ fallback ~/.bash_history
  │
  ├─ parse_history_lines()
  │   ├─ Zsh extended format (timestamp stripping)
  │   ├─ Fish format (prefix stripping)
  │   └─ Raw format (passthrough)
  │
  ├─ is_technical_command()
  │   ├─ Python source line filter
  │   ├─ TECHNICAL_TOOLS set lookup
  │   └─ PREFIX_PATTERNS matching
  │
  ├─ detect_feedback_signal()
  │   ├─ Failure markers list
  │   ├─ Success markers list
  │   ├─ 300-char prose filter
  │   └─ Python source skip
  │
  ├─ detect_box_cd()
  │   ├─ Path normalization (quotes, ~, last component)
  │   └─ HTB_NAME_RE validation
  │
  ├─ detect_box_host()
  │   └─ .htb regex + exclusion list
  │
  ├─ filter_context_commands()
  │   ├─ CWD state tracking via cd commands
  │   └─ HISTORY_CONTEXT_LIMIT enforcement
  │
  ├─ command_hash()
  │   └─ SHA-256 of normalized command
  │
  ├─ observe_history()
      ├─ watchfiles.awatch() setup
      ├─ Offset tracking per file
      ├─ Delta-only reads on changes
      ├─ Deduplication (seen_hashes set)
      ├─ Cooldown (_triggered_line_hashes, 30s, cap 200)
      └─ yield HistoryEvent
```

---

*Document generated from `src/mentor/observer/stalker.py`*

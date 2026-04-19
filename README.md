# cereal-killer

`cereal-killer` is a terminal UI assistant for box-style workflow coaching. It watches command history, tracks phase progress, and provides guidance through a local OpenAI-compatible LLM, Redis-backed context, and optional web fallback search.

## Quickstart

### Option A: Docker Compose (recommended)

1. Create environment file:

```bash
cp .env.example .env
```

2. Update `.env` and set at least `LLM_BASE_URL` to your running OpenAI-compatible endpoint.
	You can point this to another computer on your network (for example a remote llama.cpp server).

3. Build and start services:

```bash
make docker-build
make docker-up
```

Or run directly with Docker Compose:

```bash
docker compose up --build
```

This starts:
- `app` (the TUI)
- `redis` (Redis Stack)
- `searxng` (optional web search backend used as last resort)

To stop services:

```bash
make docker-down
```

### Option B: Local Python run

Requirements:
- Python 3.12+
- Redis service available

Install and run:

```bash
python -m pip install -e .
python -m cereal_killer.main
```

## What It Does

- Watches shell history and reacts to technical commands.
- Provides phase-aware coaching in a Textual TUI.
- Supports slash commands for known-box and exploration modes.
- Stores and retrieves Redis-backed learning/session context.
- Uses tiered search (local knowledge first, optional SearXNG fallback).

## Features

- Textual dashboard with live feed, reasoning panel, and checklist widget.
- Pedagogy hint levels that become more direct when progress stalls.
- Methodology audit warning when exploitation starts before recon.
- `/box` and `/new-box` context switching commands.
- `/victory` learnings vault command (also aliased as `/pwned`).
- IppSec dataset sync into Redis vector index.

## Configuration

Use environment variables (from `.env` in Docker or your shell locally):

- `LLM_BASE_URL` (default `http://host.docker.internal:8000/v1`)
- `LLM_MODEL` (default `qwen3.6`)
- `LLM_API_KEY` (default `not-needed`)
- `REASONING_PARSER` (default `qwen3`)
- `MAX_MODEL_LEN` (default `262144`)

Template file: `.env.example`

Redis and SearXNG settings are intentionally not exposed in the default template. The Docker stack provides sane internal defaults, and most users should leave those unchanged.

For external LLM hosts, set `LLM_BASE_URL` to a reachable IP or DNS name, for example `http://192.168.1.50:8000/v1`.

## Usage

After launch, use the prompt input in the TUI and run commands in your shell as usual.

Useful slash commands:

```text
/help
/box <machine-name>
/new-box <machine-name>
/loot
/victory <what-you-learned>
/clear [machine-name]
```

Keyboard shortcuts:

- `Ctrl+C`: quit
- `Ctrl+Y`: copy last code block
- `Ctrl+T`: toggle thinking panel
- `Ctrl+S`: vision capture
- `Ctrl+B`: Easy button pulse

## Docker Commands

- `make docker-build`: build service images
- `make docker-up`: build and start the stack
- `make docker-down`: stop and remove the stack

## Example Workflow

```text
1) /new-box lame
2) Run recon commands in shell (nmap, dir enumeration, SMB checks, etc.)
3) Ask follow-up questions in the TUI prompt
4) /victory <summary of vuln + exploit path>
```

## Data Sync

To sync the IppSec dataset into Redis:

```bash
python scripts/sync_ippsec.py
```

Or via the installed console script:

```bash
sync-ippsec
```

## Project Structure

- `src/cereal_killer/main.py`: app entrypoint
- `src/cereal_killer/ui.py`: Textual dashboard and interaction flow
- `src/cereal_killer/engine.py`: LLM integration and session orchestration
- `src/cereal_killer/knowledge_base.py`: RedisVL index and dataset sync
- `src/cereal_killer/observer.py`: history observer integration
- `src/mentor/engine/`: command routing, pedagogy, search orchestration, methodology, session state
- `scripts/sync_ippsec.py`: standalone dataset sync launcher

## Dependencies

Core Python dependencies (from `pyproject.toml`):

- `textual`
- `watchfiles`
- `redisvl`
- `openai`
- `httpx`
- `pyperclip`
- `pyautogui`
- `mss`

Docker services:

- `redis/redis-stack`
- `searxng/searxng`

## Development

Install editable package:

```bash
python -m pip install -e .
```

Run app:

```bash
python -m cereal_killer.main
```

## Testing

Automated tests are written under `tests/` using Python `unittest` style modules.

Detected test command used in this repository workflow:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -q
```

## Troubleshooting

- If the app cannot reach the model, verify `LLM_BASE_URL` and that your OpenAI-compatible endpoint is running.
- If your model runs on another computer, make sure that host allows inbound traffic on the model port (for example `8000`) and that Docker can route to it.
- If knowledge lookup is empty, ensure Redis is reachable and run dataset sync.
- If Docker app cannot call host model, check `host.docker.internal` routing and the compose `extra_hosts` setting.

## Contributing

No dedicated contribution guide is currently present in this repository. If you plan to contribute, open an issue/PR with a clear summary and reproducible steps.

## License

Licensed under the terms in `LICENSE`.

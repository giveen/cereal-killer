# cereal-killer

`cereal-killer` is a terminal UI assistant for box-style workflow coaching. It watches command history, tracks phase progress, and provides guidance through a local OpenAI-compatible LLM, Redis-backed context, and optional web fallback search.

## Table of Contents

- [Quickstart](#quickstart)
- [What It Does](#what-it-does)
- [Features](#features)
- [Configuration](#configuration)
- [Usage](#usage)
- [Data Sync](#data-sync)
- [Docker Commands](#docker-commands)
- [Project Structure](#project-structure)
- [Development](#development)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

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
make sync-ippsec
make tui
```

Workflow order for first run:

1. `make docker-build`
2. `make docker-up`
3. `make sync-ippsec`
4. `make tui`

Note: the sync target is named `make sync-ippsec`.

Or run directly with Docker Compose:

```bash
docker compose up -d --build redis searxng
cereal-killer
```

This starts:
- `redis` (Redis Stack)
- `searxng` (optional web search backend used as last resort)

Then `make tui` (or `cereal-killer`) launches the Textual UI from your host shell.

SearXNG is exposed on `http://localhost:18080`.

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

- `REDIS_URL` (default `redis://localhost:6379`)
- `REDIS_INDEX` (default `ippsec_idx`)
- `LLM_BASE_URL` (default `http://host.docker.internal:8000/v1`)
- `LLM_MODEL` (default `qwen3.6`)
- `LLM_API_KEY` (default `not-needed`)
- `REASONING_PARSER` (default `qwen3`)
- `MAX_MODEL_LEN` (default `262144`)
- `SEARXNG_BASE_URL` (default `http://localhost:18080`)
- `SEARXNG_VECTOR_THRESHOLD` (default `0.7`)

Template file: `.env.example`

The default `.env.example` focuses on model settings. Redis and SearXNG defaults are already defined in application config and Make targets.

For external LLM hosts, set `LLM_BASE_URL` to a reachable IP or DNS name, for example `http://192.168.1.50:8000/v1`.

## Usage

After launch, use the prompt input in the TUI and run commands in your shell as usual.

Recommended startup flow:

```bash
make docker-build
make docker-up
make sync-ippsec
make tui
```

Useful slash commands:

```text
/help
/box <machine-name>
/new-box <machine-name>
/loot
/victory <what-you-learned>
/clear [machine-name]
/exit
```

Keyboard shortcuts:

- `Ctrl+C`: quit
- `Ctrl+T`: toggle thinking panel
- `Ctrl+B`: Easy button pulse

## Example Workflow

```text
1) /new-box lame
2) Run recon commands in shell (nmap, dir enumeration, SMB checks, etc.)
3) Ask follow-up questions in the TUI prompt
4) /victory <summary of vuln + exploit path>
```

## Data Sync

No local virtual environment is required if you use Docker:

```bash
make sync-ippsec
```

This runs the sync inside the app container and rebuilds the app image first so recent code changes from `git pull` are applied.

To sync from your host Python environment instead:

```bash
python scripts/sync_ippsec.py
```

Or via the installed console script:

```bash
sync-ippsec
```

If you run the sync command from the host while Redis is started via Docker, use:

```bash
REDIS_URL=redis://localhost:6379 python scripts/sync_ippsec.py
```

## Docker Commands

- `make docker-build`: build service images
- `make docker-up`: build and start Redis + SearXNG in the background
- `make tui`: launch the Textual app (uses local install if available, otherwise falls back to `docker compose run --rm --build app cereal-killer`)
- `make docker-down`: stop and remove the stack

These Make targets enable Docker BuildKit by default, so package download/build caches are reused across rebuilds for faster iteration.
`make docker-up` intentionally does not attach to the app container. This avoids compose log-prefix interference in fullscreen TUI rendering.

Redis persistence:

- Redis data is stored in a named Docker volume (`redis_data`).
- This means `make docker-down`, `git pull`, and `make docker-build` do not wipe indexed data.
- To intentionally reset Redis data, run:

```bash
docker compose down -v
```

## Project Structure

- `src/cereal_killer/main.py`: app entrypoint
- `src/cereal_killer/ui/app.py`: Textual app controller and message routing
- `src/cereal_killer/ui/screens.py`: dashboard and modal screens
- `src/cereal_killer/ui/widgets.py`: custom UI widgets
- `src/cereal_killer/ui/styles.tcss`: Textual stylesheet
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
- SearXNG config is mounted as a read-only file (`config/searxng/settings.yml`) to avoid container ownership/permission drift in the repo. This prevents common `git pull` and `git reset` failures caused by root or container UID rewrites on tracked files.

## Contributing

No dedicated contribution guide is currently present in this repository. If you plan to contribute, open an issue/PR with a clear summary and reproducible steps.

## License

Licensed under the terms in `LICENSE`.

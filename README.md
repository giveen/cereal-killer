# cereal-killer

`cereal-killer` is a terminal UI assistant for box-style workflow coaching. It watches command history, tracks phase progress, and provides guidance through a local OpenAI-compatible LLM, Redis-backed context, and optional web fallback search.

## Table of Contents

- [Quickstart](#quickstart)
- [What It Does](#what-it-does)
- [Features](#features)
- [Configuration](#configuration)
- [Model Recommendations by VRAM](#model-recommendations-by-vram)
- [Usage](#usage)
- [Knowledge Sync (Redis Sources)](#knowledge-sync-redis-sources)
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
make docker-up-init
make
```

Workflow order for first run:

1. `make docker-build`
2. `make docker-up-init`
3. `make` (or `make run`)

`make docker-up-init` now performs a full knowledge resync (`sync-all`), including IppSec and the configured library sources.

Manual alternative:

1. `make docker-up`
2. `make sync-all`
3. `make`

If you only want the IppSec dataset (without full library refresh), use `make sync-ippsec`.

Or run directly with Docker Compose:

```bash
docker compose up -d --build redis searxng
cereal-killer
```

This starts:
- `redis` (Redis Stack)
- `searxng` (optional web search backend used as last resort)

Then `make` (or `make run`, `make tui`, or `cereal-killer`) launches the Textual UI from your host shell.

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
- `/search` local synthesis over Redis-backed sources.
- `/sync-hacktricks` and `/sync-all` knowledge ingestion workflows.

## Configuration

Use environment variables (from `.env` in Docker or your shell locally):

- `REDIS_URL` (default `redis://localhost:6379`)
- `REDIS_INDEX` (default `ippsec_idx`)
- `LLM_BASE_URL` (default `http://host.docker.internal:8000/v1`)
- `LLM_MODEL` (default `qwen3.6`)
- `LLM_API_KEY` (default `not-needed`)
- `LLM_VISION_BASE_URL` (default `http://localhost:8000/v1`)
- `LLM_VISION_MODEL` (default empty, falls back to normal model path)
- `REASONING_PARSER` (default `qwen3`)
- `MAX_MODEL_LEN` (default `262144`)
- `SEARXNG_BASE_URL` (default `http://localhost:18080`)
- `SEARXNG_VECTOR_THRESHOLD` (default `0.7`)
- `SNARK_LEVEL` (default `8`)
- `LOOT_REPORT_DIR` (default `data/loot_reports`)

Docker compose also sets:

- `HISTORY_PATH` (default `/home/jabbatheduck/.zsh_history`)

Template file: `.env.example`

The default `.env.example` focuses on model settings. Redis and SearXNG defaults are already defined in application config and Make targets.

For external LLM hosts, set `LLM_BASE_URL` to a reachable IP or DNS name, for example `http://192.168.1.50:8000/v1`.

Runtime note:

- `make docker-build` rebuilds images only; it does not by itself change a currently running host process.
- `make` / `make run` / `make tui` load `.env` before launching, so updated values (like `LLM_BASE_URL`) are applied on next launch.

## Model Recommendations by VRAM

If you are using models from https://huggingface.co/HauhauCS/models, choose by available VRAM first.

These are practical targets for this app:

- 8 GB to 12 GB VRAM: prefer Geema4/Gemma-4 5B class models (for example `Gemma-4-E2B-Uncensored-HauhauCS-Aggressive`). This is the safest starting point when you need responsiveness over deep reasoning.
- 12 GB to 16 GB VRAM: prefer Geema4/Gemma-4 8B class models (for example `Gemma-4-E4B-Uncensored-HauhauCS-Aggressive`). Good balance for everyday box workflow coaching.
- 24 GB or more VRAM: prefer Qwen3.6 35B-A3B class models (for example `Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive`). Best option from this set when you want stronger reasoning and longer-context quality.

Notes:

- Start one tier lower if you see frequent OOM or heavy token latency.
- Keep `LLM_MODEL` in `.env` aligned with the model name your OpenAI-compatible server exposes.
- If your server supports quantized variants, lower-bit quantization can reduce VRAM usage at some quality cost.

## Usage

After launch, use the prompt input in the TUI and run commands in your shell as usual.

Recommended startup flow:

```bash
make docker-build
make docker-up-init
make
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
/vision              (analyze clipboard screenshot)
/upload <path>       (analyze screenshot from file path)
/search <query>      (search local Redis sources and synthesize)
/sync-hacktricks     (ingest HackTricks into Redis)
/sync-all            (refresh IppSec + configured sources from sources.yaml)
```

Keyboard shortcuts:

- `Ctrl+C`: quit
- `Ctrl+T`: toggle thinking panel
- `Ctrl+B`: Easy button pulse
- `U`: toggle screenshot upload panel

### Screenshot Upload Workflow

**Local Development:**
1. Copy screenshots to the `./screenshots/` directory in the cereal-killer repo
2. Press `U` to open the upload panel on the left
3. Click on an image file to analyze it with Zero Cool's vision capability

**Docker on Kali/Remote Host:**
1. Set the screenshots directory when starting Docker:
   ```bash
   SCREENSHOTS_DIR=/home/kali/Pictures make docker-up
   make
   ```
2. Copy screenshots to `/home/kali/Pictures` (or your chosen directory)
3. Press `U` to open the upload panel
4. Click on images from your Kali screenshots to analyze them

**Clipboard Integration:**
- Use `/vision` command or copy an image to your clipboard
- Zero Cool will analyze the clipboard image automatically when detected

## Example Workflow

```text
1) /new-box lame
2) Run recon commands in shell (nmap, dir enumeration, SMB checks, etc.)
3) Ask follow-up questions in the TUI prompt
4) /victory <summary of vuln + exploit path>
```

## Knowledge Sync (Redis Sources)

The app supports two sync paths:

- IppSec walkthrough dataset sync
- Multi-source library sync (HackTricks, GTFOBins, LOLBAS, PayloadsAllTheThings)

### 1) IppSec dataset sync

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

### 2) HackTricks-only sync

From inside the TUI:

```text
/sync-hacktricks
```

Optional custom local clone path:

```text
/sync-hacktricks /path/to/hacktricks
```

From Docker (outside the TUI):

```bash
docker compose run --rm --build app python -m mentor.kb.sync_command
```

### 3) Full multi-source sync

This refreshes IppSec first, then all configured repositories in `src/cereal_killer/kb/sources.yaml`.

From inside the TUI:

```text
/sync-all
```

From Docker (outside the TUI):

```bash
docker compose run --rm --build app python -m mentor.kb.sync_command sync-all
```

From a local Python environment:

```bash
PYTHONPATH=src python3 -m mentor.kb.sync_command sync-all
```

Configured source registry (default):

- HackTricks
- GTFOBins
- LOLBAS
- PayloadsAllTheThings
- IppSec (refreshed first by `sync-all`)

## Docker Commands

- `make docker-build`: build service images
- `make docker-up`: build and start Redis + SearXNG in the background
- `make docker-up-init`: run `make docker-up`, then run a full `sync-all` resync
- `make sync-all`: run full knowledge sync (`sync_all_command`) inside the app container
- `make sync-ippsec`: run IppSec-only sync inside the app container
- `make` / `make run` / `make tui`: launch the Textual app (uses local install if available, otherwise falls back to `docker compose run --rm --build app cereal-killer`)
- `make docker-down`: stop and remove the stack

These Make targets enable Docker BuildKit by default, so package download/build caches are reused across rebuilds for faster iteration.
`make docker-up` intentionally does not attach to the app container. This avoids compose log-prefix interference in fullscreen TUI rendering.

Redis persistence:

- Redis data is stored in a stable named Docker volume (`cereal-killer-redis-data`).
- This means `make docker-down`, `git pull`, and `make docker-build` do not wipe indexed data.
- To intentionally reset Redis data, run:

```bash
docker compose down -v
```

Quick verify:

```bash
docker volume ls | grep cereal-killer-redis-data
docker compose exec -T redis redis-cli FT._LIST
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
- `pyyaml`

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
- If history observation fails in Docker, verify `HISTORY_PATH` and host file permissions/UID-GID mapping.
- SearXNG config is mounted as a read-only file (`config/searxng/settings.yml`) to avoid container ownership/permission drift in the repo. This prevents common `git pull` and `git reset` failures caused by root or container UID rewrites on tracked files.
- If `sync-all` fails, verify outbound GitHub access and that Redis is reachable from the app container (`REDIS_URL`).

## Contributing

No dedicated contribution guide is currently present in this repository. If you plan to contribute, open an issue/PR with a clear summary and reproducible steps.

## License

Licensed under the terms in `LICENSE`.

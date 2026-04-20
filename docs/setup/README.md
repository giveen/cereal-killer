# Gibson Infrastructure Setup Guide

This guide prepares your local host for Gibson + Cereal Killer with llama-swap, Redis, and model artifacts.

## 1. Prerequisites

- Linux host with systemd
- Python 3.10+
- Docker + Docker Compose
- NVIDIA driver installed (for GPU inference)
- CUDA toolkit available (recommended)

Quick checks:

```bash
nvidia-smi
nvcc --version
docker --version
docker compose version
```

## 2. Local Model Layout

Create a model directory that contains:

- At least one `.gguf` model file
- At least one multimodal projector (`*mmproj*.gguf`)

Example:

```bash
mkdir -p /home/${USER}/models/gibson
ls -lah /home/${USER}/models/gibson
```

Expected files (example names):

- `Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf`
- `mmproj-Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-f16.gguf`

## 3. Generate Config Files Automatically

Use the config generator to create:

- llama-swap config: `config/llama-swap/config.yaml`
- Docker env file: `.env`

Run:

```bash
python scripts/setup/generate_config.py
./scripts/setup/gibson_check.sh /home/${USER}/models/gibson
```

The script now starts with `Use default settings? [y/n]`.

- `y` uses the project defaults and first auto-detected model artifacts where possible.
- `n` keeps the full interactive prompt flow.

It can also download the recommended HauhauCS Qwen3.6 model family directly from Hugging Face. The setup flow detects VRAM and recommends one of these quants:

- `IQ2_M` for smaller VRAM budgets
- `Q4_K_M` for 24 GB class GPUs
- `Q8_K_P` for 48 GB+ GPUs

The script still prompts if a required path cannot be auto-detected, such as a missing `llama-server` binary. After generation, run the Gibson preflight check with the same model directory shown above.

## 4. Deploy llama-swap

When you build llama-swap locally, the binary is usually platform-specific rather than a generic `llama-swap` filename. Example build output:

```bash
build/llama-swap-linux-amd64
build/llama-swap-linux-arm64
build/llama-swap-darwin-arm64
```

For Linux hosts, either:

- point your systemd unit `ExecStart` at the exact built binary, such as `build/llama-swap-linux-amd64`
- or install a stable symlink, for example `/usr/local/bin/llama-swap`, that points to the correct platform build

If llama-swap is installed as a systemd service, copy the generated config and restart:

```bash
sudo mkdir -p /etc/llama-swap
sudo cp config/llama-swap/config.yaml /etc/llama-swap/config.yaml
sudo ln -sf /path/to/llama-swap/build/llama-swap-linux-amd64 /usr/local/bin/llama-swap
sudo systemctl daemon-reload
sudo systemctl enable --now llama-swap
sudo systemctl restart llama-swap
sudo systemctl status llama-swap --no-pager
```

## 5. Start Infrastructure

From repo root:

```bash
make docker-up
```

This should start:

- Redis on `6379`
- SearXNG on `18080`

## 6. Run Gibson Check

Validate host readiness:

```bash
python scripts/setup/check_env.py --model-dir /home/${USER}/models/gibson
```

Or with helper wrapper:

```bash
./scripts/setup/gibson_check.sh /home/${USER}/models/gibson
```

Or run the standardized setup target:

```bash
make setup
```

The checker verifies:

- NVIDIA/CUDA presence
- `llama-swap` systemd service state
- Redis connectivity on `127.0.0.1:6379`
- required model artifacts (`.gguf` + `mmproj`)

It also appends each run to `logs/setup.log`.

## 7. Launch TUI

```bash
make run
```

Open Ops tab (`F2`) and look at status bar:

- `SETUP: ✓ READY` means startup checks passed
- `SETUP: ⚠ SETUP INCOMPLETE` means missing endpoint or data dirs

When incomplete, follow this guide and verify:

- `LLM_BASE_URL` points to llama-swap/OpenAI-compatible endpoint
- required local directories exist: `data/`, `screenshots/`, `config/`

## 8. Common Fixes

- llama-swap not active:

```bash
sudo systemctl restart llama-swap
sudo systemctl status llama-swap --no-pager
```

- Redis unavailable:

```bash
docker compose up -d redis
docker compose ps
```

- `.env` missing or stale:

```bash
python scripts/setup/generate_config.py
```

- No `mmproj` file detected:

```bash
find /home/${USER}/models -type f | rg -i 'mmproj.*\.gguf$'
```

For Redis and llama-swap error code triage, see:

- `docs/setup/TROUBLESHOOTING.md`

# Setup Troubleshooting: Redis and llama-swap

This page is focused on common Redis and llama-swap failure states and how to resolve them quickly.

## Redis Error Codes and Symptoms

### ECONNREFUSED (Connection refused)

Symptoms:

- checker reports Redis unavailable at 127.0.0.1:6379
- app fails to query or sync KB

Causes:

- Redis container/service not running
- Wrong host or port

Fix:

```bash
docker compose up -d redis
docker compose ps
ss -ltnp | rg 6379
```

### NOAUTH Authentication required

Symptoms:

- redis-cli commands fail with NOAUTH

Causes:

- Redis is configured with password but REDIS_URL has no credentials

Fix:

- update REDIS_URL in .env to include password
- verify with:

```bash
redis-cli -h 127.0.0.1 -p 6379 ping
```

### WRONGTYPE Operation against a key holding the wrong kind of value

Symptoms:

- sync/index calls fail on existing keys

Causes:

- stale keys with incompatible schema

Fix:

- purge affected dataset and re-sync:

```bash
make sync-all
```

### OOM command not allowed when used memory > 'maxmemory'

Symptoms:

- writes/index updates fail intermittently

Causes:

- Redis memory cap reached

Fix:

- increase Redis memory limit
- delete old data and re-ingest

## llama-swap Error Codes and Symptoms

### systemctl is-active llama-swap -> inactive/failed

Symptoms:

- setup check reports llama-swap not active
- LLM endpoint appears unavailable

Fix:

```bash
sudo systemctl restart llama-swap
sudo systemctl status llama-swap --no-pager
journalctl -u llama-swap -n 100 --no-pager
```

If the service fails immediately after a local build, verify that `ExecStart` points to the real platform binary produced by llama-swap, for example `llama-swap-linux-amd64`, not a guessed filename.

### HTTP 404 /models

Symptoms:

- readiness check flags llama-swap missing

Causes:

- wrong LLM_BASE_URL path
- endpoint not OpenAI-compatible

Fix:

- verify URL in .env
- expected OpenAI-compatible base, e.g.:

```bash
LLM_BASE_URL=http://host.docker.internal:8000/v1
```

- probe manually:

```bash
curl -sS http://host.docker.internal:8000/v1/models | head
```

### HTTP 500 from /models or generation endpoints

Symptoms:

- setup incomplete status persists
- requests fail after startup

Causes:

- invalid config.yaml
- missing model/mmproj mapping
- insufficient GPU/VRAM

Fix:

- regenerate config and confirm mmproj path:

```bash
python scripts/setup/generate_config.py
```

- validate service logs:

```bash
journalctl -u llama-swap -n 200 --no-pager
```

### Model loads but vision fails

Symptoms:

- text works, image analysis fails

Causes:

- missing or mismatched mmproj file

Fix:

- ensure model + projector family match (for Qwen-VL)
- rerun generator and select matching mmproj

## Quick Recovery Sequence

```bash
python scripts/setup/generate_config.py
bash scripts/setup/gibson_check.sh
make docker-up
make run
```

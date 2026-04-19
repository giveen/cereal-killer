# cereal-killer

Modular Python 3.12+ TUI app using Textual (UI), watchfiles (Observer), AsyncOpenAI (LLM engine), and RedisVL (knowledge base).

## Architecture

- `src/cereal_killer/ui.py`: MVC UI layer (Header/Footer/Chat/Sidebar, Easy Button modal, reasoning collapsible, prompt input)
- `src/cereal_killer/observer.py`: async shell history observer with OS fallbacks and context filtering
- `src/cereal_killer/engine.py`: local OpenAI-compatible chat wrapper for Qwen 3.6 persona
- `src/cereal_killer/knowledge_base.py`: RedisVL index + ippsec.rocks sync + walkthrough retrieval

## Local run

```bash
python -m pip install -e .
python -m cereal_killer.main
```

## Sync ippsec dataset into Redis

```bash
python scripts/sync_ippsec.py
```

## Docker compose

```bash
docker compose up --build
```

Set `LLM_BASE_URL` to your host-managed llama.cpp/sglang OpenAI-compatible endpoint.

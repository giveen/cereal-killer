.PHONY: docker-build docker-up docker-down tui sync-ippsec

DOCKER_BUILDKIT ?= 1
COMPOSE_DOCKER_CLI_BUILD ?= 1

export DOCKER_BUILDKIT
export COMPOSE_DOCKER_CLI_BUILD

docker-build:
	docker compose build

docker-up:
	docker compose up -d --build redis searxng

tui:
	@if command -v cereal-killer >/dev/null 2>&1; then \
		REDIS_URL="$${REDIS_URL:-redis://localhost:6379}" \
		SEARXNG_BASE_URL="$${SEARXNG_BASE_URL:-http://localhost:18080}" \
		LLM_BASE_URL="$${LLM_BASE_URL:-http://localhost:8000/v1}" \
		cereal-killer; \
	elif [ -x .venv/bin/cereal-killer ]; then \
		REDIS_URL="$${REDIS_URL:-redis://localhost:6379}" \
		SEARXNG_BASE_URL="$${SEARXNG_BASE_URL:-http://localhost:18080}" \
		LLM_BASE_URL="$${LLM_BASE_URL:-http://localhost:8000/v1}" \
		.venv/bin/cereal-killer; \
	else \
		echo "No local cereal-killer install found. Launching via Docker app service..."; \
		docker compose run --rm --build app cereal-killer; \
	fi

docker-down:
	docker compose down

sync-ippsec:
	docker compose run --rm --build app python scripts/sync_ippsec.py

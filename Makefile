.PHONY: docker-build docker-up docker-up-init docker-down tui run start sync-ippsec sync-all setup check check-env

.DEFAULT_GOAL := run

DOCKER_BUILDKIT ?= 1
COMPOSE_DOCKER_CLI_BUILD ?= 1

export DOCKER_BUILDKIT
export COMPOSE_DOCKER_CLI_BUILD

docker-build:
	docker compose build

docker-up:
	docker compose up -d

setup:
	@echo "Generating local setup config..."
	python scripts/setup/generate_config.py
	@echo "Running Gibson setup checks..."
	set -a; [ -f .env ] && . ./.env; set +a; \
	bash ./scripts/setup/gibson_check.sh

check:
	set -a; [ -f .env ] && . ./.env; set +a; \
	bash ./scripts/setup/gibson_check.sh

check-env:
	$(MAKE) check

docker-up-init: docker-up
	@echo "Running full knowledge sync (IppSec + configured sources)..."
	$(MAKE) sync-all

tui:
	@if command -v cereal-killer >/dev/null 2>&1; then \
		set -a; [ -f .env ] && . ./.env; set +a; \
		REDIS_URL="$${REDIS_URL:-redis://localhost:6379}" \
		SEARXNG_BASE_URL="$${SEARXNG_BASE_URL:-http://localhost:18080}" \
		LLM_BASE_URL="$${LLM_BASE_URL:-http://localhost:8000/v1}" \
		cereal-killer; \
	elif [ -x .venv/bin/cereal-killer ]; then \
		set -a; [ -f .env ] && . ./.env; set +a; \
		REDIS_URL="$${REDIS_URL:-redis://localhost:6379}" \
		SEARXNG_BASE_URL="$${SEARXNG_BASE_URL:-http://localhost:18080}" \
		LLM_BASE_URL="$${LLM_BASE_URL:-http://localhost:8000/v1}" \
		.venv/bin/cereal-killer; \
	else \
		echo "No local cereal-killer install found. Launching via Docker app service..."; \
		docker compose run --rm --build app cereal-killer; \
	fi

run: check tui

start: tui

docker-down:
	docker compose down --remove-orphans

sync-ippsec:
	docker compose run --rm --build app python scripts/sync_ippsec.py

sync-all:
	docker compose run --rm --build app python -m mentor.kb.sync_command sync-all

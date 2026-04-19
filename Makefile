.PHONY: docker-build docker-up docker-down

DOCKER_BUILDKIT ?= 1
COMPOSE_DOCKER_CLI_BUILD ?= 1

export DOCKER_BUILDKIT
export COMPOSE_DOCKER_CLI_BUILD

docker-build:
	docker compose build

docker-up:
	docker compose up --build

docker-down:
	docker compose down

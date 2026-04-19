# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        xclip \
        wl-clipboard \
    && rm -rf /var/lib/apt/lists/*
    

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY scripts /app/scripts

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && pip install .

CMD ["cereal-killer"]

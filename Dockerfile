# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

# Support running as a non-root user matching host UID/GID
ARG USER_ID=1000
ARG GROUP_ID=1000

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    USER_ID=${USER_ID} \
    GROUP_ID=${GROUP_ID}

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        xclip \
        wl-clipboard \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g ${GROUP_ID} appuser || true \
    && useradd -u ${USER_ID} -g ${GROUP_ID} -m -s /bin/bash appuser || true
    

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY scripts /app/scripts

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && pip install . \
    && python - <<'PY'
import PIL
import multipart
print("vision deps ok")
PY

# Set ownership of /app to the non-root user
RUN chown -R ${USER_ID}:${GROUP_ID} /app

# Ensure the logs directory exists and is writable by the non-root user.
RUN mkdir -p /app/logs && chown ${USER_ID}:${GROUP_ID} /app/logs

USER appuser

CMD ["cereal-killer"]

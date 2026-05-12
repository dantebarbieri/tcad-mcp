# syntax=docker/dockerfile:1.7
FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install build deps separately so the layer caches when only sources change.
COPY pyproject.toml README.md LICENSE ./
COPY tcad_mcp ./tcad_mcp

RUN pip install --no-cache-dir .

EXPOSE 8080

# Drop privileges. Match the homeserver UID/GID convention via build args
# if needed; defaults to 1000 which lines up with most LinuxServer.io/Hotio
# setups.
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd --system --gid ${APP_GID} app \
    && useradd --system --uid ${APP_UID} --gid app --no-create-home --shell /usr/sbin/nologin app
USER app

# Health probe: TCP connect to the listen port — same shape as the other MCP
# servers in github.com/dantebarbieri/homeserver so docker compose healthchecks
# stay consistent across the fleet.
HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import socket; socket.create_connection(('localhost', 8080), timeout=3).close()"

CMD ["uvicorn", "tcad_mcp:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]

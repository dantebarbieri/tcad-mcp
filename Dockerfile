# syntax=docker/dockerfile:1.7

# ---- Builder ---------------------------------------------------------------
# Use the official uv image so we don't pay the cost of installing it. Tags
# of the form `uv:<UV_VER>-python<PY_VER>-trixie-slim` give us a reproducible
# uv + interpreter pair without resolving Python at build time. Pinned to a
# minor (0.11.x) so security/patch updates flow but breaking uv changes don't.
# Astral publishes derived images on `trixie-slim` (not bookworm) — see
# https://docs.astral.sh/uv/guides/integration/docker/#available-images.
FROM ghcr.io/astral-sh/uv:0.11-python3.14-trixie-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_PROGRESS=1

WORKDIR /app

# Install only the runtime dependencies first, with the lockfile as the only
# bind input. This layer's cache key is `(uv.lock, pyproject.toml)` — sources
# don't invalidate it, so 99% of source-only rebuilds skip the network entirely.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=README.md,target=README.md \
    --mount=type=bind,source=LICENSE,target=LICENSE \
    uv sync --locked --no-dev --no-install-project

# Now bring in sources and install the project itself (still no dev extras).
COPY pyproject.toml README.md LICENSE ./
COPY tcad_mcp ./tcad_mcp
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# ---- Runtime ---------------------------------------------------------------
# Bare Python on the SAME Debian release (trixie) as the builder so the
# venv's interpreter symlink and shared libraries line up exactly. Without
# uv at runtime, the final image is smaller and the attack surface minimal.
FROM python:3.14-slim-trixie AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

# Copy the resolved venv and the project sources from the builder. The venv
# is fully self-contained (uv compiled bytecode + copied wheels), so no extra
# `pip install` step is needed at runtime.
COPY --from=builder /app/.venv /app/.venv
COPY tcad_mcp ./tcad_mcp

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

CMD ["python", "-m", "tcad_mcp"]

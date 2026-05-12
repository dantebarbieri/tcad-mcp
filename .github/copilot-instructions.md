# Copilot instructions — tcad-mcp

This is a small standalone MCP server that wraps the public TrueProdigy
appraisal-district SaaS API. It's office-agnostic by design: anything an
operator might want to change is an environment variable, not a code change.

## Layout

- `tcad_mcp/__init__.py` — exports the production ASGI `app` lazily via PEP
  562 (Dockerfile uses `uvicorn tcad_mcp:app`).
- `tcad_mcp/__main__.py` — `python -m tcad_mcp` dev entry point.
- `tcad_mcp/server.py` — `create_app()` factory + FastMCP tool definitions +
  `_UpstreamClient` (TrueProdigy JWT cache, year cache, retry-on-401).
  **Named `server.py`, not `app.py`**, so it doesn't shadow the lazy `app`
  attribute exposed by `__init__.py`.
- `tcad_mcp/config.py` — `AppConfig.from_env()` loads `AUTH_TOKEN_FILE` /
  `AUTH_TOKEN`, `TCAD_UPSTREAM_URL`, `TCAD_OFFICE`, `TCAD_HTTP_TIMEOUT`.
- `tcad_mcp/shapers.py` — pure JSON-shaping helpers (no I/O, no env). The
  test suite exercises this module directly.
- `tests/` — pytest, asyncio mode `auto`. `conftest.py` provides the
  `auth_env` fixture for tests that import the package itself.

## Conventions

- **No IdP-specific code or config.** v0.2.0 grows OAuth 2.1 support via
  generic OIDC discovery (issuer URL only). Do not add `AUTHELIA_*`,
  `KEYCLOAK_*`, etc. environment variables.
- **Behavior parity with the homeserver carve-out is sacrosanct.** v0.1.0 is
  intended to be a 1:1 port — when in doubt, match the existing
  `homeserver/docker/dockerfiles/mcp-tcad/app.py`.
- **All env access goes through `AppConfig`.** Don't sprinkle
  `os.environ.get(...)` calls across modules — extend the dataclass.
- **Pure helpers in `shapers.py`, side-effect helpers (httpx) in `server.py`.**
  This split is what lets the test suite skip JWKS / httpx / FastMCP
  bootstrapping for the bulk of the unit tests.
- Match the parent repo's docstring style — module + function docstrings,
  no inline comments unless they document a non-obvious upstream quirk
  (e.g. the `formatlDate` typo in TCAD's `/general` response).

## CI / release

- `ci.yml` — ruff + pytest matrix on Python 3.11/3.12/3.13, plus docker build
  (no push).
- `release.yml` — tag-triggered (`v*.*.*`) multi-arch build (amd64 + arm64)
  pushed to `ghcr.io/dantebarbieri/tcad-mcp` with semver + `latest` tags.
- Bump version in `pyproject.toml` first, then `git tag vX.Y.Z` and push.

## Out of scope for this repo

- Authelia configuration (lives in the homeserver repo's runbook).
- The home-scout enrichment pipeline that consumes this MCP.
- Any TCAD-office-specific defaults beyond `TCAD_OFFICE`.

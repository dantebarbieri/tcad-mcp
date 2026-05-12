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
- `tcad_mcp/config.py` — `AppConfig.from_env()` builds `BearerConfig` +
  `OAuthConfig`. Generic `_load_secret(env_var)` helper implements the
  standard Docker `ENV_VAR` / `ENV_VAR_FILE` pattern. `_parse_tristate`
  handles the `BEARER_AUTH_ENABLED` / `OAUTH_AUTH_ENABLED` three-state
  envs (unset / true / false).
- `tcad_mcp/auth.py` — `BearerOrOAuthMiddleware` (bearer + OIDC JWT) and
  `make_protected_resource_metadata` (RFC 9728 well-known endpoint).
  `_OIDCMetadata` does discovery + JWKS caching with `asyncio.Lock`
  singleflight + negative-cache backoff.
- `tcad_mcp/shapers.py` — pure JSON-shaping helpers (no I/O, no env). The
  test suite exercises this module directly.
- `tests/` — pytest, asyncio mode `auto`. 128 tests across:
  `test_address_normalization.py`, `test_search_ladder.py`,
  `test_field_shapers.py`, `test_auth.py`, `test_config.py`.
  `conftest.py` provides the `auth_env` fixture for tests that import
  the package itself.

## Conventions

- **No IdP-specific code or config.** OAuth support is via generic OIDC
  discovery — only the *value* of `OAUTH_ISSUER` names the IdP. Do not add
  `AUTHELIA_*`, `KEYCLOAK_*`, etc. environment variables. A pre-tag grep
  gate enforces this (no IdP names in `tcad_mcp/` or `tests/`).
- **All env access goes through `AppConfig`.** Don't sprinkle
  `os.environ.get(...)` calls across modules — extend the dataclass.
- **Long-lived secrets follow the Docker `_FILE` convention.** Use
  `_load_secret("ENV_VAR")` instead of reading `os.environ` directly.
  Direct env var beats `ENV_VAR_FILE` if both are set.
- **Pure helpers in `shapers.py`, side-effect helpers (httpx) in `server.py`.**
  This split is what lets the test suite skip JWKS / httpx / FastMCP
  bootstrapping for the bulk of the unit tests.
- **Three-state auth toggles.** `BEARER_AUTH_ENABLED` and
  `OAUTH_AUTH_ENABLED` accept `true/false/yes/no/on/off/1/0` (case
  insensitive); unset means "auto-detect from config presence". Typos
  fail-fast in `_parse_tristate`.
- **Open-server mode is allowed.** When both modes resolve to disabled,
  the middleware passes everything through. `AppConfig.from_env` prints
  a stderr warning so operators see it. Do not regress this behavior to
  fail-fast; some operators want a fully-open server on a trusted LAN.
- Match the parent repo's docstring style — module + function docstrings,
  no inline comments unless they document a non-obvious upstream quirk
  (e.g. the `formatlDate` typo in TCAD's `/general` response).

## Security notes (don't regress)

These are all enforced by tests in `test_auth.py` and `test_config.py`:

- JWT validation requires explicit asymmetric `algorithms` allowlist
  (RS/PS/ES/EdDSA) — no symmetric algorithms accepted.
- Audience required when OAuth is enabled (`OAUTH_AUDIENCE` or
  `RESOURCE_URL`). Confused-deputy hole otherwise (RFC 9728 §7.4).
- Discovery doc's `issuer` claim is verified to match configured issuer
  (OIDC §4.3) — blocks discovery-mirror tampering.
- `exp` must be a finite number (no NaN/Infinity bypass); `nbf` checked
  if present; both with 60s clock skew.
- `WWW-Authenticate` carries `resource_metadata="<URL>"` per
  draft-ietf-oauth-resource-metadata — never embeds the issuer URL
  directly (that would leak the IdP into the response).
- `/.well-known/oauth-protected-resource` returns 404 in bearer-only or
  open mode (RFC 9728 §3.2 forbids zero-value `authorization_servers`).
- JWT-shape pre-check rejects garbage tokens before any network fetch
  (kills the bearer-as-DoS-amplifier class of attack).
- Static bearer compared with `hmac.compare_digest`.
- Forwarded host validated against `_SAFE_HOST_RE` to prevent
  metadata-URL poisoning from direct connections.

## CI / release

- `ci.yml` — ruff + pytest matrix on Python 3.11/3.12/3.13, plus docker build
  (no push).
- `release.yml` — tag-triggered (`v*.*.*`) multi-arch build (amd64 + arm64)
  pushed to `ghcr.io/dantebarbieri/tcad-mcp` with semver + `latest` tags.
- Bump version in `pyproject.toml` first, then `git tag vX.Y.Z` and push.

## Out of scope for this repo

- Authelia (or any IdP-specific) configuration — lives in the homeserver
  repo's runbook at `docker/docs/MCP-OAUTH.md`.
- Client setup procedures (Claude.ai / Cursor / Open WebUI / etc.) —
  documented in `docs/CLIENTS.md`.
- The home-scout enrichment pipeline that consumes this MCP.
- Any TCAD-office-specific defaults beyond `TCAD_OFFICE`.


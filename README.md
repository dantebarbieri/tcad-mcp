# tcad-mcp

A small [Model Context Protocol](https://modelcontextprotocol.io) server that
wraps the **Travis Central Appraisal District** public portal (and any other
TCAD office that runs on TrueProdigy's SaaS backend) so AI agents can pull
structured property data — subdivision, year built, lot size, school
district, value history, deed history, protest status — from a single
authenticated HTTP endpoint.

> Status: **alpha**. v0.3.0 adds independent enable/disable for each auth
> mode and the standard Docker `_FILE` secret convention across the board.

## What it does

Exposes nine tools over MCP's Streamable HTTP transport:

| Tool | What it returns |
|---|---|
| `search_property(address, limit)` | Address-based search with deterministic 4-step fallback ladder; tells you which strategy hit so callers can flag non-canonical matches. |
| `get_property_general(account_id)` | Legal description, owner, exemptions, agent, deferral, audit year. |
| `get_property_values(account_id)` | Current-year values + 5-year history (parallel `/value` + `/valuehistory`). |
| `get_full_value_history(pid)` | All historical years for a pid (uses the pid-only search trick — covers years before the 5-year window). |
| `get_property_land(account_id)` | Lot size (sqft + acres), cost/sqft, market value, land type. |
| `get_property_improvements(account_id)` | Year built, living area, components + per-component features (parallel feature fetches). |
| `get_property_taxing_units(account_id)` | Per-unit breakdown + aggregate totals + derived `school_district` (RRISD vs AISD vs etc.). |
| `get_protest_information(account_id)` | Protest/appeal status, opinion-of-value, board determination. |
| `get_property_deed_history(pid)` | Full deed/sale history sorted ascending by date. |

The full design rationale (TCAD endpoint shapes, the `pAccountID` vs `pid`
distinction, the JWT auth flow, the 5-year-window quirk, the address
fallback ladder, etc.) is documented in the homeserver repo's design spec:
[`homeserver/docs/superpowers/specs/2026-05-12-mcp-tcad-design.md`](https://github.com/dantebarbieri/homeserver/blob/main/docs/superpowers/specs/2026-05-12-mcp-tcad-design.md).

## Authentication

Three user-facing auth modes, each independently enabled/disabled. A request
is allowed if it satisfies **any** enabled mode.

### Mode 1: Bearer (always-available shared secret)

A single static secret loaded from `AUTH_TOKEN` (env var) or `AUTH_TOKEN_FILE`
(path to a file — standard Docker secret pattern, same as `POSTGRES_PASSWORD`
/ `POSTGRES_PASSWORD_FILE`).

```http
Authorization: Bearer <your-static-token>
```

Use this for CLI clients, scripted callers, MCP clients that don't speak
OAuth (e.g., the current Open WebUI integration), or as a **fallback path**
during OAuth rollout — both modes work in parallel.

Auto-enabled iff `AUTH_TOKEN(_FILE)` is set; force-disable with
`BEARER_AUTH_ENABLED=false`; require with `BEARER_AUTH_ENABLED=true` (boots
fail-fast if no token is set).

### Mode 2: OAuth (manual client_credentials)

Operator pre-configures an OAuth client at the IdP (Authelia / Keycloak /
Auth0 / Okta / Dex / Authentik / Zitadel / etc.), hands the
`client_id` + `client_secret` to the consumer. The consumer does:

```http
POST <token_endpoint>
grant_type=client_credentials&client_id=...&client_secret=...&scope=mcp:tcad&audience=...
```

…then calls the MCP server with the resulting access token. The server
validates the JWT against the issuer's discovered JWKS — no IdP-specific
code anywhere; only the *value* of `OAUTH_ISSUER` names the IdP.

Use this for machine-to-machine consumers (cron jobs, CI pipelines,
internal agents) where you want auditable per-client credentials without
end-user consent friction.

### Mode 3: Automatic (Claude.ai discovery + DCR)

Same server code path as Mode 2 — Claude.ai (or any MCP client following
the [MCP authorization spec][mcp-auth] + [draft-ietf-oauth-resource-metadata][prm])
discovers the IdP automatically:

1. The user pastes just the MCP server URL into Claude.ai.
2. Claude hits a protected endpoint → 401 with `WWW-Authenticate: Bearer
   resource_metadata="<URL>"`.
3. Claude `GET`s the metadata URL → learns the issuer.
4. Claude `GET`s the issuer's `/.well-known/openid-configuration` → learns
   the registration + token endpoints.
5. Claude registers itself dynamically (DCR), runs the user through the
   `authorization_code` consent flow, and stores the resulting tokens.
6. Subsequent requests carry `Authorization: Bearer <JWT>`.

Requires the IdP to support **Dynamic Client Registration** and
`authorization_code` grant. Server-side this is the same OAuth mode as
above — the "automatic" experience is purely a Claude-side workflow on
top of the discovery endpoints the server publishes.

[mcp-auth]: https://modelcontextprotocol.io/specification/draft/basic/authorization
[prm]: https://datatracker.ietf.org/doc/draft-ietf-oauth-resource-metadata/

### Disabling auth entirely

If both `BEARER_AUTH_ENABLED=false` **and** `OAUTH_AUTH_ENABLED=false`
(or neither config is set), the server boots with **no authentication**.
A one-line warning is printed to stderr at startup. Suitable only for
trusted-LAN deployments behind a network ACL.

## Quickstart (Docker, bearer-only)

```bash
docker run --rm -p 8080:8080 \
  -e AUTH_TOKEN="$(openssl rand -hex 32)" \
  ghcr.io/dantebarbieri/tcad-mcp:latest
```

## Quickstart (Docker, OAuth + bearer fallback)

```bash
docker run --rm -p 8080:8080 \
  -e AUTH_TOKEN="$(openssl rand -hex 32)" \
  -e OAUTH_ISSUER="https://your-idp.example" \
  -e OAUTH_AUDIENCE="https://mcp-tcad.example" \
  -e RESOURCE_URL="https://mcp-tcad.example" \
  -e OAUTH_REQUIRED_SCOPE="mcp:tcad" \
  ghcr.io/dantebarbieri/tcad-mcp:latest
```

Then point Claude.ai at `https://mcp-tcad.example` (Mode 3) and complete
the consent flow — or hand `client_id`/`client_secret` to your OpenClaw /
script (Mode 2) — or use the static `AUTH_TOKEN` for CLI calls (Mode 1).
All three work at once.

## Quickstart (Docker, OAuth-only)

```bash
docker run --rm -p 8080:8080 \
  -e BEARER_AUTH_ENABLED=false \
  -e OAUTH_ISSUER="https://your-idp.example" \
  -e OAUTH_AUDIENCE="https://mcp-tcad.example" \
  -e RESOURCE_URL="https://mcp-tcad.example" \
  -e OAUTH_REQUIRED_SCOPE="mcp:tcad" \
  ghcr.io/dantebarbieri/tcad-mcp:latest
```

## Configuration

### Auth mode toggles

| Variable | Default | Effect |
|---|---|---|
| `BEARER_AUTH_ENABLED` | unset (auto) | `unset` → enabled iff `AUTH_TOKEN(_FILE)` is set. `true` → require it (fail at startup if not set). `false` → force-disable even when set. Accepts `true/false/yes/no/on/off/1/0` (case-insensitive). |
| `OAUTH_AUTH_ENABLED` | unset (auto) | Same three-state semantics, keyed off `OAUTH_ISSUER`. |

### Bearer mode

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `AUTH_TOKEN` | one of | — | Static bearer token (direct value). |
| `AUTH_TOKEN_FILE` | one of | — | Path to a file containing the token (Docker secret pattern). |

Direct env var wins over `_FILE` if both are set.

### OAuth mode

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OAUTH_ISSUER` | when OAuth enabled | — | OIDC issuer URL. |
| `OAUTH_AUDIENCE` | when OAuth enabled | falls back to `RESOURCE_URL` | Required JWT `aud` claim. Server fails at startup if this and `RESOURCE_URL` are both unset while OAuth is enabled — without an audience, the server would accept any token from this issuer regardless of which resource it was minted for ([RFC 9728 §7.4](https://www.rfc-editor.org/rfc/rfc9728#section-7.4)). |
| `RESOURCE_URL` | recommended in production | derived from `Host` header | Externally-visible URL of this server. Set this when behind a reverse proxy. |
| `OAUTH_REQUIRED_SCOPE` | no | unset (no scope check) | Scope required in the token (matches `scope` string OR `scp` array claims). |
| `OAUTH_JWKS_URL` | no | derived from discovery | Override for IdPs that don't ship `/.well-known/openid-configuration`. |
| `OAUTH_DISCOVERY_TTL` | no | `3600` | OIDC discovery doc cache TTL (seconds). |
| `OAUTH_JWKS_TTL` | no | `3600` | JWKS cache TTL (seconds). Lower this for faster key-rotation pickup. |

### Upstream + transport

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `TCAD_UPSTREAM_URL` | no | `https://prod-container.trueprodigyapi.com` | TrueProdigy base URL. |
| `TCAD_OFFICE` | no | `Travis` | Office string sent to the auth endpoint. Try `Williamson`, `Hays`, etc. for other Texas counties on TrueProdigy. |
| `TCAD_HTTP_TIMEOUT` | no | `20` | httpx timeout in seconds. |

### Secret loading convention

Long-lived secrets (currently just `AUTH_TOKEN`) accept either form:

- `AUTH_TOKEN=<value>` — direct value
- `AUTH_TOKEN_FILE=/run/secrets/mytoken` — path to a file (Docker secret)

This matches the pattern used by `postgres`, `mysql`, and most other
official Docker images. Future secret-bearing env vars will follow the
same `<NAME>` / `<NAME>_FILE` convention.

## Develop

```bash
git clone https://github.com/dantebarbieri/tcad-mcp.git
cd tcad-mcp
python -m venv .venv && . .venv/bin/activate    # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
pytest        # 128 tests
ruff check .
```

Run the dev server (bearer-only):

```bash
AUTH_TOKEN=dev python -m tcad_mcp
```

## Roadmap

- Pluggable upstream wrapper so non-TrueProdigy CADs (HCAD, DCAD, etc.)
  can ship as separate adapters in the same package.
- Optional response cache (currently the consumer is expected to cache).

## License

[MIT](LICENSE).



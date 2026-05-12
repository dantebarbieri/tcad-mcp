# tcad-mcp

A small [Model Context Protocol](https://modelcontextprotocol.io) server that
wraps the **Travis Central Appraisal District** public portal (and any other
TCAD office that runs on TrueProdigy's SaaS backend) so AI agents can pull
structured property data — subdivision, year built, lot size, school
district, value history, deed history, protest status — from a single
authenticated HTTP endpoint.

> Status: **alpha**. v0.2.0 adds standard OAuth 2.1 / OIDC support so it
> works as a remote MCP for [Claude.ai](https://claude.ai) and any other
> client that follows the [MCP authorization
> spec](https://modelcontextprotocol.io/specification/draft/basic/authorization).
> The static-bearer path from v0.1.0 stays as a fallback.

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

The server accepts **either** of two auth paths in parallel:

### 1. Static bearer (always available)

A single shared secret loaded from `AUTH_TOKEN` or `AUTH_TOKEN_FILE`. Used
by CLI clients, scripted callers, and clients (like the current Open WebUI
MCP integration) that don't speak OAuth yet.

```http
Authorization: Bearer <your-static-token>
```

### 2. OAuth 2.1 / OIDC (optional; enabled by setting `OAUTH_ISSUER`)

For Claude.ai's remote-MCP integration and any other client that follows
the [MCP authorization
spec](https://modelcontextprotocol.io/specification/draft/basic/authorization)
plus [draft-ietf-oauth-resource-metadata][prm].

[prm]: https://datatracker.ietf.org/doc/draft-ietf-oauth-resource-metadata/

The server is **IdP-agnostic** — it speaks standard OIDC discovery, no
vendor-specific code. Verified working with:

- [Authelia](https://www.authelia.com/) (≥ 4.39 for Dynamic Client Registration)
- [Keycloak](https://www.keycloak.org/)
- [Auth0](https://auth0.com/)
- [Okta](https://www.okta.com/)
- [Dex](https://dexidp.io/)
- [Authentik](https://goauthentik.io/)
- [Zitadel](https://zitadel.com/)

The flow Claude.ai follows automatically:

1. Client sends a request without OAuth credentials → server returns 401
   with `WWW-Authenticate: Bearer resource_metadata="<URL>"`.
2. Client `GET`s the `<URL>` (resource metadata) → learns the issuer.
3. Client `GET`s the issuer's `/.well-known/openid-configuration` → learns
   the token, registration, and authorization endpoints.
4. Client registers itself dynamically (DCR), runs the user through the
   authorization-code consent flow, and stores the resulting tokens.
5. Subsequent requests carry `Authorization: Bearer <JWT>`. The server
   validates signature against the issuer's JWKS, plus `iss` / `aud` /
   `exp` / optional scope.

Both `client_credentials` (for machine-to-machine clients like cron jobs)
and `authorization_code` + DCR (for end users via Claude.ai) work — which
the IdP supports is up to the operator's IdP config, not anything the MCP
server cares about.

## Quickstart (Docker, bearer-only)

```bash
docker run --rm -p 8080:8080 \
  -e AUTH_TOKEN="$(openssl rand -hex 32)" \
  ghcr.io/dantebarbieri/tcad-mcp:latest
```

## Quickstart (Docker, with OAuth)

```bash
docker run --rm -p 8080:8080 \
  -e AUTH_TOKEN="$(openssl rand -hex 32)" \
  -e OAUTH_ISSUER="https://your-idp.example" \
  -e OAUTH_AUDIENCE="https://mcp-tcad.example" \
  -e RESOURCE_URL="https://mcp-tcad.example" \
  -e OAUTH_REQUIRED_SCOPE="mcp:tcad" \
  ghcr.io/dantebarbieri/tcad-mcp:latest
```

Then point Claude.ai at `https://mcp-tcad.example` and complete the
consent flow.

## Configuration

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `AUTH_TOKEN_FILE` | one of | — | Path to a file containing the static bearer token. |
| `AUTH_TOKEN` | one of | — | Static bearer token directly. |
| `TCAD_UPSTREAM_URL` | no | `https://prod-container.trueprodigyapi.com` | TrueProdigy base URL. |
| `TCAD_OFFICE` | no | `Travis` | Office string sent to the auth endpoint. Try `Williamson`, `Hays`, etc. for other Texas counties on TrueProdigy. |
| `TCAD_HTTP_TIMEOUT` | no | `20` | httpx timeout in seconds. |
| `OAUTH_ISSUER` | no | unset (bearer-only) | OIDC issuer URL. **Enables the OAuth path when set.** |
| `OAUTH_AUDIENCE` | when OAuth enabled | falls back to `RESOURCE_URL` | Required JWT `aud` claim. Server fails at startup if this and `RESOURCE_URL` are both unset while `OAUTH_ISSUER` is set — without an audience, the server would accept any token from this issuer regardless of which resource it was minted for. |
| `RESOURCE_URL` | recommended in production | derived from `Host` header | Externally-visible URL of this server. Set this when behind a reverse proxy. |
| `OAUTH_REQUIRED_SCOPE` | no | unset (no scope check) | Scope required in the token (matches `scope` string OR `scp` array claims). |
| `OAUTH_JWKS_URL` | no | derived from discovery | Override for IdPs that don't ship `/.well-known/openid-configuration`. |
| `OAUTH_DISCOVERY_TTL` | no | `3600` | OIDC discovery doc cache TTL (seconds). |
| `OAUTH_JWKS_TTL` | no | `3600` | JWKS cache TTL (seconds). Lower this for faster key-rotation pickup. |

## Develop

```bash
git clone https://github.com/dantebarbieri/tcad-mcp.git
cd tcad-mcp
python -m venv .venv && . .venv/bin/activate    # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
pytest        # 88 tests
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


# tcad-mcp

A small [Model Context Protocol](https://modelcontextprotocol.io) server that
wraps the **Travis Central Appraisal District** public portal (and any other
TCAD office that runs on TrueProdigy's SaaS backend) so AI agents can pull
structured property data — subdivision, year built, lot size, school
district, value history, deed history, protest status — from a single
authenticated HTTP endpoint.

> Status: **alpha**. v0.1.0 is a 1:1 carve-out from a personal homeserver
> stack with bearer-only auth. v0.2.0 (in progress) adds standard OAuth 2.1
> support so it works as a remote MCP for [Claude.ai](https://claude.ai)
> and any other client that follows the [MCP authorization
> spec](https://modelcontextprotocol.io/specification/draft/basic/authorization).

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

## Quickstart (Docker)

```bash
docker run --rm -p 8080:8080 \
  -e AUTH_TOKEN="$(openssl rand -hex 32)" \
  ghcr.io/dantebarbieri/tcad-mcp:latest
```

Then point any MCP client at `http://localhost:8080` with that bearer token.

## Configuration

All knobs are environment variables:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `AUTH_TOKEN_FILE` | one of | — | Path to a file containing the bearer token (e.g. a Docker secret). |
| `AUTH_TOKEN` | one of | — | Bearer token directly (alternative to `AUTH_TOKEN_FILE`). |
| `TCAD_UPSTREAM_URL` | no | `https://prod-container.trueprodigyapi.com` | TrueProdigy base URL. |
| `TCAD_OFFICE` | no | `Travis` | Office string sent to the auth endpoint. Try `Williamson`, `Hays`, etc. for other Texas counties on TrueProdigy. |
| `TCAD_HTTP_TIMEOUT` | no | `20` | httpx timeout in seconds. |

## Develop

```bash
git clone https://github.com/dantebarbieri/tcad-mcp.git
cd tcad-mcp
python -m venv .venv && . .venv/bin/activate    # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
pytest
ruff check .
```

Run the dev server:

```bash
AUTH_TOKEN=dev python -m tcad_mcp
```

## Roadmap

- **v0.2.0** — OAuth 2.1 (client_credentials + authorization_code with
  Dynamic Client Registration) via generic OIDC discovery. Works against
  any compliant IdP — Authelia, Keycloak, Auth0, Okta, Dex, Authentik,
  Zitadel, etc. The static-bearer path stays as a fallback.
- Pluggable upstream wrapper so non-TrueProdigy CADs (HCAD, DCAD, etc.)
  can ship as separate adapters in the same package.
- Optional response cache (currently the consumer is expected to cache).

## License

[MIT](LICENSE).

# Connecting clients to tcad-mcp

This server speaks the [Model Context Protocol](https://modelcontextprotocol.io)
over **Streamable HTTP** at `<your-host>/mcp`, with two parallel auth paths
(static bearer + OIDC-validated JWT) — see the [Authentication section of
the README](../README.md#authentication) for the toggles.

The pages below give copy-pasteable setup snippets for each MCP-aware
client. They all hit the same endpoint shape:

- **URL:** `https://your-host/mcp`
- **Discovery:** `https://your-host/.well-known/oauth-protected-resource`
- **Health:** `https://your-host/health` (unauthenticated)

In every example below, replace `mcp-tcad.example` with your own deployed
hostname and the placeholder tokens with values from your operator (or
from `openssl rand -hex 32` for self-deployments).

---

## Quick reference: which auth mode does each client support?

| Client | Static bearer | OAuth (operator-issued) | Automatic discovery (DCR) |
|---|---|---|---|
| Claude.ai (web) | ✅ via header | ✅ paste client_id+secret | ✅ paste URL only |
| Claude Desktop | ✅ via header | ✅ via header | ✅ via Settings → Integrations |
| Cursor | ✅ via header | ✅ via header | ⚠ varies by version |
| Continue.dev | ✅ via header | ✅ via header | ⚠ varies by version |
| Open WebUI | ✅ | — | — |
| Cody (Sourcegraph) | ✅ | ⚠ via header | — |
| Cline (VS Code) | ✅ | ✅ via header | — |
| Zed | ✅ | ✅ via header | — |
| ChatGPT | via OpenAPI bridge | via OpenAPI bridge | via OpenAPI bridge |
| `curl` / scripts | ✅ | ✅ (after token exchange) | n/a |

"Automatic discovery" means the client follows the [MCP authorization
spec](https://modelcontextprotocol.io/specification/draft/basic/authorization):
hits a protected endpoint, reads the `WWW-Authenticate: Bearer
resource_metadata="…"` header, fetches `/.well-known/oauth-protected-resource`,
discovers the IdP, then registers itself via [Dynamic Client Registration](https://datatracker.ietf.org/doc/html/rfc7591)
(DCR) and runs the user through the consent flow. Requires the IdP to
support DCR; tcad-mcp itself supports it for any compliant IdP.

---

## Claude.ai (web)

The simplest setup. Anthropic's "Custom Integrations" feature handles all
of OAuth + DCR + consent in the browser.

1. **Settings → Integrations → Add custom integration**.
2. **Name:** `TCAD` (or whatever you want).
3. **Server URL:** `https://mcp-tcad.example`
4. Click **Connect**.
   - If your server has OAuth enabled and the IdP supports DCR, Claude
     opens a tab to your IdP's consent screen — log in, approve, done.
   - If your server is bearer-only, Claude prompts for the bearer token.
5. The integration appears in your Claude conversations as available tools.

If the server has the static bearer enabled too, you can choose either
auth method in the dialog. Claude defaults to discovery → DCR; manual
bearer is the fallback.

---

## Claude Desktop (macOS / Windows / Linux)

Two ways: GUI (recommended) or JSON config (advanced / dev).

### A. GUI — Settings → Integrations

Same flow as Claude.ai (web): paste the URL, complete OAuth in the popup.
Available in Claude Desktop ≥ the late-2025 release that added remote MCP
support.

### B. JSON config (advanced)

Edit the config file:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

For a bearer-authenticated server:

```json
{
  "mcpServers": {
    "tcad": {
      "type": "streamable-http",
      "url": "https://mcp-tcad.example/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_STATIC_TOKEN_HERE"
      }
    }
  }
}
```

For OAuth (after manually exchanging credentials for an access token —
Claude Desktop's JSON config doesn't run the OAuth flow itself):

```json
{
  "mcpServers": {
    "tcad": {
      "type": "streamable-http",
      "url": "https://mcp-tcad.example/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_OAUTH_ACCESS_TOKEN_HERE"
      }
    }
  }
}
```

Restart Claude Desktop fully (quit from tray/dock, not just close the
window) for the config to take effect.

For older Claude Desktop versions that only support stdio MCPs, bridge
HTTP via [`mcp-remote`](https://github.com/geelen/mcp-remote):

```json
{
  "mcpServers": {
    "tcad": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://mcp-tcad.example/mcp",
        "--header",
        "Authorization: Bearer YOUR_STATIC_TOKEN_HERE"
      ]
    }
  }
}
```

---

## Cursor

Edit `~/.cursor/mcp.json` (global) or `<project>/.cursor/mcp.json`
(per-project). Cursor honors the same general schema as Claude Desktop's
JSON config.

```json
{
  "mcpServers": {
    "tcad": {
      "url": "https://mcp-tcad.example/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_STATIC_TOKEN_HERE"
      }
    }
  }
}
```

After saving, open the Cursor command palette → "MCP: Reload Servers"
(or restart Cursor).

For OAuth, use the operator-issued client to get an access token
out-of-band (see the "Programmatic / curl" section below) and put the
resulting token in the `Authorization` header.

---

## Continue.dev (VS Code / JetBrains)

Edit `~/.continue/config.yaml` (or the legacy `config.json`):

```yaml
mcpServers:
  - name: tcad
    type: streamable-http
    url: https://mcp-tcad.example/mcp
    headers:
      Authorization: Bearer YOUR_STATIC_TOKEN_HERE
```

The Continue extension picks up the change on reload — no IDE restart
needed in recent versions.

---

## Open WebUI

The current Open WebUI MCP integration is bearer-only over HTTP.

1. Open WebUI → **Settings (workspace) → Tools → External MCP Servers**.
2. Click **Add**.
3. **URL:** `https://mcp-tcad.example/mcp`
4. **Bearer token:** your static `AUTH_TOKEN` value.
5. Save.

The tools become available in chats with any model that supports tool
calling.

---

## Cody (Sourcegraph)

Edit `~/.config/sourcegraph/cody/settings.json` (or use the Cody
extension's settings UI):

```json
{
  "cody.experimental.mcp.servers": {
    "tcad": {
      "url": "https://mcp-tcad.example/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_STATIC_TOKEN_HERE"
      }
    }
  }
}
```

Reload Cody for the change to take effect.

---

## Cline (VS Code)

In VS Code: **Cline sidebar → MCP Servers → Edit JSON** (or edit
`~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`):

```json
{
  "mcpServers": {
    "tcad": {
      "type": "streamableHttp",
      "url": "https://mcp-tcad.example/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_STATIC_TOKEN_HERE"
      }
    }
  }
}
```

Click **Restart Server** in the Cline MCP UI, or reload VS Code.

---

## Zed

Install an MCP-compatible extension from Zed's extension registry, then
configure via `~/.config/zed/settings.json`:

```json
{
  "context_servers": {
    "tcad": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://mcp-tcad.example/mcp",
        "--header",
        "Authorization: Bearer YOUR_STATIC_TOKEN_HERE"
      ]
    }
  }
}
```

Zed historically prefers stdio servers, so the [`mcp-remote`](https://github.com/geelen/mcp-remote)
bridge is the most reliable path for an HTTP server.

---

## ChatGPT

ChatGPT does not natively support arbitrary remote MCP servers (as of
this writing — OpenAI's "Connectors" feature is in limited preview).
The interoperable path is to expose the MCP tools as REST endpoints and
register them as a **Custom GPT Action** via OpenAPI:

1. Build a tiny REST adapter that wraps the MCP tools you want to
   expose. Call the MCP server internally via Streamable HTTP; expose
   each tool as one REST endpoint described by OpenAPI 3.1.
2. Host the adapter somewhere ChatGPT can reach (it pings public HTTPS
   endpoints with `User-Agent: GPTBot/...`).
3. In ChatGPT → **Create a GPT → Configure → Actions → Create new action**.
4. Paste your adapter's OpenAPI spec URL.
5. For auth, use either Bearer (paste your `AUTH_TOKEN`) or OAuth (point
   at your IdP's authorization + token endpoints — the same ones
   Claude.ai discovers automatically).

Alternatively, watch [OpenAI's connector / MCP roadmap](https://platform.openai.com/docs/guides/connectors)
for native support to land.

A minimal Python adapter using [`mcp`](https://pypi.org/project/mcp/) +
FastAPI is the typical pattern; we don't ship one in this repo.

---

## Programmatic / curl / scripts

### Static bearer

```bash
curl -isk https://mcp-tcad.example/health
curl -isk -H "Authorization: Bearer YOUR_STATIC_TOKEN_HERE" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  https://mcp-tcad.example/mcp | head -20
```

### OAuth `client_credentials` (machine-to-machine)

```bash
ACCESS_TOKEN=$(curl -sk -X POST https://your-idp.example/oauth2/token \
  -d "grant_type=client_credentials" \
  -d "client_id=YOUR_CLIENT_ID" \
  -d "client_secret=YOUR_CLIENT_SECRET" \
  -d "scope=mcp:tcad" \
  -d "audience=https://mcp-tcad.example" | jq -r .access_token)

curl -isk -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  https://mcp-tcad.example/mcp
```

The exact token-endpoint URL depends on the IdP — discover it via
`GET https://your-idp.example/.well-known/openid-configuration` if you
don't already know it.

### Python (SDK)

The official Python SDK (`mcp` on PyPI) handles MCP protocol
framing for you:

```python
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    headers = {"Authorization": "Bearer YOUR_STATIC_TOKEN_HERE"}
    async with streamablehttp_client(
        "https://mcp-tcad.example/mcp", headers=headers
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print([t.name for t in tools.tools])
            result = await session.call_tool(
                "search_property", {"address": "11301 Maidenstone Dr"}
            )
            print(result.content[0].text)


asyncio.run(main())
```

### TypeScript / Node

```ts
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const transport = new StreamableHTTPClientTransport(
  new URL("https://mcp-tcad.example/mcp"),
  { requestInit: { headers: { Authorization: "Bearer YOUR_STATIC_TOKEN_HERE" } } },
);

const client = new Client({ name: "demo", version: "0.0.1" }, { capabilities: {} });
await client.connect(transport);
const tools = await client.listTools();
console.log(tools.tools.map((t) => t.name));
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `401 Unauthorized` | Wrong bearer / token expired / wrong scheme | Check the `WWW-Authenticate` header in the response — it carries the failure reason (`invalid_token`, `invalid_request`, etc.) and the `resource_metadata` URL for OAuth discovery. |
| `403 insufficient_scope` | OAuth token doesn't carry the required scope | Check the `scope=` value in `WWW-Authenticate`; re-issue the token with that scope. |
| `404 oauth_not_configured` on `/.well-known/oauth-protected-resource` | The server is in bearer-only or open mode (no OAuth enabled). | If you wanted OAuth, set `OAUTH_ISSUER` (and `OAUTH_AUDIENCE`) on the server. Otherwise, use the bearer flow. |
| Claude.ai's "Connect" button hangs or errors | The IdP doesn't support DCR, or its CORS config doesn't include `https://claude.ai` | Either fall back to bearer auth in Claude.ai's dialog, or fix the IdP CORS allowlist. |
| The server logs `WARN: tcad-mcp starting with NO authentication enabled` | Both modes resolved to disabled | Set `AUTH_TOKEN` (and/or `OAUTH_ISSUER`), or accept the open-server posture for trusted-LAN deploys. |

For more, run the server with `LOG_LEVEL=debug` and watch
`docker logs <container>` while reproducing the issue.

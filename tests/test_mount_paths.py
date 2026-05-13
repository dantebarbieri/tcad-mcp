"""Tests for the MCP endpoint mount paths.

FastMCP mounts streamable-http at ``/mcp`` by default, but a couple of
clients — Claude.ai's web "custom integrations" flow being the most
visible — POST straight to the bare server URL with no path suffix.
``create_app`` therefore mounts the same handler at both ``/`` and
``/mcp``. These tests pin that behavior so we don't lose either path.

Auth is disabled here (the dual-mount is orthogonal to auth and the
auth path is exhaustively covered in ``test_auth.py``).
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def open_app(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BEARER_AUTH_ENABLED", "false")
    monkeypatch.setenv("OAUTH_AUTH_ENABLED", "false")
    from tcad_mcp.server import create_app

    return create_app()


def _initialize_payload() -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "initialize",
        "id": 1,
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }


_MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def test_mcp_endpoint_at_root(open_app) -> None:
    """``POST /`` reaches the FastMCP streamable-http handler.

    This is the path Claude.ai's web custom-integrations flow uses — it
    POSTs to whatever URL the user pasted, with no ``/mcp`` suffix. If
    we lose this mount, every Claude.ai web user gets a 404 after the
    OAuth dance completes.
    """
    with TestClient(open_app) as c:
        r = c.post("/", json=_initialize_payload(), headers=_MCP_HEADERS)
    assert r.status_code == 200
    assert "protocolVersion" in r.text


def test_mcp_endpoint_at_mcp_path(open_app) -> None:
    """``POST /mcp`` keeps working — backward compat with existing configs.

    Claude Desktop (via mcp-remote), Cursor, Continue.dev, Open WebUI,
    and the bearer-token curl recipes in CLIENTS.md all already point at
    ``/mcp`` and must not break when the root mount is added.
    """
    with TestClient(open_app) as c:
        r = c.post("/mcp", json=_initialize_payload(), headers=_MCP_HEADERS)
    assert r.status_code == 200
    assert "protocolVersion" in r.text


def test_health_still_routes_to_health_handler(open_app) -> None:
    """``/health`` must NOT be swallowed by the new root mount.

    Route ordering matters: the ``/health`` Route is inserted at index
    0 of ``app.routes`` so it matches before the streamable-http mounts.
    Regression test against accidentally reordering those inserts.
    """
    with TestClient(open_app) as c:
        r = c.get("/health")
    assert r.status_code == 200
    assert r.text == "ok"


def test_well_known_still_routes_to_metadata_handler(open_app) -> None:
    """``/.well-known/oauth-protected-resource`` must keep its handler.

    With OAuth disabled the handler returns 404 (per RFC 9728 §3.2 — a
    metadata document with zero authorization servers is invalid). The
    important assertion is that the request reaches the *metadata*
    handler (which returns a JSON ``oauth_not_configured`` error body),
    not the FastMCP root mount.
    """
    with TestClient(open_app) as c:
        r = c.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 404
    assert "oauth_not_configured" in r.text


def test_unknown_path_still_404s(open_app) -> None:
    """Adding a root mount must not turn arbitrary paths into MCP endpoints.

    The new ``/`` Route uses an exact-match path (not a Mount), so
    ``/some/other/path`` should still 404 — only the literal ``/`` and
    ``/mcp`` reach the streamable-http handler.
    """
    with TestClient(open_app) as c:
        r = c.post("/some/random/path", json=_initialize_payload(), headers=_MCP_HEADERS)
    assert r.status_code == 404

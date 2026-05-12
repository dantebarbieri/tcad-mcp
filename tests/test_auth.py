"""Tests for tcad_mcp.auth — bearer fallback + standard OIDC JWT validation.

The test setup is intentionally generic: the fake IdP fixtures simulate any
OIDC-compliant authorization server (discovery doc + JWKS endpoint). No
Authelia-specific code anywhere — that's the whole point of v0.2.0.

Tests use ``pytest_httpx`` to mock the discovery + JWKS HTTP calls the
middleware makes, and ``joserfc`` to mint real RSA-signed JWTs against a
controlled keypair.
"""
from __future__ import annotations

import time

import pytest
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import RSAKey
from pytest_httpx import HTTPXMock
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from tcad_mcp.auth import BearerOrOAuthMiddleware, make_protected_resource_metadata
from tcad_mcp.config import BearerConfig, OAuthConfig

BEARER = "static-bearer-token"
ISSUER = "https://idp.example"
JWKS_URI = "https://idp.example/jwks"
AUDIENCE = "https://mcp-tcad.example"
SCOPE = "mcp:tcad"


# ---------------------------------------------------------------------------
# Fixtures + factories
# ---------------------------------------------------------------------------


@pytest.fixture
def signing_key() -> RSAKey:
    return RSAKey.generate_key(2048, parameters={"kid": "k1", "alg": "RS256"})


def make_jwt(
    key: RSAKey,
    *,
    iss: str = ISSUER,
    aud: str | list[str] = AUDIENCE,
    scope: str | None = SCOPE,
    exp_offset: int = 3600,
    extra_claims: dict | None = None,
    kid: str = "k1",
) -> str:
    claims: dict = {
        "iss": iss,
        "aud": aud,
        "exp": int(time.time()) + exp_offset,
        "iat": int(time.time()),
    }
    if scope is not None:
        claims["scope"] = scope
    if extra_claims:
        claims.update(extra_claims)
    return joserfc_jwt.encode(
        header={"alg": "RS256", "kid": kid},
        claims=claims,
        key=key,
    )


async def _ok_handler(_request) -> JSONResponse:
    return JSONResponse({"ok": True})


def make_app(
    *,
    bearer_config: BearerConfig | None = None,
    oauth_config: OAuthConfig | None = None,
) -> Starlette:
    """Tiny Starlette app: /test (protected) + /health and well-known (bypass)."""
    bc = bearer_config or BearerConfig(enabled=True, token=BEARER)
    oc = oauth_config or OAuthConfig(
        enabled=False,
        issuer=None,
        audience=None,
        required_scope=None,
        jwks_url_override=None,
    )
    app = Starlette(
        routes=[
            Route("/health", _ok_handler),
            Route(
                "/.well-known/oauth-protected-resource",
                make_protected_resource_metadata(oc),
            ),
            Route("/test", _ok_handler),
        ]
    )
    app.add_middleware(
        BearerOrOAuthMiddleware,
        bearer_config=bc,
        oauth_config=oc,
    )
    return app


def make_oauth_config(
    *,
    enabled: bool = True,
    issuer: str | None = ISSUER,
    audience: str | None = AUDIENCE,
    scope: str | None = SCOPE,
    resource_url: str | None = None,
    jwks_url_override: str | None = None,
    jwks_ttl: int = 3600,
) -> OAuthConfig:
    return OAuthConfig(
        enabled=enabled,
        issuer=issuer,
        audience=audience,
        required_scope=scope,
        jwks_url_override=jwks_url_override,
        discovery_ttl=3600,
        jwks_ttl=jwks_ttl,
        resource_url=resource_url,
    )


def make_bearer_config(
    *, enabled: bool = True, token: str | None = BEARER
) -> BearerConfig:
    return BearerConfig(enabled=enabled, token=token)


def make_disabled_bearer() -> BearerConfig:
    return BearerConfig(enabled=False, token=None)


def make_disabled_oauth() -> OAuthConfig:
    return OAuthConfig(
        enabled=False,
        issuer=None,
        audience=None,
        required_scope=None,
        jwks_url_override=None,
    )


def mock_idp(
    httpx_mock: HTTPXMock,
    key: RSAKey,
    *,
    jwks_uri: str = JWKS_URI,
) -> None:
    """Set up the standard OIDC discovery + JWKS responses."""
    httpx_mock.add_response(
        url=f"{ISSUER}/.well-known/openid-configuration",
        json={
            "issuer": ISSUER,
            "jwks_uri": jwks_uri,
            "token_endpoint": f"{ISSUER}/oauth2/token",
            "registration_endpoint": f"{ISSUER}/oauth2/register",
            "grant_types_supported": [
                "client_credentials",
                "authorization_code",
                "refresh_token",
            ],
        },
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=jwks_uri,
        json={"keys": [key.as_dict()]},
        is_reusable=True,
    )


# ---------------------------------------------------------------------------
# Bypass paths
# ---------------------------------------------------------------------------


def test_health_bypasses_auth() -> None:
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200


def test_well_known_bypasses_auth() -> None:
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    r = client.get(
        "/.well-known/oauth-protected-resource",
        headers={"X-Forwarded-Proto": "https", "Host": "mcp-tcad.example"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["authorization_servers"] == [ISSUER]
    assert body["scopes_supported"] == [SCOPE]
    assert body["bearer_methods_supported"] == ["header"]


def test_well_known_in_bearer_only_mode_returns_404() -> None:
    """RFC 9728 + MCP spec: zero-value authorization_servers is invalid;
    bearer-only mode therefore must NOT serve the metadata document."""
    app = make_app(oauth_config=None)
    client = TestClient(app)
    r = client.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 404


def test_well_known_resource_uses_resource_url_override() -> None:
    app = make_app(
        oauth_config=make_oauth_config(resource_url="https://override.example")
    )
    client = TestClient(app)
    r = client.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200
    assert r.json()["resource"] == "https://override.example"


def test_well_known_resource_derived_from_x_forwarded_proto() -> None:
    """Behind a TLS-terminating proxy, scheme comes from X-Forwarded-Proto."""
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    r = client.get(
        "/.well-known/oauth-protected-resource",
        headers={"X-Forwarded-Proto": "https", "Host": "mcp-tcad.example"},
    )
    assert r.status_code == 200
    assert r.json()["resource"] == "https://mcp-tcad.example"


# ---------------------------------------------------------------------------
# Static bearer fallback
# ---------------------------------------------------------------------------


def test_static_bearer_in_bearer_only_mode() -> None:
    app = make_app(oauth_config=None)
    client = TestClient(app)
    r = client.get("/test", headers={"Authorization": f"Bearer {BEARER}"})
    assert r.status_code == 200


def test_static_bearer_fallback_with_oauth_configured() -> None:
    """Static bearer must keep working even when OAuth is enabled — that's the
    whole point of "fallback" (OpenClaw, Open WebUI, curl all stay on bearer).

    Notice we deliberately do NOT mock the IdP here — if the bearer fallback
    is wired correctly, the middleware should never reach OIDC discovery or
    JWKS for the static-bearer path. ``pytest-httpx`` would fail the test
    if any unmocked HTTP call were made.
    """
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    r = client.get("/test", headers={"Authorization": f"Bearer {BEARER}"})
    assert r.status_code == 200


def test_static_bearer_with_extra_whitespace() -> None:
    app = make_app(oauth_config=None)
    client = TestClient(app)
    # Trim is performed on the token, so trailing whitespace must succeed.
    r = client.get("/test", headers={"Authorization": f"Bearer {BEARER}  "})
    assert r.status_code == 200


def test_no_authorization_header_returns_401() -> None:
    """RFC 6750 §3: no creds → no `error` param in WWW-Authenticate."""
    app = make_app(oauth_config=None)
    client = TestClient(app)
    r = client.get("/test")
    assert r.status_code == 401
    header = r.headers.get("WWW-Authenticate", "")
    assert "Bearer" in header
    assert "resource_metadata=" in header
    assert "error=" not in header  # no creds → no error code (RFC 6750 §3)


def test_non_bearer_scheme_returns_401_invalid_request() -> None:
    app = make_app(oauth_config=None)
    client = TestClient(app)
    r = client.get("/test", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401
    assert 'error="invalid_request"' in r.headers.get("WWW-Authenticate", "")


def test_empty_bearer_returns_401() -> None:
    app = make_app(oauth_config=None)
    client = TestClient(app)
    r = client.get("/test", headers={"Authorization": "Bearer "})
    assert r.status_code == 401


def test_wrong_static_bearer_in_bearer_only_mode_returns_401() -> None:
    app = make_app(oauth_config=None)
    client = TestClient(app)
    r = client.get("/test", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# OAuth happy path
# ---------------------------------------------------------------------------


def test_valid_jwt_passes(httpx_mock: HTTPXMock, signing_key: RSAKey) -> None:
    mock_idp(httpx_mock, signing_key)
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    token = make_jwt(signing_key)
    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text


def test_audience_array_includes_match(
    httpx_mock: HTTPXMock, signing_key: RSAKey
) -> None:
    """RFC 7519 allows `aud` to be an array of strings."""
    mock_idp(httpx_mock, signing_key)
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    token = make_jwt(signing_key, aud=["https://other.example", AUDIENCE])
    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_scp_array_claim_accepted(
    httpx_mock: HTTPXMock, signing_key: RSAKey
) -> None:
    """Microsoft-style `scp` array claim works alongside RFC 6749 `scope`."""
    mock_idp(httpx_mock, signing_key)
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    token = make_jwt(
        signing_key,
        scope=None,
        extra_claims={"scp": ["mcp:tcad", "mcp:other"]},
    )
    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_no_scope_required_skips_scope_check(
    httpx_mock: HTTPXMock, signing_key: RSAKey
) -> None:
    mock_idp(httpx_mock, signing_key)
    app = make_app(oauth_config=make_oauth_config(scope=None))
    client = TestClient(app)
    token = make_jwt(signing_key, scope="any:thing")
    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_oauth_without_audience_or_resource_url_rejected_at_startup() -> None:
    """OAuth enabled but neither OAUTH_AUDIENCE nor RESOURCE_URL set → fail fast.

    Without an audience the resource server would accept any token from the
    issuer regardless of which resource it was minted for (RFC 9728 §7.4
    confused-deputy). Config validates this at startup.
    """
    with pytest.raises(RuntimeError, match="OAUTH_AUDIENCE"):
        OAuthConfig(
            enabled=True,
            issuer=ISSUER,
            audience=None,
            required_scope=None,
            jwks_url_override=None,
            resource_url=None,
        )


def test_oauth_audience_defaults_to_resource_url() -> None:
    """When RESOURCE_URL is set but OAUTH_AUDIENCE isn't, audience defaults
    to the resource URL — this is the most common safe configuration."""
    cfg = OAuthConfig(
        enabled=True,
        issuer=ISSUER,
        audience=None,
        required_scope=None,
        jwks_url_override=None,
        resource_url=AUDIENCE,
    )
    assert cfg.audience == AUDIENCE


# ---------------------------------------------------------------------------
# OAuth failure modes
# ---------------------------------------------------------------------------


def test_expired_token_rejected(
    httpx_mock: HTTPXMock, signing_key: RSAKey
) -> None:
    mock_idp(httpx_mock, signing_key)
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    token = make_jwt(signing_key, exp_offset=-60)
    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_wrong_issuer_rejected(
    httpx_mock: HTTPXMock, signing_key: RSAKey
) -> None:
    mock_idp(httpx_mock, signing_key)
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    token = make_jwt(signing_key, iss="https://wrong-issuer.example")
    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_wrong_audience_rejected(
    httpx_mock: HTTPXMock, signing_key: RSAKey
) -> None:
    mock_idp(httpx_mock, signing_key)
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    token = make_jwt(signing_key, aud="https://other.example")
    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_audience_array_no_match_rejected(
    httpx_mock: HTTPXMock, signing_key: RSAKey
) -> None:
    mock_idp(httpx_mock, signing_key)
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    token = make_jwt(signing_key, aud=["https://a.example", "https://b.example"])
    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_missing_scope_returns_403_insufficient_scope(
    httpx_mock: HTTPXMock, signing_key: RSAKey
) -> None:
    """RFC 6750 §3: missing scope → 403 + insufficient_scope + scope param."""
    mock_idp(httpx_mock, signing_key)
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    token = make_jwt(signing_key, scope="mcp:other")
    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    header = r.headers.get("WWW-Authenticate", "")
    assert 'error="insufficient_scope"' in header
    assert f'scope="{SCOPE}"' in header


def test_jwt_signed_with_unknown_key_rejected(
    httpx_mock: HTTPXMock, signing_key: RSAKey
) -> None:
    """JWT signed by a key NOT advertised in the JWKS must fail signature check."""
    other_key = RSAKey.generate_key(
        2048, parameters={"kid": "evil", "alg": "RS256"}
    )
    mock_idp(httpx_mock, signing_key)
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    token = make_jwt(other_key, kid="evil")
    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_garbage_bearer_short_circuits_no_jwks_fetch() -> None:
    """A non-JWT-shaped bearer must NOT trigger a JWKS fetch.

    This protects against an attacker amplifying random bearer attempts into
    a JWKS / discovery refetch storm. We deliberately don't mock the IdP —
    if the middleware tried to fetch JWKS pytest-httpx would error out.
    """
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    r = client.get("/test", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_future_nbf_rejected(
    httpx_mock: HTTPXMock, signing_key: RSAKey
) -> None:
    mock_idp(httpx_mock, signing_key)
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    token = make_jwt(
        signing_key,
        extra_claims={"nbf": int(time.time()) + 600},  # 10 minutes in the future
    )
    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_non_numeric_exp_rejected(
    httpx_mock: HTTPXMock, signing_key: RSAKey
) -> None:
    """A `NaN` or string `exp` must not slip past the time check."""
    mock_idp(httpx_mock, signing_key)
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    # joserfc's encoder coerces — manually build the JWT with a string exp.
    bad_claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": "not-a-number",
        "iat": int(time.time()),
        "scope": SCOPE,
    }
    token = joserfc_jwt.encode(
        header={"alg": "RS256", "kid": "k1"},
        claims=bad_claims,
        key=signing_key,
    )
    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_alg_none_rejected(
    httpx_mock: HTTPXMock, signing_key: RSAKey
) -> None:
    """`alg=none` must be rejected via the explicit asymmetric-only allowlist.

    The first defense is actually the JWT-shape regex (rejects empty-sig
    tokens shaped ``header.payload.``). The deeper defense is the explicit
    ``algorithms=[...]`` list passed to ``jwt.decode`` — joserfc's default
    registry already rejects `none`, but the explicit allowlist guards
    against future joserfc default changes opening the hole.

    To reach the deeper defense (rather than the shape regex), use a
    non-empty bogus third segment so the token passes the shape pre-check.
    """
    mock_idp(httpx_mock, signing_key)
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    import base64
    import json as json_mod

    def b64(data: dict) -> str:
        raw = json_mod.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = b64({"alg": "none", "kid": "k1"})
    payload = b64(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "exp": int(time.time()) + 3600,
            "scope": SCOPE,
        }
    )
    # Non-empty bogus signature so the JWT shape regex passes and we reach
    # joserfc's algorithm check.
    token = f"{header}.{payload}.bogus_signature_data"
    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_discovery_issuer_mismatch_rejected(
    httpx_mock: HTTPXMock, signing_key: RSAKey
) -> None:
    """OIDC §4.3: discovery doc's `issuer` must match the configured issuer.

    Without this check, a discovery mirror could trick the resource server
    into trusting tokens minted by a different authorization server. JWKS
    is intentionally NOT mocked here — discovery should fail first, and
    pytest-httpx would flag an unused mock if it weren't.
    """
    httpx_mock.add_response(
        url=f"{ISSUER}/.well-known/openid-configuration",
        json={
            "issuer": "https://different-issuer.example",  # lie about who we are
            "jwks_uri": JWKS_URI,
        },
        is_reusable=True,
    )
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    token = make_jwt(signing_key)
    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_resource_url_with_spoofed_host_header_when_configured() -> None:
    """When RESOURCE_URL is explicitly set, malicious Host headers are ignored."""
    app = make_app(
        oauth_config=make_oauth_config(resource_url="https://mcp-tcad.example")
    )
    client = TestClient(app)
    r = client.get(
        "/.well-known/oauth-protected-resource",
        headers={
            "X-Forwarded-Proto": "https",
            "Host": "evil.example",  # ignored — RESOURCE_URL takes priority
        },
    )
    assert r.status_code == 200
    assert r.json()["resource"] == "https://mcp-tcad.example"


def test_resource_url_rejects_host_with_quote_injection() -> None:
    """A malicious Host header with quote chars must NOT poison the metadata.

    The middleware drops back to a safe placeholder rather than echoing
    attacker input into a header value. (NPM normally strips this; the
    test exists as defense-in-depth for direct connections.)
    """
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    r = client.get(
        "/.well-known/oauth-protected-resource",
        headers={"X-Forwarded-Proto": "https", "Host": 'evil"\\.example'},
    )
    # Either the request is rejected by Starlette's header parser, or the
    # middleware falls back to the placeholder. Both are acceptable; what
    # we must NOT see is the malicious string echoed into the response.
    if r.status_code == 200:
        assert 'evil"' not in r.text
        assert "_unconfigured" in r.text or "https://" in r.json()["resource"]


def test_bearer_in_bearer_only_mode_rejects_jwt() -> None:
    """Without OAuth configured, a JWT-shaped token still fails the bearer check."""
    app = make_app(oauth_config=None)
    client = TestClient(app)
    r = client.get("/test", headers={"Authorization": "Bearer eyJhbGciOiJI.x.y"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# WWW-Authenticate header (resource metadata pointer)
# ---------------------------------------------------------------------------


def test_www_authenticate_points_at_resource_metadata() -> None:
    """The 401 must point at /.well-known/oauth-protected-resource.

    Per draft-ietf-oauth-resource-metadata, clients (Claude.ai included)
    discover the issuer via the resource metadata URL — NOT via an issuer
    URL embedded in the WWW-Authenticate header. This is what keeps the
    server IdP-neutral end-to-end.
    """
    app = make_app(
        oauth_config=make_oauth_config(resource_url="https://mcp-tcad.example")
    )
    client = TestClient(app)
    r = client.get("/test")  # No bearer
    assert r.status_code == 401
    header = r.headers.get("WWW-Authenticate", "")
    assert (
        'resource_metadata="https://mcp-tcad.example/.well-known/oauth-protected-resource"'
        in header
    )
    assert ISSUER not in header  # Must NOT leak issuer URL


def test_www_authenticate_uses_x_forwarded_proto() -> None:
    """When RESOURCE_URL isn't set, derive the metadata URL from forwarded headers."""
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    r = client.get(
        "/test",
        headers={"X-Forwarded-Proto": "https", "Host": "mcp-tcad.example"},
    )
    assert r.status_code == 401
    header = r.headers.get("WWW-Authenticate", "")
    assert (
        "https://mcp-tcad.example/.well-known/oauth-protected-resource" in header
    )


def test_www_authenticate_in_bearer_only_mode() -> None:
    app = make_app(oauth_config=None)
    client = TestClient(app)
    r = client.get(
        "/test",
        headers={"X-Forwarded-Proto": "https", "Host": "mcp-tcad.example"},
    )
    assert r.status_code == 401
    header = r.headers.get("WWW-Authenticate", "")
    assert "Bearer" in header
    assert "resource_metadata=" in header


# ---------------------------------------------------------------------------
# Discovery and JWKS overrides
# ---------------------------------------------------------------------------


def test_jwks_url_override_skips_discovery(
    httpx_mock: HTTPXMock, signing_key: RSAKey
) -> None:
    """When OAUTH_JWKS_URL is set, the middleware must NOT call discovery."""
    direct_jwks_url = "https://idp.example/special/jwks.json"
    httpx_mock.add_response(
        url=direct_jwks_url,
        json={"keys": [signing_key.as_dict()]},
        is_reusable=True,
    )
    # Note: NO discovery endpoint mock added — pytest-httpx will fail the
    # test if the middleware tries to fetch it.
    app = make_app(
        oauth_config=make_oauth_config(jwks_url_override=direct_jwks_url)
    )
    client = TestClient(app)
    token = make_jwt(signing_key)
    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_discovery_and_jwks_cached(
    httpx_mock: HTTPXMock, signing_key: RSAKey
) -> None:
    """Multiple requests within TTL must not hammer the IdP."""
    mock_idp(httpx_mock, signing_key)
    app = make_app(oauth_config=make_oauth_config())
    client = TestClient(app)
    token = make_jwt(signing_key)
    for _ in range(5):
        r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
    # Discovery + JWKS each fetched at most once across 5 requests.
    discovery_calls = [
        rq for rq in httpx_mock.get_requests()
        if rq.url.path == "/.well-known/openid-configuration"
    ]
    jwks_calls = [
        rq for rq in httpx_mock.get_requests() if rq.url.path == "/jwks"
    ]
    assert len(discovery_calls) == 1, (
        f"discovery hit {len(discovery_calls)} times, expected 1 (caching broken)"
    )
    assert len(jwks_calls) == 1, (
        f"JWKS hit {len(jwks_calls)} times, expected 1 (caching broken)"
    )


# ---------------------------------------------------------------------------
# Per-mode enable matrix (v0.3.0+)
# ---------------------------------------------------------------------------


def test_open_server_mode_passes_all_requests() -> None:
    """When BOTH modes disabled, the middleware passes everything through.

    This is the "open server" mode for trusted-LAN deployments. Operator
    accepts the responsibility (warned at startup); the middleware itself
    does no auth.
    """
    app = make_app(
        bearer_config=make_disabled_bearer(),
        oauth_config=make_disabled_oauth(),
    )
    client = TestClient(app)
    # No Authorization header at all — should still succeed.
    r = client.get("/test")
    assert r.status_code == 200
    # Bogus header — also fine in open mode.
    r = client.get("/test", headers={"Authorization": "anything"})
    assert r.status_code == 200


def test_bearer_disabled_oauth_enabled_rejects_static_token() -> None:
    """OAuth-only mode must NOT accept the static bearer.

    The static bearer fails the bearer check (mode disabled), then the JWT
    shape pre-check rejects it (not a JWT), so no JWKS fetch happens —
    pytest-httpx isn't mocked because none should be needed.
    """
    app = make_app(
        bearer_config=make_disabled_bearer(),
        oauth_config=make_oauth_config(),
    )
    client = TestClient(app)
    r = client.get("/test", headers={"Authorization": f"Bearer {BEARER}"})
    assert r.status_code == 401


def test_bearer_disabled_oauth_enabled_accepts_jwt(
    httpx_mock: HTTPXMock, signing_key: RSAKey
) -> None:
    mock_idp(httpx_mock, signing_key)
    app = make_app(
        bearer_config=make_disabled_bearer(),
        oauth_config=make_oauth_config(),
    )
    client = TestClient(app)
    token = make_jwt(signing_key)
    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text


def test_bearer_enabled_oauth_disabled_rejects_jwt() -> None:
    """Bearer-only mode rejects JWT-shaped tokens too — only the static
    bearer is accepted. No JWKS fetch happens (test would fail if it did
    since pytest-httpx isn't mocked)."""
    app = make_app(
        bearer_config=make_bearer_config(),
        oauth_config=make_disabled_oauth(),
    )
    client = TestClient(app)
    # Hand-construct a JWT-shaped token; should still be rejected.
    fake_jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhYmMifQ.fakefakefake"
    r = client.get("/test", headers={"Authorization": f"Bearer {fake_jwt}"})
    assert r.status_code == 401


def test_bearer_enabled_oauth_disabled_accepts_bearer() -> None:
    app = make_app(
        bearer_config=make_bearer_config(),
        oauth_config=make_disabled_oauth(),
    )
    client = TestClient(app)
    r = client.get("/test", headers={"Authorization": f"Bearer {BEARER}"})
    assert r.status_code == 200


def test_open_mode_well_known_returns_404() -> None:
    """Open-server mode: no OAuth → 404 the protected-resource metadata."""
    app = make_app(
        bearer_config=make_disabled_bearer(),
        oauth_config=make_disabled_oauth(),
    )
    client = TestClient(app)
    r = client.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 404

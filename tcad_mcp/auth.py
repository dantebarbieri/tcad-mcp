"""Auth middleware: static bearer + standard OIDC JWT validation.

This module is intentionally **IdP-agnostic**. It speaks generic OAuth 2.1 /
OIDC: the only knob naming an authorization server is the ``OAUTH_ISSUER``
URL value. Discovery via ``GET <issuer>/.well-known/openid-configuration``
yields the JWKS URL and the rest. Should work against Authelia, Keycloak,
Auth0, Okta, Dex, Authentik, Zitadel, Cloudflare Access, or any other
OIDC-compliant issuer — no code changes required.

Two auth paths are supported in parallel:

1. **Static bearer (fallback)** — exact-match against ``AUTH_TOKEN`` from
   env/file, identical to v0.1.0. Always enabled; cheap constant-time
   compare via :func:`hmac.compare_digest`. Used by OpenClaw, Open WebUI,
   and ad-hoc ``curl`` callers.
2. **OAuth JWT** — only enabled when ``OAUTH_ISSUER`` is set. The bearer
   token is decoded as a JWT and validated against the issuer's discovered
   JWKS. Validation enforces:
       - signature against an asymmetric algorithm allowlist (RS/PS/ES/EdDSA)
       - ``exp`` (finite numeric, with optional clock-skew margin)
       - ``nbf`` (when present)
       - ``iss == OAUTH_ISSUER``
       - ``aud`` contains ``OAUTH_AUDIENCE`` (which is required when OAuth
         is enabled — see :class:`tcad_mcp.config.OAuthConfig`)
       - optional scope check against either the RFC 6749 ``scope`` string
         or the array-style ``scp`` claim (Microsoft style)

   On 401 / 403 the response carries a ``WWW-Authenticate`` header per
   RFC 6750 § 3 *and* draft-ietf-oauth-resource-metadata (the
   ``resource_metadata`` parameter that Claude.ai follows to discover the
   issuer). The MCP server itself never embeds an issuer URL in the error
   response — clients always go through the metadata pointer, which is what
   keeps the server IdP-neutral end-to-end.
"""
from __future__ import annotations

import asyncio
import hmac
import re
import time

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .config import BearerConfig, OAuthConfig

# Paths that bypass auth entirely.
_BYPASS_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/.well-known/oauth-protected-resource",
    }
)

# Asymmetric-only algorithms allowlist. Symmetric (HS*) is excluded so that
# a JWKS leak (or attacker-controlled JWKS key, were that possible) cannot
# be used to forge tokens via the classic "alg confusion" attack. ``none``
# is rejected by joserfc's default registry but excluded here too for
# explicit defense-in-depth.
_ALLOWED_ALGORITHMS: tuple[str, ...] = (
    "RS256", "RS384", "RS512",
    "PS256", "PS384", "PS512",
    "ES256", "ES384", "ES512",
    "EdDSA",
)

# Clock skew tolerance for exp / nbf checks (seconds). 60s matches what
# most IdPs configure on the issuing side.
_CLOCK_SKEW_SECONDS = 60

# Negative-cache backoff after a JWKS / discovery fetch failure. Keeps a
# misconfigured or down IdP from translating to a per-request fetch storm.
_FETCH_FAILURE_BACKOFF_SECONDS = 30

# Cheap pre-check: a JWT is exactly three non-empty base64url segments
# separated by ``.`` Anything else can be rejected without touching the
# JWKS cache, which protects us against arbitrary-bearer-as-DoS-amplifier.
_JWT_SHAPE_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")

# Validation rule for derived host headers when ``RESOURCE_URL`` isn't set.
# Standard host[:port], no control chars, no quotes/spaces — anything else
# could let a malicious client poison the advertised metadata URL.
_SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]+(:\d+)?$")


class _AuthError(Exception):
    """Internal — bubbled up to convert into a 401 response."""


class _ScopeError(Exception):
    """Internal — bubbled up to convert into a 403 ``insufficient_scope``."""


class _OIDCMetadata:
    """Lazy + cached OIDC discovery and JWKS fetch for one issuer.

    Single asyncio loop. A per-instance ``asyncio.Lock`` provides
    singleflight semantics: 100 concurrent requests racing an expired entry
    fetch the new value once, not 100 times.
    """

    def __init__(
        self,
        issuer: str,
        jwks_url_override: str | None,
        discovery_ttl: int,
        jwks_ttl: int,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._jwks_url_override = jwks_url_override
        self._discovery_ttl = discovery_ttl
        self._jwks_ttl = jwks_ttl

        self._discovery: dict | None = None
        self._discovery_expires_at: float = 0.0
        self._discovery_failed_until: float = 0.0
        self._discovery_lock = asyncio.Lock()

        self._jwks: KeySet | None = None
        self._jwks_expires_at: float = 0.0
        self._jwks_failed_until: float = 0.0
        self._jwks_lock = asyncio.Lock()

    async def _fetch_discovery(self) -> dict:
        url = f"{self._issuer}/.well-known/openid-configuration"
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url)
            r.raise_for_status()
            doc = r.json()
        # OIDC §4.3: returned `issuer` MUST exactly match the requested issuer.
        # Without this check, the resource server can be fooled by a discovery
        # mirror into trusting tokens minted by a different authorization server.
        if doc.get("issuer", "").rstrip("/") != self._issuer:
            raise _AuthError(
                f"OIDC discovery issuer mismatch: doc says "
                f"{doc.get('issuer')!r}, configured {self._issuer!r}"
            )
        return doc

    async def _get_discovery(self) -> dict:
        now = time.time()
        if self._discovery is not None and now < self._discovery_expires_at:
            return self._discovery
        if now < self._discovery_failed_until:
            raise _AuthError("OIDC discovery temporarily unavailable")
        async with self._discovery_lock:
            now = time.time()
            if self._discovery is not None and now < self._discovery_expires_at:
                return self._discovery
            try:
                self._discovery = await self._fetch_discovery()
            except _AuthError:
                self._discovery_failed_until = now + _FETCH_FAILURE_BACKOFF_SECONDS
                raise
            except Exception as e:  # noqa: BLE001
                self._discovery_failed_until = now + _FETCH_FAILURE_BACKOFF_SECONDS
                raise _AuthError(
                    f"OIDC discovery failed for {self._issuer}"
                ) from e
            self._discovery_expires_at = now + self._discovery_ttl
            self._discovery_failed_until = 0.0
            return self._discovery

    async def _resolve_jwks_url(self) -> str:
        if self._jwks_url_override:
            return self._jwks_url_override
        disc = await self._get_discovery()
        url = disc.get("jwks_uri")
        if not url:
            raise _AuthError(
                f"OIDC discovery for {self._issuer} did not advertise jwks_uri"
            )
        return url

    async def get_jwks(self) -> KeySet:
        now = time.time()
        if self._jwks is not None and now < self._jwks_expires_at:
            return self._jwks
        if now < self._jwks_failed_until:
            raise _AuthError("JWKS temporarily unavailable")
        async with self._jwks_lock:
            now = time.time()
            if self._jwks is not None and now < self._jwks_expires_at:
                return self._jwks
            try:
                url = await self._resolve_jwks_url()
                async with httpx.AsyncClient(timeout=10.0) as c:
                    r = await c.get(url)
                    r.raise_for_status()
                    self._jwks = KeySet.import_key_set(r.json())
            except _AuthError:
                self._jwks_failed_until = now + _FETCH_FAILURE_BACKOFF_SECONDS
                raise
            except Exception as e:  # noqa: BLE001
                self._jwks_failed_until = now + _FETCH_FAILURE_BACKOFF_SECONDS
                raise _AuthError("JWKS fetch failed") from e
            self._jwks_expires_at = now + self._jwks_ttl
            self._jwks_failed_until = 0.0
            return self._jwks

    def invalidate_jwks(self) -> None:
        """Force the next ``get_jwks`` call to refetch.

        Exposed for operator-driven key rotation. Not used by the middleware
        — the cache TTL handles natural rotation; operators needing faster
        rotation can lower ``OAUTH_JWKS_TTL``.
        """
        self._jwks = None
        self._jwks_expires_at = 0.0


class BearerOrOAuthMiddleware(BaseHTTPMiddleware):
    """Multi-mode auth middleware: static bearer + OIDC JWT.

    Each mode is independently enabled/disabled via :class:`BearerConfig`
    and :class:`OAuthConfig`. When both are disabled, the middleware
    becomes a pass-through (open-server mode for trusted-LAN deploys).

    When at least one mode is enabled, the bearer path is checked first
    via :func:`hmac.compare_digest` (constant-time over equal-length
    inputs). On bearer miss (or when bearer is disabled), if OAuth is
    enabled the token is decoded as a JWT and validated against the
    issuer's JWKS plus ``iss`` / ``aud`` / scope claims.

    A JWT-shape pre-check rejects garbage tokens before any network fetch
    — preventing an attacker from amplifying junk bearer attempts into
    JWKS / discovery refetches.
    """

    def __init__(
        self,
        app,
        *,
        bearer_config: BearerConfig,
        oauth_config: OAuthConfig,
    ) -> None:
        super().__init__(app)
        self._bearer_config = bearer_config
        self._oauth_config = oauth_config
        self._meta: _OIDCMetadata | None = None
        if oauth_config.enabled:
            self._meta = _OIDCMetadata(
                oauth_config.issuer,  # type: ignore[arg-type]  # enabled => non-None
                oauth_config.jwks_url_override,
                oauth_config.discovery_ttl,
                oauth_config.jwks_ttl,
            )

    @property
    def _open_server(self) -> bool:
        return not self._bearer_config.enabled and not self._oauth_config.enabled

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _BYPASS_PATHS:
            return await call_next(request)

        # Open-server mode: no auth at all (operator's choice; warned at startup).
        if self._open_server:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        if not header:
            # No credentials — RFC 6750 §3: SHOULD NOT include `error` param.
            return self._challenge(request, error=None, status=401)
        if not header.lower().startswith("bearer "):
            # Wrong scheme — RFC 6750 §3.1: invalid_request.
            return self._challenge(request, error="invalid_request", status=401)
        token = header[7:].strip()
        if not token:
            return self._challenge(request, error="invalid_request", status=401)

        # Path 1: static bearer (constant-time compare).
        if (
            self._bearer_config.enabled
            and self._bearer_config.token is not None
            and hmac.compare_digest(token, self._bearer_config.token)
        ):
            return await call_next(request)

        # Path 2: OAuth JWT.
        if self._meta is None:
            return self._challenge(request, error="invalid_token", status=401)
        if not _JWT_SHAPE_RE.match(token):
            # Definitely not a JWT — short-circuit to avoid amplifying junk
            # bearer attempts into JWKS refetches.
            return self._challenge(request, error="invalid_token", status=401)
        try:
            await self._validate_jwt(token)
        except _ScopeError as e:
            return self._challenge(
                request, error="insufficient_scope", status=403, scope=str(e)
            )
        except _AuthError:
            return self._challenge(request, error="invalid_token", status=401)

        return await call_next(request)

    async def _validate_jwt(self, token: str) -> None:
        assert self._meta is not None and self._oauth_config is not None
        keyset = await self._meta.get_jwks()
        try:
            decoded = jwt.decode(
                token, keyset, algorithms=list(_ALLOWED_ALGORITHMS)
            )
        except JoseError as e:
            raise _AuthError(f"jwt validation failed: {e}") from e

        claims = decoded.claims

        # exp — required, finite numeric, in the future (with skew).
        exp = claims.get("exp")
        if not _is_finite_number(exp):
            raise _AuthError("token missing or non-numeric exp")
        if float(exp) + _CLOCK_SKEW_SECONDS <= time.time():
            raise _AuthError("token expired")

        # nbf — optional; if present must be finite and not in the future.
        nbf = claims.get("nbf")
        if nbf is not None:
            if not _is_finite_number(nbf):
                raise _AuthError("token has non-numeric nbf")
            if float(nbf) > time.time() + _CLOCK_SKEW_SECONDS:
                raise _AuthError("token not yet valid")

        # iss
        if claims.get("iss") != self._oauth_config.issuer:
            raise _AuthError("issuer mismatch")

        # aud — REQUIRED when OAuth is enabled (config validates this at
        # startup, so audience is non-None here).
        required_aud = self._oauth_config.audience
        assert required_aud is not None, (
            "OAuthConfig should have rejected this at startup"
        )
        aud = claims.get("aud")
        if isinstance(aud, str):
            if aud != required_aud:
                raise _AuthError("audience mismatch")
        elif isinstance(aud, list):
            if required_aud not in aud:
                raise _AuthError("audience mismatch")
        else:
            raise _AuthError("audience claim missing or unsupported type")

        # scope (RFC 6749 `scope` string, or Microsoft-style `scp` array)
        required_scope = self._oauth_config.required_scope
        if required_scope and required_scope not in _extract_scopes(claims):
            raise _ScopeError(required_scope)

    def _challenge(
        self,
        request: Request,
        *,
        error: str | None,
        status: int,
        scope: str | None = None,
    ) -> Response:
        resource_url = _resource_url_for_request(request, self._oauth_config)
        metadata_url = f"{resource_url}/.well-known/oauth-protected-resource"
        params = [f'resource_metadata="{metadata_url}"']
        if error:
            params.append(f'error="{error}"')
        if scope:
            params.append(f'scope="{scope}"')
        body: dict = {}
        if error:
            body["error"] = error
        if scope:
            body["scope"] = scope
        return JSONResponse(
            body or {"error": "unauthorized"},
            status_code=status,
            headers={"WWW-Authenticate": "Bearer " + ", ".join(params)},
        )


def make_protected_resource_metadata(oauth_config: OAuthConfig | None):
    """Build the route handler for ``/.well-known/oauth-protected-resource``.

    In bearer-only or open-server mode (no OAuth issuer configured) this
    returns 404 — per RFC 9728 §3.2 the metadata document MUST NOT have a
    zero-value ``authorization_servers`` array, and per the MCP authorization
    spec it MUST list at least one issuer. Returning 404 is the spec-correct
    way to say "this resource doesn't speak OAuth".
    """

    async def handler(request: Request) -> Response:
        if oauth_config is None or not oauth_config.enabled:
            return JSONResponse(
                {"error": "oauth_not_configured"}, status_code=404
            )
        resource = _resource_url_for_request(request, oauth_config)
        body: dict = {
            "resource": resource,
            "authorization_servers": [oauth_config.issuer],
            "bearer_methods_supported": ["header"],
        }
        if oauth_config.required_scope:
            body["scopes_supported"] = [oauth_config.required_scope]
        return JSONResponse(body)

    return handler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_finite_number(x: object) -> bool:
    """True only if ``x`` is a real, finite int or float (no NaN/Infinity)."""
    if isinstance(x, bool):  # bool is an int subclass — exclude
        return False
    if isinstance(x, int):
        return True
    if isinstance(x, float):
        return x == x and x not in (float("inf"), float("-inf"))
    return False


def _extract_scopes(claims: dict) -> set[str]:
    scope = claims.get("scope")
    if isinstance(scope, str) and scope:
        return set(scope.split())
    scp = claims.get("scp")
    if isinstance(scp, list):
        return {str(s) for s in scp}
    return set()


def _resource_url_for_request(
    request: Request, oauth_config: OAuthConfig | None
) -> str:
    """Resolve the externally-visible URL of this MCP server.

    Priority:
        1. Explicit ``RESOURCE_URL`` env var (most reliable; what production
           deployments should set).
        2. ``X-Forwarded-Proto`` + ``X-Forwarded-Host`` / ``Host`` headers,
           validated against a strict allowlist regex so a malicious client
           connecting directly (bypassing NPM) can't poison the metadata URL
           with control characters or quote-injection.
        3. Request scheme + netloc (last resort, only correct when the
           client connects directly).

    Returns ``https://_unconfigured`` if all of the above produce something
    unsafe — better to advertise an obvious placeholder than to echo
    attacker-controlled input into a header value.
    """
    if oauth_config is not None and oauth_config.resource_url:
        return oauth_config.resource_url.rstrip("/")
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get(
        "host"
    )
    if forwarded_proto and forwarded_host:
        scheme = forwarded_proto.split(",", 1)[0].strip().lower()
        host = forwarded_host.split(",", 1)[0].strip()
        if scheme in ("https", "http") and _SAFE_HOST_RE.match(host):
            return f"{scheme}://{host}"
    if (
        request.url.scheme in ("http", "https")
        and _SAFE_HOST_RE.match(request.url.netloc)
    ):
        return f"{request.url.scheme}://{request.url.netloc}"
    return "https://_unconfigured"

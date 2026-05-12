"""Environment-driven configuration for tcad-mcp.

Kept deliberately small and import-time-safe — ``AppConfig.from_env`` is the
only thing that may raise on missing required values, and it's only called
from ``create_app`` at startup, not at module import. This lets the test suite
import any module under ``tcad_mcp`` without setting ``AUTH_TOKEN``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class OAuthConfig:
    """OIDC-issuer configuration for the OAuth auth path.

    All fields are optional. ``issuer is None`` disables the OAuth path
    entirely — the server falls back to bearer-only mode, identical to v0.1.0.

    Intentionally **IdP-agnostic**: the only knob naming an IdP is the issuer
    URL itself (a value, not a key name). The middleware uses standard OIDC
    discovery (``GET <issuer>/.well-known/openid-configuration``) to find
    ``jwks_uri`` and other endpoints. ``OAUTH_JWKS_URL`` is an override for
    the rare IdP that doesn't ship discovery.

    When ``issuer`` is set, ``audience`` is **required** (defaulted to
    ``resource_url`` if both are set; rejects at startup if neither is). This
    prevents the confused-deputy problem of accepting any issuer-minted
    token regardless of which resource it was minted for.
    """

    issuer: str | None
    audience: str | None
    required_scope: str | None
    jwks_url_override: str | None
    discovery_ttl: int = 3600
    jwks_ttl: int = 3600
    resource_url: str | None = None

    def __post_init__(self) -> None:
        # Audience requirement: when OAuth is enabled, we must know what
        # `aud` claim to require. Defaulting to resource_url is allowed
        # (object.__setattr__ because we're frozen).
        if self.issuer is not None and self.audience is None:
            if self.resource_url is None:
                raise RuntimeError(
                    "OAUTH_ISSUER is set but neither OAUTH_AUDIENCE nor "
                    "RESOURCE_URL was provided. Set OAUTH_AUDIENCE explicitly "
                    "(or set RESOURCE_URL to use it as the default audience). "
                    "An unset audience would let any token from this issuer "
                    "be replayed against this MCP server (RFC 9728 §7.4)."
                )
            object.__setattr__(self, "audience", self.resource_url)

    @property
    def enabled(self) -> bool:
        return self.issuer is not None

    @classmethod
    def from_env(cls) -> OAuthConfig:
        return cls(
            issuer=_strip_or_none(os.environ.get("OAUTH_ISSUER")),
            audience=_strip_or_none(os.environ.get("OAUTH_AUDIENCE")),
            required_scope=_strip_or_none(os.environ.get("OAUTH_REQUIRED_SCOPE")),
            jwks_url_override=_strip_or_none(os.environ.get("OAUTH_JWKS_URL")),
            discovery_ttl=int(os.environ.get("OAUTH_DISCOVERY_TTL", "3600")),
            jwks_ttl=int(os.environ.get("OAUTH_JWKS_TTL", "3600")),
            resource_url=_strip_or_none(os.environ.get("RESOURCE_URL")),
        )


@dataclass(frozen=True)
class AppConfig:
    upstream_url: str
    office: str
    http_timeout: float
    bearer_token: str
    oauth: OAuthConfig

    @classmethod
    def from_env(cls) -> AppConfig:
        upstream = os.environ.get(
            "TCAD_UPSTREAM_URL", "https://prod-container.trueprodigyapi.com"
        ).rstrip("/")
        office = os.environ.get("TCAD_OFFICE", "Travis")
        http_timeout = float(os.environ.get("TCAD_HTTP_TIMEOUT", "20"))
        bearer = _load_bearer_token()
        return cls(
            upstream_url=upstream,
            office=office,
            http_timeout=http_timeout,
            bearer_token=bearer,
            oauth=OAuthConfig.from_env(),
        )


def _strip_or_none(s: str | None) -> str | None:
    if s is None:
        return None
    s = s.strip()
    return s or None


def _load_bearer_token() -> str:
    token_file = os.environ.get("AUTH_TOKEN_FILE")
    if token_file:
        with open(token_file) as f:
            return f.read().strip()
    token = os.environ.get("AUTH_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "tcad-mcp requires AUTH_TOKEN_FILE or AUTH_TOKEN to be set"
        )
    return token

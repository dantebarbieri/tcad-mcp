"""Environment-driven configuration for tcad-mcp.

Kept deliberately small and import-time-safe — ``AppConfig.from_env`` is the
only thing that may raise on missing required values, and it's only called
from ``create_app`` at startup, not at module import. This lets the test suite
import any module under ``tcad_mcp`` without setting ``AUTH_TOKEN``.

Three auth modes can be independently enabled/disabled (v0.3.0+):

- **Bearer** — controlled by ``BEARER_AUTH_ENABLED``. Three-state:
  unset = auto-enable iff ``AUTH_TOKEN`` / ``AUTH_TOKEN_FILE`` is set
  (current behavior); ``"true"`` requires it (fail-fast at startup);
  ``"false"`` force-disables even when configured.
- **OAuth (manual client + auto-discovery)** — controlled by
  ``OAUTH_AUTH_ENABLED``, same three-state semantics keyed off
  ``OAUTH_ISSUER``.

Both disabled is permitted (open-server mode for trusted-LAN deploys);
the server prints a one-line warning at startup but boots normally.

All long-lived secrets accept either ``ENV_VAR`` (direct value) or
``ENV_VAR_FILE`` (path to a file containing the value, the standard Docker
pattern used by postgres / mysql / etc.).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass


def _strip_or_none(s: str | None) -> str | None:
    if s is None:
        return None
    s = s.strip()
    return s or None


def _load_secret(env_var: str) -> str | None:
    """Load a secret from either ``ENV_VAR`` or ``ENV_VAR_FILE``.

    Direct env var takes priority over the file (so test/dev can override
    a Docker secret without having to remount). Returns None if neither
    source provides a value.
    """
    direct = _strip_or_none(os.environ.get(env_var))
    if direct:
        return direct
    file_env = os.environ.get(f"{env_var}_FILE")
    if file_env:
        path = file_env.strip()
        if path:
            with open(path) as f:
                return f.read().strip() or None
    return None


def _parse_tristate(env_var: str) -> bool | None:
    """Parse a three-state env var: unset → None, ``"true"``/``"1"`` → True,
    ``"false"``/``"0"`` → False. Anything else raises (fail-fast on typos).
    """
    raw = os.environ.get(env_var)
    if raw is None:
        return None
    val = raw.strip().lower()
    if val in ("true", "1", "yes", "on"):
        return True
    if val in ("false", "0", "no", "off"):
        return False
    if val == "":
        return None
    raise RuntimeError(
        f"{env_var} must be one of true/false/yes/no/on/off/1/0 (or unset for auto), got {raw!r}"
    )


@dataclass(frozen=True)
class BearerConfig:
    """Static-bearer auth configuration.

    ``token`` is the loaded secret (from ``AUTH_TOKEN`` or
    ``AUTH_TOKEN_FILE``); ``None`` if neither is set. ``enabled`` is the
    final mode-on/off after resolving ``BEARER_AUTH_ENABLED`` against the
    presence of ``token``.
    """

    enabled: bool
    token: str | None

    @classmethod
    def from_env(cls) -> BearerConfig:
        token = _load_secret("AUTH_TOKEN")
        explicit = _parse_tristate("BEARER_AUTH_ENABLED")
        if explicit is True:
            if not token:
                raise RuntimeError(
                    "BEARER_AUTH_ENABLED=true but AUTH_TOKEN / "
                    "AUTH_TOKEN_FILE is not set."
                )
            return cls(enabled=True, token=token)
        if explicit is False:
            return cls(enabled=False, token=token)
        # auto-detect (unset)
        return cls(enabled=token is not None, token=token)


@dataclass(frozen=True)
class OAuthConfig:
    """OIDC-issuer configuration for the OAuth auth path.

    Intentionally **IdP-agnostic**: the only knob naming an IdP is the issuer
    URL itself (a value, not a key name). The middleware uses standard OIDC
    discovery (``GET <issuer>/.well-known/openid-configuration``) to find
    ``jwks_uri`` and other endpoints.

    ``enabled`` is the final mode-on/off after resolving
    ``OAUTH_AUTH_ENABLED`` against the presence of ``issuer``. When
    enabled, ``audience`` is **required** (defaulted to ``resource_url`` if
    both are set; rejects at startup if neither is). This prevents the
    confused-deputy problem of accepting any issuer-minted token regardless
    of which resource it was minted for (RFC 9728 §7.4).
    """

    enabled: bool
    issuer: str | None
    audience: str | None
    required_scope: str | None
    jwks_url_override: str | None
    discovery_ttl: int = 3600
    jwks_ttl: int = 3600
    resource_url: str | None = None

    def __post_init__(self) -> None:
        if not self.enabled:
            return
        if self.issuer is None:
            raise RuntimeError(
                "OAUTH_AUTH_ENABLED=true but OAUTH_ISSUER is not set."
            )
        if self.audience is None:
            if self.resource_url is None:
                raise RuntimeError(
                    "OAUTH enabled but neither OAUTH_AUDIENCE nor "
                    "RESOURCE_URL was provided. Set OAUTH_AUDIENCE explicitly "
                    "(or set RESOURCE_URL to use it as the default audience). "
                    "An unset audience would let any token from this issuer "
                    "be replayed against this MCP server (RFC 9728 §7.4)."
                )
            object.__setattr__(self, "audience", self.resource_url)

    @classmethod
    def from_env(cls) -> OAuthConfig:
        issuer = _strip_or_none(os.environ.get("OAUTH_ISSUER"))
        explicit = _parse_tristate("OAUTH_AUTH_ENABLED")
        if explicit is True and issuer is None:
            raise RuntimeError(
                "OAUTH_AUTH_ENABLED=true but OAUTH_ISSUER is not set."
            )
        if explicit is False:
            enabled = False
        elif explicit is True:
            enabled = True
        else:
            enabled = issuer is not None
        return cls(
            enabled=enabled,
            issuer=issuer,
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
    bearer: BearerConfig
    oauth: OAuthConfig

    @classmethod
    def from_env(cls) -> AppConfig:
        upstream = os.environ.get(
            "TCAD_UPSTREAM_URL", "https://prod-container.trueprodigyapi.com"
        ).rstrip("/")
        office = os.environ.get("TCAD_OFFICE", "Travis")
        http_timeout = float(os.environ.get("TCAD_HTTP_TIMEOUT", "20"))
        cfg = cls(
            upstream_url=upstream,
            office=office,
            http_timeout=http_timeout,
            bearer=BearerConfig.from_env(),
            oauth=OAuthConfig.from_env(),
        )
        if not cfg.bearer.enabled and not cfg.oauth.enabled:
            print(
                "WARN: tcad-mcp starting with NO authentication enabled "
                "(neither bearer nor OAuth). Suitable only for trusted-LAN "
                "deployments behind a network ACL. Set AUTH_TOKEN or "
                "OAUTH_ISSUER to enable auth.",
                file=sys.stderr,
            )
        return cfg

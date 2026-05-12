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
class AppConfig:
    upstream_url: str
    office: str
    http_timeout: float
    bearer_token: str

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
        )


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

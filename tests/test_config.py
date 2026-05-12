"""Tests for ``tcad_mcp.config`` — per-mode auth toggles + _FILE secrets."""
from __future__ import annotations

import pytest

from tcad_mcp.config import (
    AppConfig,
    BearerConfig,
    OAuthConfig,
    _load_secret,
    _parse_tristate,
)

# ---------------------------------------------------------------------------
# _load_secret — env var + _FILE
# ---------------------------------------------------------------------------


def test_load_secret_from_direct_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "from-env")
    assert _load_secret("MY_SECRET") == "from-env"


def test_load_secret_from_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    f = tmp_path / "secret"
    f.write_text("from-file\n")
    monkeypatch.setenv("MY_SECRET_FILE", str(f))
    assert _load_secret("MY_SECRET") == "from-file"


def test_load_secret_direct_takes_priority_over_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Direct env var beats _FILE — useful for overriding a Docker secret in dev."""
    f = tmp_path / "secret"
    f.write_text("from-file\n")
    monkeypatch.setenv("MY_SECRET", "from-env")
    monkeypatch.setenv("MY_SECRET_FILE", str(f))
    assert _load_secret("MY_SECRET") == "from-env"


def test_load_secret_returns_none_if_neither_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MY_SECRET", raising=False)
    monkeypatch.delenv("MY_SECRET_FILE", raising=False)
    assert _load_secret("MY_SECRET") is None


def test_load_secret_strips_whitespace(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    f = tmp_path / "secret"
    f.write_text("  with-whitespace  \n\n")
    monkeypatch.setenv("MY_SECRET_FILE", str(f))
    assert _load_secret("MY_SECRET") == "with-whitespace"


def test_load_secret_empty_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_SECRET", "   ")
    assert _load_secret("MY_SECRET") is None


# ---------------------------------------------------------------------------
# _parse_tristate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        ("true", True), ("True", True), ("TRUE", True),
        ("yes", True), ("on", True), ("1", True),
        ("false", False), ("False", False), ("FALSE", False),
        ("no", False), ("off", False), ("0", False),
    ],
)
def test_parse_tristate_recognized_values(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: bool
) -> None:
    monkeypatch.setenv("MY_FLAG", value)
    assert _parse_tristate("MY_FLAG") is expected


def test_parse_tristate_unset_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MY_FLAG", raising=False)
    assert _parse_tristate("MY_FLAG") is None


def test_parse_tristate_empty_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_FLAG", "")
    assert _parse_tristate("MY_FLAG") is None


def test_parse_tristate_typo_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-fast on typos — `BEARER_AUTH_ENABLED=tru` would silently mean
    "auto" without this check."""
    monkeypatch.setenv("MY_FLAG", "maybe")
    with pytest.raises(RuntimeError, match="MY_FLAG"):
        _parse_tristate("MY_FLAG")


# ---------------------------------------------------------------------------
# BearerConfig.from_env — three-state matrix
# ---------------------------------------------------------------------------


def test_bearer_auto_enabled_when_token_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_TOKEN", "abc")
    monkeypatch.delenv("BEARER_AUTH_ENABLED", raising=False)
    cfg = BearerConfig.from_env()
    assert cfg.enabled is True
    assert cfg.token == "abc"


def test_bearer_auto_disabled_when_token_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    monkeypatch.delenv("AUTH_TOKEN_FILE", raising=False)
    monkeypatch.delenv("BEARER_AUTH_ENABLED", raising=False)
    cfg = BearerConfig.from_env()
    assert cfg.enabled is False
    assert cfg.token is None


def test_bearer_explicit_true_requires_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    monkeypatch.delenv("AUTH_TOKEN_FILE", raising=False)
    monkeypatch.setenv("BEARER_AUTH_ENABLED", "true")
    with pytest.raises(RuntimeError, match="AUTH_TOKEN"):
        BearerConfig.from_env()


def test_bearer_explicit_false_disables_even_with_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_TOKEN", "abc")
    monkeypatch.setenv("BEARER_AUTH_ENABLED", "false")
    cfg = BearerConfig.from_env()
    assert cfg.enabled is False
    assert cfg.token == "abc"  # still loaded, just not used


def test_bearer_loads_from_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    f = tmp_path / "tok"
    f.write_text("file-token")
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    monkeypatch.setenv("AUTH_TOKEN_FILE", str(f))
    cfg = BearerConfig.from_env()
    assert cfg.enabled is True
    assert cfg.token == "file-token"


# ---------------------------------------------------------------------------
# OAuthConfig.from_env — three-state matrix
# ---------------------------------------------------------------------------


def test_oauth_auto_enabled_when_issuer_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OAUTH_ISSUER", "https://idp.example")
    monkeypatch.setenv("RESOURCE_URL", "https://mcp.example")
    monkeypatch.delenv("OAUTH_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("OAUTH_AUDIENCE", raising=False)
    cfg = OAuthConfig.from_env()
    assert cfg.enabled is True
    assert cfg.issuer == "https://idp.example"
    assert cfg.audience == "https://mcp.example"  # defaulted from RESOURCE_URL


def test_oauth_auto_disabled_when_issuer_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OAUTH_ISSUER", raising=False)
    monkeypatch.delenv("OAUTH_AUTH_ENABLED", raising=False)
    cfg = OAuthConfig.from_env()
    assert cfg.enabled is False


def test_oauth_explicit_true_requires_issuer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OAUTH_ISSUER", raising=False)
    monkeypatch.setenv("OAUTH_AUTH_ENABLED", "true")
    with pytest.raises(RuntimeError, match="OAUTH_ISSUER"):
        OAuthConfig.from_env()


def test_oauth_explicit_false_disables_even_with_issuer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OAUTH_ISSUER", "https://idp.example")
    monkeypatch.setenv("OAUTH_AUDIENCE", "https://mcp.example")
    monkeypatch.setenv("OAUTH_AUTH_ENABLED", "false")
    cfg = OAuthConfig.from_env()
    assert cfg.enabled is False


def test_oauth_explicit_true_with_full_config_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OAUTH_ISSUER", "https://idp.example")
    monkeypatch.setenv("OAUTH_AUDIENCE", "https://mcp.example")
    monkeypatch.setenv("OAUTH_REQUIRED_SCOPE", "mcp:tcad")
    monkeypatch.setenv("OAUTH_AUTH_ENABLED", "true")
    cfg = OAuthConfig.from_env()
    assert cfg.enabled is True
    assert cfg.audience == "https://mcp.example"
    assert cfg.required_scope == "mcp:tcad"


# ---------------------------------------------------------------------------
# AppConfig — open-server warning
# ---------------------------------------------------------------------------


def test_app_config_open_server_warns(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Both modes disabled (no AUTH_TOKEN, no OAUTH_ISSUER) → boot, but warn."""
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    monkeypatch.delenv("AUTH_TOKEN_FILE", raising=False)
    monkeypatch.delenv("OAUTH_ISSUER", raising=False)
    monkeypatch.delenv("BEARER_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("OAUTH_AUTH_ENABLED", raising=False)
    cfg = AppConfig.from_env()
    assert cfg.bearer.enabled is False
    assert cfg.oauth.enabled is False
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "NO authentication" in captured.err


def test_app_config_only_bearer_no_warning(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("AUTH_TOKEN", "abc")
    monkeypatch.delenv("OAUTH_ISSUER", raising=False)
    monkeypatch.delenv("BEARER_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("OAUTH_AUTH_ENABLED", raising=False)
    cfg = AppConfig.from_env()
    assert cfg.bearer.enabled is True
    assert cfg.oauth.enabled is False
    captured = capsys.readouterr()
    assert "WARN" not in captured.err


def test_app_config_explicit_disable_both_warns(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Explicit `false` for both modes also warns (even though config is present)."""
    monkeypatch.setenv("AUTH_TOKEN", "abc")
    monkeypatch.setenv("OAUTH_ISSUER", "https://idp.example")
    monkeypatch.setenv("OAUTH_AUDIENCE", "https://mcp.example")
    monkeypatch.setenv("BEARER_AUTH_ENABLED", "false")
    monkeypatch.setenv("OAUTH_AUTH_ENABLED", "false")
    cfg = AppConfig.from_env()
    assert cfg.bearer.enabled is False
    assert cfg.oauth.enabled is False
    captured = capsys.readouterr()
    assert "WARN" in captured.err

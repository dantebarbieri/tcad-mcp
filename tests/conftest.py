"""Pytest config — keep AUTH_TOKEN out of the per-test environment.

Most tests only exercise pure helpers (``tcad_mcp.shapers``), which have no
env dependency. The ones that import ``tcad_mcp`` directly will trip the
``AUTH_TOKEN`` requirement at module import time — those tests should set
it explicitly via this fixture.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide a synthetic AUTH_TOKEN for tests that import ``tcad_mcp``."""
    monkeypatch.setenv("AUTH_TOKEN", "test-bearer-token")

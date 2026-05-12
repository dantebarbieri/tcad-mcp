"""tcad-mcp — MCP server for the Travis Central Appraisal District public portal.

The ``app`` symbol is what ``uvicorn tcad_mcp:app`` (the production entry
point in the Dockerfile) imports. It's resolved lazily via PEP 562
:func:`__getattr__` so importing any sibling module (e.g. ``tcad_mcp.shapers``
in unit tests) does not trigger ``AppConfig.from_env`` and require
``AUTH_TOKEN`` to be set.

The factory + FastMCP wiring lives in :mod:`tcad_mcp.server` (not
``tcad_mcp.app``) — naming the submodule ``app`` would shadow the lazy
attribute defined here.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .server import create_app

if TYPE_CHECKING:
    from starlette.applications import Starlette

__all__ = ["create_app", "app"]


def __getattr__(name: str) -> Any:
    if name == "app":
        global app  # noqa: PLW0603 — module-level singleton cache
        app = create_app()
        return app
    raise AttributeError(f"module 'tcad_mcp' has no attribute {name!r}")


if TYPE_CHECKING:
    app: Starlette


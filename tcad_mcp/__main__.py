"""CLI entry point — `python -m tcad_mcp` runs uvicorn on the bundled app.

Useful for local development; production uses the Dockerfile CMD which calls
uvicorn directly.
"""
from __future__ import annotations

import os


def main() -> None:
    import uvicorn  # noqa: PLC0415 — defer the import for fast --help

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    log_level = os.environ.get("LOG_LEVEL", "info")
    uvicorn.run(
        "tcad_mcp:app",
        host=host,
        port=port,
        log_level=log_level,
        access_log=False,
    )


if __name__ == "__main__":
    main()

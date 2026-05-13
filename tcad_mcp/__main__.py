"""CLI entry point — `python -m tcad_mcp` runs uvicorn on the bundled app.

Used both for local development and **as the production Dockerfile CMD**.
We can't simply ``CMD ["uvicorn", "...", "--host", "::"]`` because that
goes through ``asyncio.loop.create_server(host="::")``, which hardcodes
``IPV6_V6ONLY=1`` on the listening socket — so the resulting listener is
IPv6-only and refuses every IPv4 connection. Reverse-proxies (Nginx,
NPM, Traefik, …) on a Docker network with both IPv4 and IPv6 enabled
typically resolve the upstream container to *both* address families and
load-balance / fail-over between them, so a v6-only listener produces
half-broken behavior (visible as ``502, 200`` retry pairs in the proxy
access log).

To get a true dual-stack listener we pre-bind the socket ourselves with
``IPV6_V6ONLY=0`` and hand it to ``Server.run(sockets=[sock])``, which
skips uvicorn's ``loop.create_server`` path entirely.
"""
from __future__ import annotations

import os
import socket


def main() -> None:
    import uvicorn  # noqa: PLC0415 — defer the import for fast --help

    host = os.environ.get("HOST", "::")
    port = int(os.environ.get("PORT", "8080"))
    log_level = os.environ.get("LOG_LEVEL", "info")

    config = uvicorn.Config(
        "tcad_mcp:app",
        log_level=log_level,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    server = uvicorn.Server(config)
    server.run(sockets=_build_sockets(host, port))


def _build_sockets(host: str, port: int) -> list[socket.socket]:
    """Build the listening socket(s) for uvicorn.

    Special-case ``host == "::"``: build a single ``AF_INET6`` socket with
    ``IPV6_V6ONLY=0`` so the kernel accepts IPv6 connections **and** IPv4
    connections (transparently mapped to ``::ffff:a.b.c.d``). Setting
    ``IPV6_V6ONLY`` explicitly is required because asyncio's
    ``loop.create_server`` defaults it to 1, and the kernel's per-socket
    default (``/proc/sys/net/ipv6/bindv6only``) is not portable.

    Other host values (a specific v4 or v6 address, ``0.0.0.0``,
    ``127.0.0.1``, …) get a conventional single-stack socket — no surprise
    dual-stack behavior when the operator asked for a specific address.
    """
    if host == "::":
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.bind(("::", port))
        sock.listen(128)
        return [sock]

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(128)
    return [sock]


if __name__ == "__main__":
    main()

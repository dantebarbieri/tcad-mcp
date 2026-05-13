"""Tests for the dual-stack socket builder in tcad_mcp.__main__.

These exist because uvicorn's standard ``--host "::"`` codepath goes
through ``asyncio.loop.create_server`` which hardcodes ``IPV6_V6ONLY=1``,
producing a v6-only listener that breaks reverse proxies on dual-stack
Docker networks. The package's ``__main__`` builds the socket itself to
guarantee a true dual-stack listener.
"""
from __future__ import annotations

import socket
import sys

import pytest

from tcad_mcp.__main__ import _build_sockets


def test_dual_stack_wildcard_is_dual_stack() -> None:
    """``host="::"`` must yield an AF_INET6 socket with V6ONLY=0.

    This is the production case (Dockerfile default). Without V6ONLY=0
    the listener refuses every IPv4 connection, which manifests as a
    ``502 → 200`` retry storm at the upstream proxy.
    """
    socks = _build_sockets("::", 0)
    try:
        assert len(socks) == 1
        s = socks[0]
        assert s.family == socket.AF_INET6
        v6only = s.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY)
        assert v6only == 0, f"IPV6_V6ONLY should be 0 for dual-stack, got {v6only}"
    finally:
        for s in socks:
            s.close()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="IPv4-mapped-IPv6 connect on Windows requires explicit ::ffff: form",
)
def test_dual_stack_socket_actually_accepts_ipv4() -> None:
    """End-to-end check: the dual-stack listener accepts an IPv4 connect.

    The static V6ONLY=0 assertion in the previous test would still pass
    on a system where the kernel refuses IPv4-mapped IPv6 (e.g. some
    hardened containers), so this test does the real connect to catch
    such regressions.
    """
    socks = _build_sockets("::", 0)
    try:
        listener = socks[0]
        port = listener.getsockname()[1]

        # IPv4 connect should succeed.
        with socket.create_connection(("127.0.0.1", port), timeout=2) as c4:
            assert c4.family == socket.AF_INET

        # IPv6 connect should also succeed.
        with socket.create_connection(("::1", port), timeout=2) as c6:
            assert c6.family == socket.AF_INET6
    finally:
        for s in socks:
            s.close()


def test_ipv4_only_host_is_single_stack() -> None:
    """An explicit ``0.0.0.0`` should NOT silently become dual-stack.

    Operators who deliberately ask for IPv4-only get IPv4-only. The
    dual-stack behavior is opt-in via the ``::`` wildcard.
    """
    socks = _build_sockets("0.0.0.0", 0)
    try:
        assert len(socks) == 1
        assert socks[0].family == socket.AF_INET
    finally:
        for s in socks:
            s.close()


def test_specific_ipv6_host_stays_v6() -> None:
    """An explicit IPv6 address gets a v6 single-stack socket."""
    socks = _build_sockets("::1", 0)
    try:
        assert len(socks) == 1
        assert socks[0].family == socket.AF_INET6
    finally:
        for s in socks:
            s.close()


def test_specific_ipv4_host_stays_v4() -> None:
    """An explicit IPv4 address gets a v4 single-stack socket."""
    socks = _build_sockets("127.0.0.1", 0)
    try:
        assert len(socks) == 1
        assert socks[0].family == socket.AF_INET
    finally:
        for s in socks:
            s.close()

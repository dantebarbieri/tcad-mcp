"""Tests for ``tcad_mcp.shapers.normalize_address``."""
from __future__ import annotations

import pytest

from tcad_mcp.shapers import normalize_address


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Strip suffix word + comma tail
        ("11301 Maidenstone Dr, Austin, TX", "11301 MAIDENSTONE"),
        ("4202 Oak Creek Dr", "4202 OAK CREEK"),
        # Multi-suffix and case variations
        ("100 main st, austin", "100 MAIN"),
        ("100 MAIN STREET", "100 MAIN"),
        # No street suffix at all
        ("100 Main", "100 MAIN"),
        # Internal whitespace collapsed
        ("100   Main   Drive", "100 MAIN"),
        # Empty / whitespace-only
        ("", ""),
        ("   ", ""),
        # Comma-only tail strip
        ("100 Main Dr,", "100 MAIN"),
        # Suffix-like word inside the body still stripped (case-insensitive)
        ("11301 maidenstone drive", "11301 MAIDENSTONE"),
    ],
)
def test_normalize_address(raw: str, expected: str) -> None:
    assert normalize_address(raw) == expected

"""Tests for ``tcad_mcp.shapers.build_search_ladder``."""
from __future__ import annotations

from tcad_mcp.shapers import build_search_ladder


def test_full_ladder_for_three_token_street() -> None:
    # "11301 OAK CREEK PARK" — exact, broadened-1, broadened-2, street-only
    ladder = build_search_ladder("11301 OAK CREEK PARK")
    assert ladder == [
        ("exact", "11301 OAK CREEK PARK"),
        ("broadened-1", "11301 OAK CREEK"),
        ("broadened-2", "11301 OAK"),
        ("street-only", "OAK CREEK PARK"),
    ]


def test_two_token_street_skips_broadened_2() -> None:
    # "11301 MAIDENSTONE" — len(street_tokens)==1, only exact + street-only
    ladder = build_search_ladder("11301 MAIDENSTONE")
    assert ladder == [
        ("exact", "11301 MAIDENSTONE"),
        ("street-only", "MAIDENSTONE"),
    ]


def test_three_token_total_yields_broadened_1_only() -> None:
    # 1 num + 2 street tokens — broadened-1 fires, broadened-2 doesn't
    ladder = build_search_ladder("100 OAK CREEK")
    assert ladder == [
        ("exact", "100 OAK CREEK"),
        ("broadened-1", "100 OAK"),
        ("street-only", "OAK CREEK"),
    ]


def test_no_house_number_skips_street_only() -> None:
    # No leading number → street-only would duplicate exact, so it's filtered
    ladder = build_search_ladder("OAK CREEK PARK")
    assert ladder == [
        ("exact", "OAK CREEK PARK"),
        ("broadened-1", "OAK CREEK"),
        ("broadened-2", "OAK"),
    ]


def test_empty_input() -> None:
    assert build_search_ladder("") == []
    assert build_search_ladder("   ") == []


def test_dedup_when_strategies_yield_same_query() -> None:
    # "100 MAIN" — exact="100 MAIN", street-only="MAIN" — distinct, no dedup
    ladder = build_search_ladder("100 MAIN")
    queries = [q for _, q in ladder]
    assert len(queries) == len(set(queries))


def test_capped_at_four_attempts() -> None:
    ladder = build_search_ladder("100 A B C D E F")
    assert len(ladder) <= 4

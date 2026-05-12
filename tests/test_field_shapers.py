"""Tests for the small pure shaping helpers in ``tcad_mcp.shapers``."""
from __future__ import annotations

import pytest

from tcad_mcp.shapers import (
    assemble_owner_mailing,
    date_only,
    parse_features,
    safe_float,
    safe_int,
    shape_search_result,
    split_subdivision,
)

# --- safe_int / safe_float -------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [(None, None), ("", None), ("abc", None), ("1", 1), ("1.9", 1), (2.5, 2), (3, 3)],
)
def test_safe_int(raw, expected) -> None:
    assert safe_int(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [(None, None), ("", None), ("abc", None), ("1.5", 1.5), (3, 3.0)],
)
def test_safe_float(raw, expected) -> None:
    assert safe_float(raw) == expected


# --- date_only -------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2024-08-12T00:00:00", "2024-08-12"),
        ("2024-08-12 00:00:00", "2024-08-12"),
        ("2024-08-12", "2024-08-12"),
        ("", None),
        (None, None),
        (12345, 12345),  # non-string — passed through untouched
    ],
)
def test_date_only(raw, expected) -> None:
    assert date_only(raw) == expected


# --- split_subdivision -----------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("LOT 18 BLK T BARRINGTON OAKS SEC 3", "Barrington Oaks"),
        ("LOT 1 BLK A SOMETHING UNIT 2", "Something"),
        ("LOT 1 BLK A SIMPLE ADDITION", "Simple Addition"),
        ("LOT 1 BLK A WITH PH 4", "With"),
        ("", ""),
    ],
)
def test_split_subdivision(raw, expected) -> None:
    assert split_subdivision(raw) == expected


# --- parse_features --------------------------------------------------------

def test_parse_features_basic() -> None:
    raw = ["Foundation: SLAB", "Roof Style: GABLE"]
    parsed, raw_back = parse_features(raw)
    assert parsed == {"foundation": "SLAB", "roof_style": "GABLE"}
    assert raw_back == raw


def test_parse_features_skips_unparseable() -> None:
    raw = ["Foundation: SLAB", "garbage line", ""]
    parsed, _ = parse_features(raw)
    assert parsed == {"foundation": "SLAB"}


def test_parse_features_empty() -> None:
    parsed, raw = parse_features([])
    assert parsed == {}
    assert raw == []


def test_parse_features_none() -> None:
    parsed, raw = parse_features(None)  # type: ignore[arg-type]
    assert parsed == {}
    assert raw == []


# --- assemble_owner_mailing -----------------------------------------------

def test_assemble_owner_mailing_full() -> None:
    row = {
        "addrDeliveryLine": "11301 MAIDENSTONE DR",
        "addrUnitDesignator": "APT 2",
        "addrCity": "AUSTIN",
        "addrState": "TX",
        "addrZip": "78759-4429",
    }
    assert (
        assemble_owner_mailing(row)
        == "11301 MAIDENSTONE DR APT 2 AUSTIN TX 78759-4429"
    )


def test_assemble_owner_mailing_missing_unit() -> None:
    row = {
        "addrDeliveryLine": "11301 MAIDENSTONE DR",
        "addrCity": "AUSTIN",
        "addrState": "TX",
        "addrZip": "78759",
    }
    assert (
        assemble_owner_mailing(row)
        == "11301 MAIDENSTONE DR AUSTIN TX 78759"
    )


def test_assemble_owner_mailing_empty() -> None:
    assert assemble_owner_mailing({}) is None


# --- shape_search_result ---------------------------------------------------

def test_shape_search_result_minimal() -> None:
    row = {
        "pid": 164007,
        "pAccountID": 9221886,
        "legalDescription": "LOT 18 BLK T BARRINGTON OAKS SEC 3",
        "legalAcreage": "0.25",
        "deedDt": "2020-01-15T00:00:00",
        "arbHearing": "Yes",
        "displayName": "DOE, JOHN",
    }
    out = shape_search_result(row)
    assert out["pid"] == 164007
    assert out["account_id"] == 9221886
    assert out["subdivision"] == "Barrington Oaks"
    assert out["lot_sqft"] == round(0.25 * 43560)
    assert out["last_deed_date"] == "2020-01-15"
    assert out["has_arb_hearing"] is True
    assert out["owner_name"] == "DOE, JOHN"


def test_shape_search_result_owner_fallback() -> None:
    row = {"name": "FALLBACK NAME"}
    out = shape_search_result(row)
    assert out["owner_name"] == "FALLBACK NAME"


def test_shape_search_result_no_acreage() -> None:
    out = shape_search_result({"legalAcreage": ""})
    assert out["lot_sqft"] is None


def test_shape_search_result_no_arb_when_missing() -> None:
    out = shape_search_result({})
    assert out["has_arb_hearing"] is False

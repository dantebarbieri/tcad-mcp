"""Pure shaping helpers — no I/O, no env access, no httpx.

These are split out so the test suite can exercise them in isolation without
needing to bootstrap the FastMCP app, mock httpx, or set ``AUTH_TOKEN``.
"""
from __future__ import annotations

import re
from typing import Any

SUFFIX_WORDS: frozenset[str] = frozenset(
    {
        "DR", "DRIVE", "ST", "STREET", "LN", "LANE", "AVE", "AVENUE",
        "RD", "ROAD", "CV", "COVE", "BLVD", "BOULEVARD", "CT", "COURT",
        "TRL", "TRAIL", "WAY", "PL", "PLACE", "PKWY", "PARKWAY",
        "HWY", "HIGHWAY", "CIR", "CIRCLE", "LOOP",
    }
)


def normalize_address(address: str) -> str:
    """Strip everything from the first comma onward, drop suffix words,
    collapse internal whitespace, uppercase. The uppercase step is purely for
    log readability — TCAD's ``mlike`` operator is case-insensitive.
    """
    addr = (address or "").split(",", 1)[0]
    addr = re.sub(r"\s+", " ", addr).strip().upper()
    if not addr:
        return ""
    tokens = [t for t in addr.split(" ") if t and t not in SUFFIX_WORDS]
    return " ".join(tokens)


def build_search_ladder(normalized: str) -> list[tuple[str, str]]:
    """Return the deterministic fallback ladder of ``(strategy, query)`` pairs.

    Strategies, in order:
        - ``exact``: ``<num> <street tokens>``
        - ``broadened-1``: drop the last street token (requires ≥2 street tokens)
        - ``broadened-2``: keep only the first street token (requires ≥3)
        - ``street-only``: drop the number entirely

    Duplicates are filtered so we never re-issue the same query, and the list
    is capped at 4 attempts.
    """
    parts = [p for p in normalized.split(" ") if p]
    if not parts:
        return []
    has_num = parts[0].isdigit()
    num = parts[0] if has_num else ""
    street_tokens = parts[1:] if has_num else parts

    candidates: list[tuple[str, str]] = []

    def add(strategy: str, tokens: list[str], include_num: bool = True) -> None:
        if not tokens and not (include_num and num):
            return
        prefix = f"{num} " if include_num and num else ""
        query = (prefix + " ".join(tokens)).strip()
        if query:
            candidates.append((strategy, query))

    add("exact", street_tokens)
    if len(street_tokens) >= 2:
        add("broadened-1", street_tokens[:-1])
    if len(street_tokens) >= 3:
        add("broadened-2", street_tokens[:1])
    if num and street_tokens:
        add("street-only", street_tokens, include_num=False)

    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for strategy, query in candidates:
        if query in seen:
            continue
        seen.add(query)
        deduped.append((strategy, query))
    return deduped[:4]


def safe_int(x: Any) -> int | None:
    if x is None or x == "":
        return None
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def safe_float(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def date_only(s: Any) -> str | None:
    """Strip the time portion of an ISO datetime, leaving just YYYY-MM-DD."""
    if not s or not isinstance(s, str):
        return s if s else None
    return s.split(" ", 1)[0].split("T", 1)[0]


def split_subdivision(legal_description: str) -> str:
    """Strip ``LOT N BLK X`` prefix and ``SEC|UNIT|PH N`` suffix; title-case.

    e.g. ``"LOT 18 BLK T BARRINGTON OAKS SEC 3"`` -> ``"Barrington Oaks"``.
    """
    s = legal_description or ""
    s = re.sub(r"^\s*LOT\s+\S+\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^\s*BLK\s+\S+\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+(SEC|UNIT|PH)\s+\S+\s*$", "", s, flags=re.IGNORECASE)
    return s.strip().title()


def parse_features(raw_list: list[str]) -> tuple[dict[str, str], list[str]]:
    """Parse the free-text feature list into ``{snake_case_key: value}``."""
    parsed: dict[str, str] = {}
    raw = list(raw_list or [])
    for item in raw:
        if ": " not in item:
            continue
        key, val = item.split(": ", 1)
        snake = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
        if snake:
            parsed[snake] = val
    return parsed, raw


def assemble_owner_mailing(row: dict) -> str | None:
    parts = [
        row.get("addrDeliveryLine"),
        row.get("addrUnitDesignator"),
        " ".join(
            p
            for p in (row.get("addrCity"), row.get("addrState"), row.get("addrZip"))
            if p
        ),
    ]
    joined = " ".join(p for p in parts if p).strip()
    return joined or None


def shape_search_result(row: dict) -> dict:
    legal = row.get("legalDescription") or ""
    acreage = safe_float(row.get("legalAcreage"))
    lot_sqft = round(acreage * 43560) if acreage is not None else None
    return {
        "pid": row.get("pid"),
        "account_id": row.get("pAccountID"),
        "subdivision": split_subdivision(legal) or None,
        "lot_sqft": lot_sqft,
        "lot": row.get("lot"),
        "block": row.get("block"),
        "tract": row.get("tract"),
        "geo_id": row.get("geoID"),
        "map_id": row.get("mapID"),
        "tax_office_ref": row.get("taxOfficeRef"),
        "market_area": row.get("marketArea"),
        "zip": row.get("zip"),
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "appraised_value": safe_int(row.get("appraisedValue")),
        "market_value": safe_int(row.get("marketValue")),
        "land_value": safe_int(row.get("landValue")),
        "improvement_value": safe_int(row.get("improvementValue")),
        "zoning": row.get("zoning"),
        "legal_description": legal or None,
        "full_address": row.get("fullSitus"),
        "street_components": {
            "num": row.get("streetNum"),
            "prefix": row.get("streetPrefix"),
            "name": row.get("streetName"),
            "suffix": row.get("streetSuffix"),
            "secondary": row.get("streetSecondary"),
        },
        "owner_name": row.get("displayName") or row.get("name"),
        "owner_mailing_address": assemble_owner_mailing(row),
        "last_deed_date": date_only(row.get("deedDt")),
        "has_arb_hearing": (row.get("arbHearing") == "Yes"),
    }

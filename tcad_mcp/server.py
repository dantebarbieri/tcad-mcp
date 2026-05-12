"""MCP server wrapping the Travis Central Appraisal District public portal.

Backed by the public TrueProdigy SaaS endpoint (default
``https://prod-container.trueprodigyapi.com``). Two parallel auth paths:

1. **Static bearer** — loaded from ``AUTH_TOKEN_FILE`` or ``AUTH_TOKEN``;
   always available, used by OpenClaw / Open WebUI / curl.
2. **OAuth 2.1 JWT** (v0.2.0+) — generic OIDC validation against any issuer
   set via ``OAUTH_ISSUER``; used by Claude.ai's remote-MCP integration. See
   :mod:`tcad_mcp.auth`.

Intentionally office- AND IdP-agnostic: every external dependency is
env-driven so the same image can be republished for any TCAD office on
TrueProdigy and run against any OIDC-compliant authorization server.

Environment variables
---------------------
AUTH_TOKEN_FILE          Path to a file containing the static bearer token
                         (mutually exclusive with ``AUTH_TOKEN``).
AUTH_TOKEN               Static bearer token (alternative to
                         ``AUTH_TOKEN_FILE``).
TCAD_UPSTREAM_URL        Override the TrueProdigy base URL.
TCAD_OFFICE              Office string sent to the auth endpoint
                         (default ``"Travis"``; e.g. ``"Williamson"``).
TCAD_HTTP_TIMEOUT        httpx timeout in seconds (default ``20``).
OAUTH_ISSUER             OIDC issuer URL — enables the OAuth path when set.
                         All other ``OAUTH_*`` vars are no-ops without this.
OAUTH_AUDIENCE           Required JWT ``aud`` claim. Defaults to the
                         externally-visible URL of this server.
OAUTH_REQUIRED_SCOPE     Optional scope check (matches RFC 6749 ``scope`` or
                         array-style ``scp`` claims).
OAUTH_JWKS_URL           Override the discovery-derived JWKS URL.
OAUTH_DISCOVERY_TTL      OIDC discovery cache (seconds, default 3600).
OAUTH_JWKS_TTL           JWKS cache (seconds, default 3600).
RESOURCE_URL             Externally-visible URL of this server. If unset,
                         derived from ``X-Forwarded-Proto`` / ``Host``
                         headers (which is what NPM sends).
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from datetime import datetime
from typing import Any

import httpx
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from .auth import BearerOrOAuthMiddleware, make_protected_resource_metadata
from .config import AppConfig
from .shapers import (
    build_search_ladder,
    date_only,
    normalize_address,
    parse_features,
    safe_float,
    safe_int,
    shape_search_result,
)

_ISD_RE = re.compile(r"\bISD\b", re.IGNORECASE)


async def _health(_request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def _decode_jwt_exp(token: str) -> float:
    """Decode an unsigned JWT and return its ``exp`` claim (unix seconds)."""
    try:
        _header, payload, _sig = token.split(".")
        payload += "=" * (-len(payload) % 4)
        body = json.loads(base64.urlsafe_b64decode(payload))
        return float(body.get("exp", time.time() + 60))
    except Exception:
        return time.time() + 60


class _UpstreamClient:
    """Tiny stateful wrapper around the TrueProdigy upstream.

    Holds the JWT cache + assessment-year cache. One instance per app — the
    FastMCP tool functions close over it via the factory below. Single asyncio
    loop, so no locks needed: concurrent calls racing an expired entry both
    fetch a new value and the second simply overwrites with an equally
    valid one.
    """

    def __init__(self, config: AppConfig) -> None:
        self._cfg = config
        self._token: str = ""
        self._token_expires_at: float = 0.0
        self._year: int = 0
        self._year_expires_at: float = 0.0

    async def _mint_token(self, client: httpx.AsyncClient) -> str:
        r = await client.post(
            f"{self._cfg.upstream_url}/trueprodigy/cadpublic/auth/token",
            json={"office": self._cfg.office},
        )
        r.raise_for_status()
        return r.json()["user"]["token"]

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        if self._token and time.time() < self._token_expires_at - 30:
            return self._token
        fresh = await self._mint_token(client)
        self._token = fresh
        self._token_expires_at = _decode_jwt_exp(fresh)
        return fresh

    async def get_year(self) -> int:
        if self._year and time.time() < self._year_expires_at:
            return self._year
        try:
            async with httpx.AsyncClient(timeout=self._cfg.http_timeout) as c:
                r = await c.get(f"{self._cfg.upstream_url}/public/config/defaultyear")
                r.raise_for_status()
                new_year = int(r.json()["results"]["year"])
            self._year = new_year
            self._year_expires_at = time.time() + 86400
            return new_year
        except Exception:
            return datetime.now().year

    async def request(self, method: str, path: str, **kwargs) -> Any:
        """Authenticated upstream call. Mints a JWT if needed and retries on 401.

        Returns the parsed JSON body, or ``{}`` for HTTP 204 / empty bodies
        (TCAD uses 204 to signal "no matching rows" on the search endpoint).
        """
        async with httpx.AsyncClient(timeout=self._cfg.http_timeout) as client:
            token = await self._get_token(client)
            for attempt in (1, 2):
                r = await client.request(
                    method,
                    f"{self._cfg.upstream_url}{path}",
                    headers={"Authorization": token},
                    **kwargs,
                )
                if r.status_code == 401 and attempt == 1:
                    self._token = ""
                    self._token_expires_at = 0.0
                    token = await self._get_token(client)
                    continue
                r.raise_for_status()
                if r.status_code == 204 or not r.content:
                    return {}
                return r.json()


def _build_mcp(upstream: _UpstreamClient) -> FastMCP:
    """Wire the FastMCP tools as closures over a single upstream client."""
    mcp = FastMCP("tcad")

    @mcp.tool
    async def search_property(address: str, limit: int = 5) -> dict:
        """Search TCAD by address using a deterministic fallback ladder.

        The address is normalised (suffix words stripped, city/state/zip
        dropped) and searched against ``streetPrimary`` with the ``mlike``
        operator. If the exact form returns no results, progressively broader
        queries are tried.

        Returns ``{strategy, query_used, totalProperty, results}``.
        ``strategy`` is one of ``"exact" | "broadened-1" | "broadened-2" |
        "street-only" | "none"`` — anything other than ``"exact"`` is a
        non-canonical match and should prompt human confirmation downstream.
        """
        normalized = normalize_address(address)
        ladder = build_search_ladder(normalized)
        if not ladder:
            return {
                "strategy": "none",
                "query_used": "",
                "totalProperty": 0,
                "results": [],
            }

        year = await upstream.get_year()
        page_size = max(int(limit), 1)
        for strategy, query in ladder:
            body = {
                "pYear": {"operator": "=", "value": str(year)},
                "streetPrimary": {"operator": "mlike", "value": query},
            }
            data = await upstream.request(
                "POST",
                "/public/property/search",
                params={"page": 1, "pageSize": page_size},
                json=body,
            )
            rows = data.get("results") or []
            if rows:
                total = (data.get("totalProperty") or {}).get(
                    "propertyCount", len(rows)
                )
                return {
                    "strategy": strategy,
                    "query_used": query,
                    "totalProperty": total,
                    "results": [shape_search_result(r) for r in rows[:limit]],
                }
        return {
            "strategy": "none",
            "query_used": ladder[-1][1],
            "totalProperty": 0,
            "results": [],
        }

    @mcp.tool
    async def get_property_general(account_id: int) -> dict:
        """Fetch general property info: legal description, owner, exemptions."""
        data = await upstream.request(
            "GET", f"/public/propertyaccount/{account_id}/general"
        )
        rows = data.get("results") or []
        if not rows:
            return {}
        r = rows[0]
        mailing = re.sub(r"\s+", " ", r.get("address") or "").strip() or None
        return {
            "pid": r.get("pID"),
            "account_id": r.get("pAccountID"),
            "assessment_year": safe_int(r.get("pYear")),
            "legal_description": r.get("legalDescription"),
            "zoning": r.get("zoning"),
            "market_area": r.get("marketArea"),
            "market_area_description": r.get("marketAreaDescription"),
            "state_codes": r.get("stateCodes"),
            "use_code": r.get("useCd"),
            "use_code_description": r.get("useCodeDescription"),
            "owner_name": r.get("name"),
            "owner_secondary": r.get("nameSecondary"),
            "owner_pct": r.get("ownerPct"),
            "owner_mailing_address": mailing,
            "situs_address": r.get("situsAddr"),
            "street_address_short": r.get("streetAddress"),
            "exemptions": r.get("exemptionList"),
            "property_status": r.get("propertyStatus"),
            "protest_status": r.get("protestStatus"),
            # Upstream uses "informalDate" on /general but "informalDt" on /appeal.
            "informal_date": r.get("informalDate") or r.get("informalDt"),
            # Upstream typo: "formatlDate" instead of "formalDate".
            "formal_date": r.get("formatlDate") or r.get("formalDate"),
            "tax_agent": r.get("agent"),
            "tax_agent_status": r.get("agentStatus"),
            "deferral_type": r.get("deferralType"),
            "has_deferral": r.get("hasDeferral"),
            "audit_year": r.get("hsAuditYear"),
        }

    @mcp.tool
    async def get_property_values(account_id: int) -> dict:
        """Fetch the current value plus the (5-year) value history.

        For older history, use :func:`get_full_value_history`.
        """
        val_data, hist_data = await asyncio.gather(
            upstream.request("GET", f"/public/propertyaccount/{account_id}/value"),
            upstream.request(
                "GET", f"/public/propertyaccount/{account_id}/valuehistory"
            ),
        )
        val_rows = val_data.get("results") or []
        current: dict[str, int | None] = {}
        if val_rows:
            v = val_rows[0]
            current = {
                "land_value": safe_int(v.get("ownerLandValue")),
                "improvement_value": safe_int(v.get("ownerImprovementValue")),
                "market_value": safe_int(v.get("ownerMarketValue")),
                "appraised_value": safe_int(v.get("ownerAppraisedValue")),
                "net_appraised_value": safe_int(v.get("ownerNetAppraisedValue")),
                "tax_limitation_value": safe_int(v.get("ownerTaxLimitationValue")),
            }
        history: list[dict] = []
        for h in hist_data.get("results") or []:
            history.append(
                {
                    "year": safe_int(h.get("pYear")),
                    "land_value": safe_int(h.get("ownerLandValue")),
                    "improvement_value": safe_int(h.get("ownerImprovementValue")),
                    "market_value": safe_int(h.get("ownerMarketValue")),
                    "appraised_value": safe_int(h.get("ownerAppraisedValue")),
                }
            )
        history.sort(key=lambda x: x["year"] or 0)
        return {"current": current, "history": history}

    @mcp.tool
    async def get_full_value_history(pid: int) -> list[dict]:
        """Return one row per historical year for a given pid (pre-5-year-window).

        Uses the pid-only search trick: ``POST /public/property/search`` with
        no query string and a single ``pid`` filter returns one search-result
        row per historical year, each with its own ``pAccountID``.
        """
        body = {"pid": {"operator": "=", "value": str(pid)}}
        data = await upstream.request("POST", "/public/property/search", json=body)
        rows = data.get("results") or []
        out = [
            {
                "year": safe_int(r.get("pYear")),
                "account_id": r.get("pAccountID"),
                "land_value": safe_int(r.get("landValue")),
                "improvement_value": safe_int(r.get("improvementValue")),
                "market_value": safe_int(r.get("marketValue")),
                "appraised_value": safe_int(r.get("appraisedValue")),
            }
            for r in rows
        ]
        out.sort(key=lambda x: x["year"] or 0)
        return out

    @mcp.tool
    async def get_property_land(account_id: int) -> dict:
        """Fetch land details (size, type, market value)."""
        data = await upstream.request(
            "GET", f"/public/propertyaccount/{account_id}/land"
        )
        rows = data.get("results") or []
        if not rows:
            return {}
        r = rows[0]
        return {
            "size_sqft": safe_int(r.get("sizeSqft")),
            "size_acres": safe_float(r.get("sizeAcres")),
            "cost_per_sqft": safe_float(r.get("costPerSqft")),
            # Upstream renames marketValue -> mktValue on /land specifically.
            "market_value": safe_int(r.get("mktValue")),
            "land_type": r.get("landType"),
            "land_description": r.get("landDescription"),
        }

    @mcp.tool
    async def get_property_improvements(account_id: int) -> dict:
        """Fetch improvement (building) details and per-component features.

        For each improvement, ``/improvement/{id}/features`` is fetched in
        parallel. The response is collapsed into a single object: the first
        improvement's metadata at the top level, with components flattened
        across all improvements.

        ``year_built`` rule: take the minimum ``actualYearBuilt`` over
        components whose type is ``"1ST"`` or ``"2ND"`` (floor types reflect
        construction date). Falls back to the minimum over all components if
        no floor types exist.
        """
        imp_data = await upstream.request(
            "GET", f"/public/propertyaccount/{account_id}/improvement"
        )
        rows = imp_data.get("results") or []
        if not rows:
            return {}
        primary = rows[0]

        feature_tasks = []
        for r in rows:
            iid = r.get("pImprovementID")
            if iid is not None:
                feature_tasks.append(
                    upstream.request(
                        "GET",
                        f"/public/propertyaccount/improvement/{iid}/features",
                    )
                )
        feature_results = await asyncio.gather(
            *feature_tasks, return_exceptions=True
        )

        feat_by_detail: dict[Any, list[str]] = {}
        for fr in feature_results:
            if isinstance(fr, BaseException):
                continue
            for entry in (fr.get("results") or []):
                did = entry.get("pDetailID")
                if did is not None:
                    feat_by_detail[did] = entry.get("features") or []

        components: list[dict] = []
        for r in rows:
            for d in r.get("details") or []:
                raw_features = feat_by_detail.get(d.get("pDetailID"), [])
                parsed, raw_list = parse_features(raw_features)
                components.append(
                    {
                        "detail_id": d.get("pDetailID"),
                        "type_code": d.get("imprvDetailType"),
                        "type_description": d.get("detailTypeDescription"),
                        "class": d.get("class"),
                        "area_sqft": safe_float(d.get("area")),
                        "year_built": safe_int(d.get("actualYearBuilt")),
                        "effective_year_built": safe_int(d.get("effYearBuilt")),
                        "features": parsed,
                        "features_raw": raw_list,
                    }
                )

        floor_years = [
            c["year_built"]
            for c in components
            if c["type_code"] in {"1ST", "2ND"} and c["year_built"]
        ]
        fallback_years = [c["year_built"] for c in components if c["year_built"]]
        year_built = (
            min(floor_years)
            if floor_years
            else (min(fallback_years) if fallback_years else None)
        )

        return {
            "year_built": year_built,
            "living_area_sqft": safe_int(primary.get("livingArea")),
            "gross_building_area_sqft": safe_int(primary.get("grossBuildingArea")),
            "improvement_value": safe_int(primary.get("improvementValue")),
            "description": primary.get("imprvDescription"),
            "description_specific": primary.get("imprvSpecificDescription"),
            "state_code": primary.get("stateCd"),
            "components": components,
        }

    @mcp.tool
    async def get_property_taxing_units(account_id: int) -> dict:
        """Fetch per-taxing-unit breakdown plus aggregate totals.

        ``/taxable`` returns an *object* (not a list) with both per-unit rows
        and a top-level summary. The top-level ``school_district`` field is a
        derived convenience: the ``taxingUnitName`` of the unit whose name
        matches ``\\bISD\\b`` (case-insensitive). Returns ``None`` if no ISD
        unit exists.
        """
        data = await upstream.request(
            "GET", f"/public/propertyaccount/{account_id}/taxable"
        )
        blob = data.get("results") or {}
        units_raw = blob.get("taxingUnits") or []
        units: list[dict] = []
        school_district: str | None = None
        for u in units_raw:
            name = u.get("taxingUnitName")
            units.append(
                {
                    "name": name,
                    "code": u.get("taxingUnitCode"),
                    "tax_rate": safe_float(u.get("totalTaxRate")),
                    "taxable_value": safe_int(u.get("taxableValue")),
                    "net_appraised_value": safe_int(u.get("netAppraisedValue")),
                    "estimated_taxes": safe_float(u.get("estimatedTaxes")),
                    "estimated_taxes_without_exemptions": safe_float(
                        u.get("estimatedTaxesWoutExemptions")
                    ),
                    "arb_status": u.get("arbStatus"),
                }
            )
            if school_district is None and name and _ISD_RE.search(name):
                school_district = name
        return {
            "units": units,
            "total_tax_rate": safe_float(blob.get("totalTaxRate")),
            "total_estimated_taxes": safe_float(blob.get("estimatedTaxes")),
            "total_estimated_taxes_without_exemptions": safe_float(
                blob.get("estimatedTaxesWoutExemptions")
            ),
            "school_district": school_district,
        }

    @mcp.tool
    async def get_protest_information(account_id: int) -> dict:
        """Fetch protest/appeal status for a property account."""
        data = await upstream.request(
            "GET", f"/public/propertyaccount/{account_id}/appeal"
        )
        rows = data.get("results") or []
        if not rows:
            return {}
        r = rows[0]
        return {
            "appeal_id": r.get("appealID"),
            "appeal_type": r.get("appealType"),
            "appeal_status": r.get("appealStatus"),
            "informal_date": r.get("informalDt"),
            "docket_date": r.get("docketDt"),
            "claimant_opinion_of_value": r.get("claimantOpinionOfValue"),
            "initial_market_value": r.get("initialMarketValue"),
            "final_market_value": r.get("finalMarketValue"),
            "board_determination": r.get("boardDetermination"),
            "panel_members": r.get("panelMembers") or [],
        }

    @mcp.tool
    async def get_property_deed_history(pid: int) -> list[dict]:
        """Fetch full deed/sale history for a pid, sorted ascending by date."""
        data = await upstream.request("GET", f"/public/property/{pid}/deeds")
        rows = data.get("results") or []
        out = [
            {
                "deed_id": r.get("deedID"),
                "deed_type": r.get("deedType"),
                "deed_description": r.get("deedDescription"),
                "deed_date": date_only(r.get("deedDt")),
                "seller": r.get("seller"),
                "buyer": r.get("buyer"),
                "instrument_num": r.get("instrumentNum"),
                "volume": r.get("volume"),
                "book": r.get("book"),
                "page": r.get("page"),
            }
            for r in rows
        ]
        out.sort(key=lambda x: x["deed_date"] or "")
        return out

    return mcp


def create_app(config: AppConfig | None = None) -> Starlette:
    """Build the ASGI app. Reads env via :class:`AppConfig` if not supplied."""
    cfg = config or AppConfig.from_env()
    upstream = _UpstreamClient(cfg)
    mcp = _build_mcp(upstream)
    app = mcp.http_app(transport="streamable-http")
    app.add_middleware(
        BearerOrOAuthMiddleware,
        bearer=cfg.bearer_token,
        oauth_config=cfg.oauth,
    )
    # Order matters: the well-known + health routes must precede MCP's
    # catch-all so the middleware's bypass list and the metadata endpoint
    # actually win the routing.
    app.routes.insert(
        0,
        Route(
            "/.well-known/oauth-protected-resource",
            make_protected_resource_metadata(cfg.oauth),
        ),
    )
    app.routes.insert(0, Route("/health", _health))
    return app

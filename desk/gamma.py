"""Market discovery through Polymarket's public Gamma API (no key needed)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from desk.models import Market

GAMMA_URL = "https://gamma-api.polymarket.com"
PAGE_SIZE = 100  # the API never returns more than 100 per page
DEFAULT_FEE_RATE = 0.05  # "Other / General" taker rate when a fee-enabled market has no schedule


def category_from_fee_type(fee_type: str | None) -> str:
    if not fee_type:
        return "other"
    head = fee_type.split("_fees")[0].strip().lower()
    return head or "other"


def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


def _parse_dt(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_market(raw: dict) -> Market | None:
    outcomes = _json_list(raw.get("outcomes"))
    tokens = _json_list(raw.get("clobTokenIds"))
    prices = _json_list(raw.get("outcomePrices"))
    end_date = _parse_dt(raw.get("endDate"))
    if len(outcomes) != 2 or len(tokens) != 2 or len(prices) != 2 or end_date is None:
        return None
    if raw.get("feesEnabled"):
        schedule = raw.get("feeSchedule") or {}
        fee_rate = _float(schedule.get("rate"), DEFAULT_FEE_RATE)
        fee_exponent = _float(schedule.get("exponent"), 1.0)
    else:
        fee_rate, fee_exponent = 0.0, 1.0
    return Market(
        id=str(raw.get("id")),
        slug=raw.get("slug") or "",
        question=raw.get("question") or "",
        description=raw.get("description") or "",
        outcomes=(str(outcomes[0]), str(outcomes[1])),
        token_ids=(str(tokens[0]), str(tokens[1])),
        outcome_prices=(_float(prices[0]), _float(prices[1])),
        end_date=end_date,
        liquidity=_float(raw.get("liquidityNum", raw.get("liquidity"))),
        volume_24h=_float(raw.get("volume24hr")),
        enable_order_book=bool(raw.get("enableOrderBook")),
        accepting_orders=bool(raw.get("acceptingOrders")),
        closed=bool(raw.get("closed")),
        resolution_source=raw.get("resolutionSource") or "",
        category=category_from_fee_type(raw.get("feeType")),
        fee_rate=fee_rate,
        fee_exponent=fee_exponent,
        min_order_size=_float(raw.get("orderMinSize"), 5.0),
        tick_size=_float(raw.get("orderPriceMinTickSize"), 0.01),
        neg_risk=bool(raw.get("negRisk")),
        game_start=_parse_dt(raw.get("gameStartTime")),
    )


class GammaClient:
    def __init__(self, http: httpx.Client, base_url: str = GAMMA_URL):
        self._http = http
        self._base = base_url.rstrip("/")

    def active_markets(self, limit_total: int) -> list[Market]:
        """Active, open markets ordered by 24h volume, up to limit_total (deduplicated by id)."""
        seen: dict[str, Market] = {}
        offset = 0
        while offset < limit_total:
            page_size = min(PAGE_SIZE, limit_total - offset)
            resp = self._http.get(
                f"{self._base}/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": page_size,
                    "offset": offset,
                    "order": "volume24hr",
                    "ascending": "false",
                },
            )
            resp.raise_for_status()
            page = resp.json()
            for raw in page:
                market = parse_market(raw)
                if market is not None and market.id not in seen:
                    seen[market.id] = market
            if len(page) < page_size:
                break
            offset += page_size
        return list(seen.values())

    def market_by_id(self, market_id: str) -> Market | None:
        resp = self._http.get(f"{self._base}/markets/{market_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return parse_market(resp.json())

    def market_by_slug(self, slug: str) -> Market | None:
        resp = self._http.get(f"{self._base}/markets", params={"slug": slug})
        resp.raise_for_status()
        page = resp.json()
        return parse_market(page[0]) if page else None

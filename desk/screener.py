"""Cheap market filters and the per-cycle choice of which markets the analyst reads."""
from __future__ import annotations

from datetime import datetime

from desk.config import ScanSettings
from desk.models import Market


def rejection_reason(m: Market, now: datetime, s: ScanSettings, held_ids: set[str]) -> str | None:
    if m.closed or not m.enable_order_book or not m.accepting_orders:
        return "not_tradable"
    if m.id in held_ids:
        return "already_held"
    if m.liquidity < s.min_liquidity_usd:
        return "low_liquidity"
    if (m.end_date - now).total_seconds() / 3600 < s.min_hours_to_resolution:
        return "resolves_too_soon"
    low, high = s.price_band
    if not low <= m.outcome_prices[0] <= high:
        return "price_out_of_band"
    if m.category in s.skip_categories:
        return "skipped_category"
    if s.allow_categories and m.category not in s.allow_categories:
        return "category_not_allowed"
    if not m.description.strip():
        return "no_rules"
    return None


def screen(markets: list[Market], now: datetime, s: ScanSettings, held_ids: set[str]) -> list[Market]:
    return [m for m in markets if rejection_reason(m, now, s, held_ids) is None]


def pick_for_analysis(candidates: list[Market], fresh_ids: set[str], cooldown_ids: set[str], budget: int) -> list[Market]:
    if budget <= 0:
        return []
    pending = [m for m in candidates if m.id not in fresh_ids and m.id not in cooldown_ids]
    pending.sort(key=lambda m: m.volume_24h, reverse=True)
    return pending[:budget]

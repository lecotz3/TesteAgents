"""When to get out of an open position, and what a resolved market pays."""
from __future__ import annotations

from datetime import datetime

from desk.config import ExitSettings
from desk.models import Analysis, Market, OrderBook, Position


def exit_reason(
    pos: Position, book: OrderBook, analysis: Analysis | None, now: datetime, s: ExitSettings
) -> str | None:
    if (pos.end_date - now).total_seconds() / 3600 < s.exit_hours_before_resolution:
        return "resolving_soon"
    q_now = analysis.prob_of(pos.outcome_index) if analysis is not None else pos.entry_fair_prob
    if abs(q_now - pos.entry_fair_prob) * 100 > s.exit_fair_change_pp:
        return "fair_value_changed"
    bid = book.best_bid
    if bid is not None and (q_now - bid) * 100 < s.exit_edge_closed_pp:
        return "mispricing_closed"
    return None


def settlement_price(pos: Position, market: Market) -> float | None:
    if not market.closed:
        return None
    price = market.outcome_prices[pos.outcome_index]
    if price >= 0.99:
        return 1.0
    if price <= 0.01:
        return 0.0
    return None

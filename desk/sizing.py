"""Position sizing: fee-aware Kelly, fractional Kelly, hard caps and exchange minimums."""
from __future__ import annotations

import math
from dataclasses import dataclass

from desk.fees import fee_per_share


@dataclass(frozen=True)
class SizeDecision:
    shares: float
    stake: float
    kelly_full: float
    kelly_used: float
    skip_reason: str | None


def kelly_fraction(q: float, effective_price: float) -> float:
    """Full Kelly for a share bought at effective_price that pays 1 if it wins.

    Same as the guide: b = (1 - P) / P and f = (b * q - (1 - q)) / b, i.e. (q - P) / (1 - P).
    """
    if effective_price >= 1.0:
        return -1.0
    return (q - effective_price) / (1.0 - effective_price)


def floor_shares(shares: float) -> float:
    return math.floor(shares * 100 + 1e-9) / 100


def size_position(
    *,
    q: float,
    price: float,
    fee_rate: float,
    fee_exponent: float,
    equity: float,
    cash: float,
    open_count: int,
    kelly_mult: float,
    max_position_pct: float,
    max_open_positions: int,
    min_order_size: float,
) -> SizeDecision:
    effective = price + fee_per_share(price, fee_rate, fee_exponent)
    full = kelly_fraction(q, effective)
    if open_count >= max_open_positions:
        return SizeDecision(0.0, 0.0, full, 0.0, "max_open_positions")
    if full <= 0:
        return SizeDecision(0.0, 0.0, full, 0.0, "no_edge")
    used = full * kelly_mult
    stake = min(min(used, max_position_pct) * equity, max(cash, 0.0))
    shares = floor_shares(stake / effective)
    reason = "below_min_order" if shares < min_order_size else None
    return SizeDecision(shares, shares * effective, full, used, reason)

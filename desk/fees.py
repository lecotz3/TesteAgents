"""Polymarket taker fees: fee = shares x rate x (p x (1 - p)) ** exponent (exponent 1 per docs)."""
from __future__ import annotations

from typing import Iterable


def fee_per_share(price: float, rate: float, exponent: float = 1.0) -> float:
    if rate <= 0 or price <= 0 or price >= 1:
        return 0.0
    return rate * (price * (1.0 - price)) ** exponent


def fee_for_levels(levels: Iterable[tuple[float, float]], rate: float, exponent: float = 1.0) -> float:
    """Total fee for a fill described as (price, shares) per book level."""
    return sum(shares * fee_per_share(price, rate, exponent) for price, shares in levels)

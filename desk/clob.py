"""Order book reads (public CLOB endpoint) and fill simulation by walking price levels."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import httpx

from desk.models import Level, OrderBook

CLOB_URL = "https://clob.polymarket.com"
_EPS = 1e-9


def parse_book(data: dict, token_id: str) -> OrderBook:
    def levels(key: str) -> list[Level]:
        out = []
        for row in data.get(key) or []:
            try:
                price, size = float(row["price"]), float(row["size"])
            except (KeyError, TypeError, ValueError):
                continue
            if size > 0 and 0 < price < 1:
                out.append(Level(price, size))
        return out

    bids = sorted(levels("bids"), key=lambda lvl: lvl.price, reverse=True)
    asks = sorted(levels("asks"), key=lambda lvl: lvl.price)
    return OrderBook(token_id=token_id, bids=tuple(bids), asks=tuple(asks))


@dataclass(frozen=True)
class SimFill:
    shares: float
    avg_price: float  # 0.0 when nothing filled
    limit_price: float  # worst level touched; 0.0 when nothing filled
    levels: tuple[tuple[float, float], ...]  # (price, shares) per level used
    complete: bool


def _walk(levels: tuple[Level, ...], shares: float, acceptable: Callable[[float], bool]) -> SimFill:
    remaining = shares
    used: list[tuple[float, float]] = []
    for lvl in levels:
        if remaining <= _EPS or not acceptable(lvl.price):
            break
        take = min(remaining, lvl.size)
        used.append((lvl.price, take))
        remaining -= take
    filled = shares - max(remaining, 0.0)
    if filled <= _EPS:
        return SimFill(0.0, 0.0, 0.0, (), False)
    avg = sum(price * qty for price, qty in used) / filled
    return SimFill(filled, avg, used[-1][0], tuple(used), remaining <= _EPS)


def simulate_buy(book: OrderBook, shares: float, max_price: float | None = None) -> SimFill:
    return _walk(book.asks, shares, lambda p: max_price is None or p <= max_price + _EPS)


def simulate_sell(book: OrderBook, shares: float, min_price: float | None = None) -> SimFill:
    return _walk(book.bids, shares, lambda p: min_price is None or p >= min_price - _EPS)


def exit_slippage(book: OrderBook, shares: float) -> float | None:
    """Relative cost of dumping `shares` into the bids versus the best bid; None if it cannot fully fill."""
    best = book.best_bid
    if best is None:
        return None
    sim = simulate_sell(book, shares)
    if not sim.complete:
        return None
    return (best - sim.avg_price) / best


class BookClient:
    def __init__(self, http: httpx.Client, base_url: str = CLOB_URL):
        self._http = http
        self._base = base_url.rstrip("/")

    def get_book(self, token_id: str) -> OrderBook:
        resp = self._http.get(f"{self._base}/book", params={"token_id": token_id})
        resp.raise_for_status()
        return parse_book(resp.json(), token_id)

"""Executor contract: turn an order intent into a Fill (simulated or real)."""
from __future__ import annotations

from typing import Protocol

from desk.models import Fill, Market, OrderBook


class ExecutionError(Exception):
    """The order was not executed; nothing changed."""


class Executor(Protocol):
    mode: str

    def buy(self, market: Market, token_id: str, shares: float, limit_price: float, book: OrderBook) -> Fill: ...

    def sell(self, market: Market, token_id: str, shares: float, limit_price: float, book: OrderBook) -> Fill: ...

"""Paper trading: fills against the real order book at decision time, with real taker fees."""
from __future__ import annotations

from desk.clob import SimFill, simulate_buy, simulate_sell
from desk.executor.base import ExecutionError
from desk.fees import fee_for_levels
from desk.models import Fill, Market, OrderBook


class PaperExecutor:
    mode = "paper"

    def buy(self, market: Market, token_id: str, shares: float, limit_price: float, book: OrderBook) -> Fill:
        return self._fill(market, token_id, "BUY", limit_price, simulate_buy(book, shares, max_price=limit_price))

    def sell(self, market: Market, token_id: str, shares: float, limit_price: float, book: OrderBook) -> Fill:
        return self._fill(market, token_id, "SELL", limit_price, simulate_sell(book, shares, min_price=limit_price))

    @staticmethod
    def _fill(market: Market, token_id: str, side: str, limit_price: float, sim: SimFill) -> Fill:
        if not sim.complete:
            raise ExecutionError("FOK não preenchida: profundidade insuficiente dentro do limite")
        fee = fee_for_levels(sim.levels, market.fee_rate, market.fee_exponent)
        return Fill(token_id, side, sim.shares, sim.avg_price, fee, limit_price)

"""Real trading through a Trader (the official SDK, see sdk.py), guarded by the geoblock check before every order."""
from __future__ import annotations

from typing import Callable, Protocol

from desk.clob import simulate_buy
from desk.executor.base import ExecutionError, ExecutionUncertain
from desk.fees import fee_per_share
from desk.geoblock import GeoStatus
from desk.models import Fill, Market, OrderBook


class Trader(Protocol):
    def place_fok(self, *, token_id: str, side: str, price: float, size: float, spend: float | None,
                  neg_risk: bool, tick_size: float) -> dict:
        """FOK order. BUY spends `spend` at prices up to `price`; SELL sells `size` at prices down to `price`.

        Raise only when the order may have reached the exchange; return {"success": False, ...} when it surely did not.
        """

    def usdc_balance(self) -> float: ...


class LiveExecutor:
    mode = "live"

    def __init__(self, trader: Trader, geoblock: Callable[[], GeoStatus]):
        self._trader = trader
        self._geoblock = geoblock

    def buy(self, market: Market, token_id: str, shares: float, limit_price: float, book: OrderBook) -> Fill:
        # Spend exactly what the paper walk of the same book costs; the limit only caps the price.
        sim = simulate_buy(book, shares, max_price=limit_price)
        if not sim.complete:
            raise ExecutionError("livro não cobre a compra dentro do limite")
        spend = sum(price * size for price, size in sim.levels)
        return self._place(market, token_id, "BUY", shares, limit_price, spend)

    def sell(self, market: Market, token_id: str, shares: float, limit_price: float, book: OrderBook) -> Fill:
        return self._place(market, token_id, "SELL", shares, limit_price, None)

    def _guard(self) -> None:
        status = self._geoblock()
        if status.blocked:
            detail = status.error or f"região bloqueada ({status.country or '?'})"
            raise ExecutionError(f"ordem real recusada pelo geobloqueio: {detail}")

    def _place(self, market: Market, token_id: str, side: str, shares: float, limit_price: float,
               spend: float | None) -> Fill:
        self._guard()
        try:
            resp = self._trader.place_fok(
                token_id=token_id, side=side, price=limit_price, size=shares, spend=spend,
                neg_risk=market.neg_risk, tick_size=market.tick_size,
            )
        except ExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001 - anything raised may have happened after the order was posted
            raise ExecutionUncertain(
                f"resultado incerto ({type(exc).__name__}: {exc}); confira a carteira no Polymarket"
            ) from exc
        if not isinstance(resp, dict):
            raise ExecutionError(f"resposta inesperada do CLOB: {resp!r}")
        status = str(resp.get("status", "")).lower()
        if resp.get("success") and status in ("live", "delayed"):
            raise ExecutionUncertain(f"ordem aceita sem confirmação de execução ({status}); confira no Polymarket")
        if not resp.get("success") or status != "matched":
            raise ExecutionError(f"ordem não executada: {resp.get('errorMsg') or resp.get('status')}")
        filled, usdc = self._amounts(resp, side)
        if filled <= 0:  # FOK matched means fully filled within the limit
            filled, usdc = shares, spend if spend is not None else shares * limit_price
        avg = usdc / filled
        fee = filled * fee_per_share(avg, market.fee_rate, market.fee_exponent)
        return Fill(token_id, side, filled, avg, fee, limit_price)

    @staticmethod
    def _amounts(resp: dict, side: str) -> tuple[float, float]:
        """(shares, usdc) from the CLOB response; BUY makes USDC/takes shares, SELL the opposite."""
        try:
            making = float(resp.get("makingAmount") or 0)
            taking = float(resp.get("takingAmount") or 0)
        except (TypeError, ValueError):
            return 0.0, 0.0
        return (taking, making) if side == "BUY" else (making, taking)

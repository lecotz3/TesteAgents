"""Real trading through Polymarket's CLOB API, guarded by the geoblock check before every order."""
from __future__ import annotations

import math
from typing import Callable, Protocol

from desk.executor.base import ExecutionError
from desk.fees import fee_per_share
from desk.geoblock import GeoStatus
from desk.models import Fill, Market, OrderBook

CLOB_HOST = "https://clob.polymarket.com"
POLYGON_CHAIN_ID = 137


class Trader(Protocol):
    def place_fok(self, *, token_id: str, side: str, price: float, size: float, neg_risk: bool, tick_size: float) -> dict: ...

    def usdc_balance(self) -> float: ...


def _floor2(x: float) -> float:
    return math.floor(x * 100 + 1e-9) / 100


class PyClobTrader:
    """Thin wrapper over py-clob-client (installed with `uv sync --extra live`)."""

    def __init__(self, private_key: str, funder: str | None, signature_type: int):
        try:
            from py_clob_client.client import ClobClient
        except ImportError as exc:
            raise ExecutionError("modo real requer o pacote extra: uv sync --extra live") from exc
        self._client = ClobClient(
            CLOB_HOST, key=private_key, chain_id=POLYGON_CHAIN_ID, signature_type=signature_type, funder=funder
        )
        self._client.set_api_creds(self._client.create_or_derive_api_creds())

    def place_fok(self, *, token_id: str, side: str, price: float, size: float, neg_risk: bool, tick_size: float) -> dict:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType, PartialCreateOrderOptions
        from py_clob_client.order_builder.constants import BUY, SELL

        # FOK amounts: BUY = USDC to spend, SELL = shares to sell; the CLOB accepts at most 2 decimals.
        amount = _floor2(size * price) if side == "BUY" else _floor2(size)
        args = MarketOrderArgs(
            token_id=token_id, amount=amount, side=BUY if side == "BUY" else SELL, price=price,
            order_type=OrderType.FOK,
        )
        options = PartialCreateOrderOptions(tick_size=f"{tick_size:g}", neg_risk=neg_risk)
        signed = self._client.create_market_order(args, options)
        return self._client.post_order(signed, OrderType.FOK)

    def usdc_balance(self) -> float:
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

        data = self._client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        return float(data.get("balance", 0)) / 1_000_000


class LiveExecutor:
    mode = "live"

    def __init__(self, trader: Trader, geoblock: Callable[[], GeoStatus]):
        self._trader = trader
        self._geoblock = geoblock

    def buy(self, market: Market, token_id: str, shares: float, limit_price: float, book: OrderBook) -> Fill:
        return self._place(market, token_id, "BUY", shares, limit_price)

    def sell(self, market: Market, token_id: str, shares: float, limit_price: float, book: OrderBook) -> Fill:
        return self._place(market, token_id, "SELL", shares, limit_price)

    def _guard(self) -> None:
        status = self._geoblock()
        if status.blocked:
            detail = status.error or f"região bloqueada ({status.country or '?'})"
            raise ExecutionError(f"ordem real recusada pelo geobloqueio: {detail}")

    def _place(self, market: Market, token_id: str, side: str, shares: float, limit_price: float) -> Fill:
        self._guard()
        try:
            resp = self._trader.place_fok(
                token_id=token_id, side=side, price=limit_price, size=shares,
                neg_risk=market.neg_risk, tick_size=market.tick_size,
            )
        except ExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001 - py-clob-client raises its own exception types
            raise ExecutionError(f"CLOB recusou a ordem: {type(exc).__name__}: {exc}") from exc
        if not isinstance(resp, dict):
            raise ExecutionError(f"resposta inesperada do CLOB: {resp!r}")
        if not resp.get("success") or str(resp.get("status", "")).lower() != "matched":
            raise ExecutionError(f"ordem não executada: {resp.get('errorMsg') or resp.get('status')}")
        filled, usdc = self._amounts(resp, side)
        if filled <= 0:
            filled, usdc = shares, shares * limit_price  # FOK matched means fully filled within the limit
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

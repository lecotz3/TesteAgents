"""Trader backed by Polymarket's official SDK (`polymarket-client`, Protocol V2, pUSD collateral)."""
from __future__ import annotations

import math
from typing import Any

from desk.executor.base import ExecutionError

COLLATERAL_UNIT = 1_000_000  # pUSD has 6 decimals; balances come back in base units


# SDK errors raised before the order reaches the matching engine, or an explicit 4xx refusal.
_PRE_TRADE_ERRORS = {"UserInputError", "InsufficientLiquidityError", "InsufficientAllowanceError", "SigningError",
                     "RateLimitError"}


def _nothing_was_posted(exc: Exception) -> bool:
    name = type(exc).__name__
    if name == "RequestRejectedError":
        return getattr(exc, "status", 500) < 500
    return name in _PRE_TRADE_ERRORS


def _floor2(x: float) -> str:
    return f"{math.floor(x * 100 + 1e-9) / 100:.2f}"


class SdkTrader:
    """Thin wrapper over `polymarket.SecureClient` (installed with `uv sync --extra live`)."""

    def __init__(self, client: Any):
        self._client = client

    @classmethod
    def connect(cls, private_key: str, wallet: str | None) -> SdkTrader:
        try:
            from polymarket import SecureClient
        except ImportError as exc:
            raise ExecutionError("modo real requer o pacote extra: uv sync --extra live") from exc
        return cls(SecureClient.create(private_key=private_key, wallet=wallet or None))

    def place_fok(self, *, token_id: str, side: str, price: float, size: float, spend: float | None,
                  neg_risk: bool, tick_size: float) -> dict:
        # The SDK resolves tick size and neg-risk from the market itself; amounts go as strings with 2 decimals.
        try:
            if side == "BUY":
                assert spend is not None, "BUY needs the planned spend"
                resp = self._client.place_market_order(
                    asset_id=token_id, side="BUY", amount=_floor2(spend), max_price=f"{price:g}", order_type="FOK"
                )
            else:
                resp = self._client.place_market_order(
                    asset_id=token_id, side="SELL", shares=_floor2(size), min_price=f"{price:g}", order_type="FOK"
                )
        except Exception as exc:  # noqa: BLE001 - classify by SDK error name; the SDK is an optional extra
            if _nothing_was_posted(exc):
                return {"success": False, "status": "rejected", "errorMsg": f"{type(exc).__name__}: {exc}"}
            raise
        if getattr(resp, "ok", False):
            return {
                "success": True,
                "status": resp.status,
                "makingAmount": str(resp.making_amount),
                "takingAmount": str(resp.taking_amount),
            }
        return {"success": False, "status": getattr(resp, "code", "unknown"), "errorMsg": getattr(resp, "message", repr(resp))}

    def usdc_balance(self) -> float:
        return self._client.get_balance_allowance(asset_type="COLLATERAL").balance / COLLATERAL_UNIT

    def account_problems(self) -> list[str]:
        problems = []
        if self._client.get_closed_only_mode():
            problems.append("conta em modo closed-only: o Polymarket só deixa fechar posições, não abrir")
        if not self._client.get_trading_approvals_state().is_fully_approved:
            problems.append("aprovações de trading pendentes: faça 1 trade manual pequeno pelo site")
        return problems

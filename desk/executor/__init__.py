"""Executor factory: paper by default; live only behind two explicit switches."""
from __future__ import annotations

import httpx

from desk.config import ConfigError, Secrets, Settings
from desk.executor.base import ExecutionError, Executor
from desk.executor.live import LiveExecutor
from desk.executor.paper import PaperExecutor
from desk.executor.sdk import SdkTrader
from desk.geoblock import check_geoblock

__all__ = ["LIVE_CONFIRMATION", "ExecutionError", "Executor", "make_executor"]

LIVE_CONFIRMATION = "I_UNDERSTAND"


def make_executor(settings: Settings, secrets: Secrets, http: httpx.Client) -> Executor:
    if settings.mode == "paper":
        return PaperExecutor()
    if secrets.desk_live != LIVE_CONFIRMATION:
        raise ConfigError('mode = "live" exige DESK_LIVE=I_UNDERSTAND no .env (segunda trava)')
    if secrets.poly_private_key is None:
        raise ConfigError('mode = "live" exige POLY_PRIVATE_KEY no .env')
    trader = SdkTrader.connect(secrets.poly_private_key.get_secret_value(), secrets.poly_wallet)
    return LiveExecutor(trader, lambda: check_geoblock(http))

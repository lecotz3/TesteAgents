"""The desk's own P&L statement: what it earned trading against what it cost to run."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from desk.config import Settings
from desk.store import Store


@dataclass(frozen=True)
class Economics:
    equity: float
    bankroll_start: float
    costs_total: float
    costs_24h: float
    trading_pnl: float  # equity change not explained by costs
    net: float  # equity - bankroll_start
    budget_24h: float  # how much it may spend per 24h at the current equity
    shutdown_line: float  # equity at which the drawdown rule switches it off
    burn_per_day: float  # average cost per day over the last 7 days
    runway_days: float | None  # days until the shutdown line at that burn, if trading earned nothing

    def as_dict(self) -> dict:
        return asdict(self)


def _cents(x: float) -> float:
    return round(x, 2) + 0.0  # + 0.0 turns -0.0 into 0.0 so "+0.00" never prints as "-0.00"


def economics(store: Store, settings: Settings, now: datetime) -> Economics:
    last = store.last_equity()
    equity = last if last is not None else store.cash()
    start = settings.bankroll_start_usd
    costs_total = store.total_costs()
    shutdown_line = store.peak() * (1 - settings.risk.drawdown_halt)
    burn = store.costs_since(now - timedelta(days=7)) / 7
    runway = (equity - shutdown_line) / burn if burn > 0 else None
    return Economics(
        equity=equity, bankroll_start=start, costs_total=costs_total,
        costs_24h=store.costs_since(now - timedelta(hours=24)),
        trading_pnl=_cents(equity - start + costs_total), net=_cents(equity - start),
        budget_24h=settings.economy.max_daily_burn_pct * max(equity, 0.0),
        shutdown_line=shutdown_line, burn_per_day=burn, runway_days=max(runway, 0.0) if runway is not None else None,
    )

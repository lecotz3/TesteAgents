"""The desk's own P&L statement: what it earned trading against what it cost to run."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from desk.config import Settings
from desk.store import Store

BROKE_USD = 1.0  # below this nothing can be traded any more: the money ran out


def shutdown_line(peak: float, drawdown_halt: float) -> float:
    """The equity at which an agent is switched off: drawdown_halt below its peak, and never under BROKE_USD
    (drawdown_halt = 1.0 means it only stops when the money runs out)."""
    return max(peak * (1 - drawdown_halt), BROKE_USD)


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
    runway_days: float | None  # days to the shutdown line spending the full budget daily and earning nothing

    def as_dict(self) -> dict:
        return asdict(self)


def _runway_days(equity: float, line: float, daily_pct: float, daily_rent: float = 0.0) -> float | None:
    """Days to the line earning nothing. Demand for analyses exceeds the budget, so it spends ~daily_pct of a
    shrinking equity each day, and never less than the fixed daily rent (which takes over once equity is small)."""
    if equity <= line:
        return 0.0
    if daily_pct >= 1:
        return 0.0
    knee = daily_rent / daily_pct if daily_pct > 0 else math.inf  # below this equity the rent is the bigger cost
    days = 0.0
    stop = max(line, knee)
    if equity > stop:
        if stop <= 0:
            return None  # a share of a shrinking equity never reaches zero
        days = math.log(stop / equity) / math.log(1 - daily_pct)
        equity = stop
    if equity > line:
        if daily_rent <= 0:
            return None
        days += (equity - line) / daily_rent
    return days


def _cents(x: float) -> float:
    return round(x, 2) + 0.0  # + 0.0 turns -0.0 into 0.0 so "+0.00" never prints as "-0.00"


def economics(store: Store, settings: Settings, now: datetime) -> Economics:
    last = store.last_equity()
    equity = last if last is not None else store.cash()
    start = settings.bankroll_start_usd
    costs_total = store.total_costs()
    line = shutdown_line(store.peak(), settings.risk.drawdown_halt)
    burn = store.costs_since(now - timedelta(days=7)) / 7
    runway = _runway_days(equity, line, settings.economy.max_daily_burn_pct, settings.daily_rent)
    return Economics(
        equity=equity, bankroll_start=start, costs_total=costs_total,
        costs_24h=store.costs_since(now - timedelta(hours=24)),
        trading_pnl=_cents(equity - start + costs_total), net=_cents(equity - start),
        budget_24h=settings.economy.max_daily_burn_pct * max(equity, 0.0),
        shutdown_line=line, burn_per_day=burn, runway_days=runway,
    )

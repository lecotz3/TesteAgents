"""Has paper trading earned real money yet? Days running, closed trades and the result after every cost."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from desk.config import MemesSettings
from desk.memes.store import MemeStore


@dataclass(frozen=True)
class Readiness:
    days: int
    closed: int
    wins: int
    net: float  # equity - starting bankroll: trades and operating costs together
    min_days: int
    min_trades: int

    @property
    def win_rate(self) -> float:
        return self.wins / self.closed if self.closed else 0.0

    @property
    def ready(self) -> bool:
        return self.days >= self.min_days and self.closed >= self.min_trades and self.net > 0

    @property
    def summary(self) -> str:
        verdict = "pronto para considerar dinheiro real" if self.ready else "ainda não"
        return (f"{min(self.days, self.min_days)}/{self.min_days} dias, {min(self.closed, self.min_trades)}/"
                f"{self.min_trades} trades fechados ({self.win_rate:.0%} com lucro), resultado ${self.net:+.2f} "
                f"depois dos custos: {verdict}")

    def as_dict(self) -> dict:
        return {**asdict(self), "win_rate": self.win_rate, "ready": self.ready, "summary": self.summary}


def readiness(store: MemeStore, s: MemesSettings, now: datetime) -> Readiness:
    first = store.first_equity_ts()
    last = store.last_equity()
    closed = store.closed_positions()
    return Readiness(
        days=max((now - first).days, 0) if first else 0,
        closed=len(closed),
        wins=sum(1 for p in closed if p.proceeds_usd > p.cost_usd),
        net=round((last if last is not None else store.cash()) - s.bankroll_start_usd, 2) + 0.0,
        min_days=s.readiness_min_days, min_trades=s.readiness_min_trades,
    )

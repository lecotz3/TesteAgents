"""Plain data types shared by every module."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class Market:
    id: str
    slug: str
    question: str
    description: str
    outcomes: tuple[str, str]
    token_ids: tuple[str, str]
    outcome_prices: tuple[float, float]
    end_date: datetime
    liquidity: float
    volume_24h: float
    enable_order_book: bool
    accepting_orders: bool
    closed: bool
    resolution_source: str
    category: str
    fee_rate: float
    fee_exponent: float
    min_order_size: float
    tick_size: float
    neg_risk: bool
    game_start: datetime | None = None  # sports: the result starts moving live at kickoff
    event_id: str | None = None  # sibling markets (e.g. Fed hold / Fed hike) share one event

    @property
    def trade_deadline(self) -> datetime:
        """When the desk must be out: kickoff for games (web research goes stale in-play), else the end date."""
        if self.game_start is None:
            return self.end_date
        return min(self.end_date, self.game_start)


@dataclass(frozen=True)
class Level:
    price: float
    size: float


@dataclass(frozen=True)
class OrderBook:
    token_id: str
    bids: tuple[Level, ...]  # best (highest) first
    asks: tuple[Level, ...]  # best (lowest) first

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None


@dataclass(frozen=True)
class Evidence:
    claim: str
    url: str
    published: str


@dataclass(frozen=True)
class Analysis:
    market_id: str
    fair_prob: float  # probability of outcome index 0
    official_resolution_source: str | None
    confidence: str  # "low" | "medium" | "high"
    evidence: tuple[Evidence, ...]
    exit_condition: str
    reasoning: str
    created_at: datetime
    backend: str

    def prob_of(self, outcome_index: int) -> float:
        return self.fair_prob if outcome_index == 0 else 1.0 - self.fair_prob


@dataclass(frozen=True)
class Fill:
    token_id: str
    side: str  # "BUY" | "SELL"
    shares: float
    avg_price: float
    fee: float
    limit_price: float

    @property
    def notional(self) -> float:
        return self.shares * self.avg_price


@dataclass
class Position:
    market_id: str
    slug: str
    question: str
    category: str
    outcome_index: int
    outcome: str
    token_id: str
    shares: float
    avg_price: float
    fee_paid: float
    entry_fair_prob: float  # probability of the held outcome at entry
    exit_condition: str
    resolution_source: str
    end_date: datetime
    opened_at: datetime
    id: int | None = None
    status: str = "open"
    closed_at: datetime | None = None
    exit_price: float | None = None
    proceeds: float | None = None
    close_reason: str | None = None
    event_id: str | None = None

    @property
    def cost(self) -> float:
        return self.shares * self.avg_price + self.fee_paid


@dataclass(frozen=True)
class TradeRecord:
    ts: datetime
    position_id: int
    market_id: str
    action: str  # "BUY" | "SELL" | "SETTLE"
    shares: float
    price: float
    fee: float
    cash_delta: float
    reason: str

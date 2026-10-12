"""Builders for test data shaped like the real Gamma / CLOB responses (observed 2026-10-12)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from desk.models import Analysis, Evidence, Level, Market, OrderBook, Position
from desk.notify import Notifier

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def raw_market(**overrides) -> dict:
    raw = {
        "id": "1001",
        "slug": "will-it-rain-in-nyc",
        "question": "Will it rain in NYC on Oct 14?",
        "description": "Resolves Yes if the NWS Central Park station records >= 0.01in of rain on Oct 14, 2026.",
        "outcomes": json.dumps(["Yes", "No"]),
        "clobTokenIds": json.dumps(["111", "222"]),
        "outcomePrices": json.dumps(["0.62", "0.38"]),
        "endDate": (NOW + timedelta(days=2)).isoformat().replace("+00:00", "Z"),
        "liquidityNum": 25000.0,
        "volume24hr": 12000.0,
        "enableOrderBook": True,
        "acceptingOrders": True,
        "closed": False,
        "resolutionSource": "https://www.weather.gov/",
        "feeType": "weather_fees",
        "feesEnabled": True,
        "feeSchedule": {"exponent": 1, "rate": 0.05, "takerOnly": True, "rebateRate": 0.25},
        "orderMinSize": 5,
        "orderPriceMinTickSize": 0.01,
        "negRisk": False,
    }
    raw.update(overrides)
    return raw


def make_market(**overrides) -> Market:
    base = dict(
        id="1001",
        slug="will-it-rain-in-nyc",
        question="Will it rain in NYC on Oct 14?",
        description="Resolves Yes if the NWS Central Park station records >= 0.01in of rain on Oct 14, 2026.",
        outcomes=("Yes", "No"),
        token_ids=("111", "222"),
        outcome_prices=(0.62, 0.38),
        end_date=NOW + timedelta(days=2),
        liquidity=25000.0,
        volume_24h=12000.0,
        enable_order_book=True,
        accepting_orders=True,
        closed=False,
        resolution_source="https://www.weather.gov/",
        category="weather",
        fee_rate=0.0,
        fee_exponent=1.0,
        min_order_size=5.0,
        tick_size=0.01,
        neg_risk=False,
    )
    base.update(overrides)
    return Market(**base)


def make_book(token_id: str = "111", bids=((0.61, 1000.0),), asks=((0.62, 1000.0),)) -> OrderBook:
    return OrderBook(
        token_id=token_id,
        bids=tuple(Level(p, s) for p, s in sorted(bids, key=lambda x: -x[0])),
        asks=tuple(Level(p, s) for p, s in sorted(asks, key=lambda x: x[0])),
    )


def make_analysis(market_id: str = "1001", fair_prob: float = 0.74, **overrides) -> Analysis:
    base = dict(
        market_id=market_id,
        fair_prob=fair_prob,
        official_resolution_source="NWS Central Park station",
        confidence="medium",
        evidence=(Evidence("NWS forecast 80% rain", "https://forecast.weather.gov/", "2026-10-12"),),
        exit_condition="Exit if the NWS forecast drops below 60% chance of rain.",
        reasoning="Forecast models agree on a front arriving Oct 14.",
        created_at=NOW,
        backend="fake",
    )
    base.update(overrides)
    return Analysis(**base)


def make_position(**overrides) -> Position:
    base = dict(
        market_id="1001",
        slug="will-it-rain-in-nyc",
        question="Will it rain in NYC on Oct 14?",
        category="weather",
        outcome_index=0,
        outcome="Yes",
        token_id="111",
        shares=9.67,
        avg_price=0.62,
        fee_paid=0.0,
        entry_fair_prob=0.74,
        exit_condition="Exit if the NWS forecast drops below 60% chance of rain.",
        resolution_source="NWS Central Park station",
        end_date=NOW + timedelta(days=2),
        opened_at=NOW - timedelta(hours=1),
    )
    base.update(overrides)
    return Position(**base)


class CapturingNotifier(Notifier):
    """Notifier that records messages instead of sending them."""

    def __init__(self):
        super().__init__()
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)


@dataclass
class SdkAccepted:
    status: str
    making_amount: Decimal
    taking_amount: Decimal
    ok: bool = True


@dataclass
class SdkRejected:
    code: str
    message: str
    ok: bool = False


@dataclass
class SdkBalance:
    balance: int
    allowances: dict


@dataclass
class SdkApprovals:
    is_fully_approved: bool
    missing: object = None


class FakeSdkClient:
    """Stands in for polymarket.SecureClient; records the keyword arguments of each order."""

    def __init__(self, response=None, balance=0, closed_only=False, approved=True):
        self.response = response
        self.balance = balance
        self.closed_only = closed_only
        self.approved = approved
        self.orders = []

    def place_market_order(self, **kwargs):
        self.orders.append(kwargs)
        return self.response

    def get_balance_allowance(self, *, asset_type):
        assert asset_type == "COLLATERAL"
        return SdkBalance(balance=self.balance, allowances={})

    def get_closed_only_mode(self):
        return self.closed_only

    def get_trading_approvals_state(self):
        return SdkApprovals(is_fully_approved=self.approved)

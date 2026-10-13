"""Value types for the memecoin agent. Token amounts stay as raw integers (no float rounding of balances)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDC_DECIMALS = 6


@dataclass(frozen=True)
class Pair:
    """The most liquid DEX pair of a token, from DexScreener."""

    mint: str
    symbol: str
    name: str
    pair_address: str
    dex: str
    price_usd: float
    liquidity_usd: float
    market_cap_usd: float
    volume_h1_usd: float
    buys_h1: int
    sells_h1: int
    change_h1_pct: float
    change_h6_pct: float
    created_at: datetime | None
    has_socials: bool
    url: str
    x_url: str | None = None  # the X post or profile the token links to
    organic_score: float | None = None  # Jupiter's 0-100 score of real (non-bot) trading, when known

    def age_minutes(self, now: datetime) -> float:
        return (now - self.created_at).total_seconds() / 60 if self.created_at else 0.0

    @property
    def buy_ratio_h1(self) -> float:
        total = self.buys_h1 + self.sells_h1
        return self.buys_h1 / total if total else 0.0


@dataclass(frozen=True)
class Safety:
    """What RugCheck says about the token contract, its liquidity and its holders."""

    mint_authority: str | None
    freeze_authority: str | None
    rugged: bool
    danger_risks: tuple[str, ...]
    lp_locked_pct: float
    top10_pct: float  # excluding pools and lockers
    insider_pct: float
    creator_pct: float
    transfer_fee_pct: float
    decimals: int
    score: int


@dataclass(frozen=True)
class Quote:
    in_amount: int
    out_amount: int
    price_impact_pct: float  # positive = worse
    fee_bps: int


@dataclass(frozen=True)
class MemeFill:
    side: str  # "BUY" | "SELL"
    amount_raw: int  # tokens bought or sold
    usd: float  # USDC spent (BUY) or received (SELL), after Jupiter's fee
    fee_usd: float  # network fee estimate
    signature: str | None = None


@dataclass
class MemePosition:
    mint: str
    symbol: str
    name: str
    decimals: int
    amount_raw: int
    cost_usd: float
    entry_price_usd: float
    peak_price_usd: float
    entry_liquidity_usd: float
    opened_at: datetime
    take_profit_done: bool = False
    id: int | None = None
    status: str = "open"
    closed_at: datetime | None = None
    proceeds_usd: float = 0.0
    close_reason: str | None = None
    last_price_usd: float | None = None  # last DexScreener price seen

    def tokens(self) -> float:
        return self.amount_raw / 10 ** self.decimals

    def value_at(self, price_usd: float) -> float:
        return self.tokens() * price_usd

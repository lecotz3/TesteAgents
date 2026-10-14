"""Deterministic rules: which tokens may be bought, how much, and when to sell. The LLM never overrides these."""
from __future__ import annotations

from datetime import datetime, timedelta

from desk.config import MemesSettings
from desk.memes.models import MemePosition, Pair, Safety


def market_rejection(p: Pair, now: datetime, s: MemesSettings) -> str | None:
    if p.liquidity_usd < s.min_liquidity_usd:
        return "low_liquidity"
    age = p.age_minutes(now)
    if p.created_at is None or age < s.min_age_minutes:
        return "too_new"  # the first hour belongs to snipers and insiders
    if age > s.max_age_hours * 60:
        return "too_old"
    if p.volume_h1_usd < s.min_volume_h1_usd:
        return "low_volume"
    if p.buys_h1 + p.sells_h1 < s.min_txns_h1:
        return "few_trades"
    if p.buy_ratio_h1 < s.min_buy_ratio_h1:
        return "selling_pressure"
    if p.organic_score is not None and p.organic_score < s.min_organic_score:
        return "low_organic_score"  # Jupiter sees mostly bots and wash trading
    if p.change_h1_pct > s.max_change_h1_pct:
        return "vertical_pump"
    if p.change_h1_pct < s.min_change_h1_pct:
        return "falling"
    if not s.min_market_cap_usd <= p.market_cap_usd <= s.max_market_cap_usd:
        return "market_cap_out_of_range"
    return None


def safety_rejection(x: Safety, s: MemesSettings) -> str | None:
    if x.rugged:
        return "rugged"
    if x.mint_authority:
        return "mint_authority"  # the creator can print more tokens
    if x.freeze_authority:
        return "freeze_authority"  # the creator can freeze your tokens: you could never sell
    if x.danger_risks:
        return "danger_risk"
    if x.transfer_fee_pct > 0:
        return "transfer_fee"
    if x.lp_locked_pct < s.min_lp_locked_pct:
        return "lp_unlocked"
    if x.top10_pct > s.max_top10_holders_pct:
        return "concentrated_holders"
    if x.insider_pct > s.max_insider_pct:
        return "insiders"
    if x.creator_pct > s.max_creator_pct:
        return "creator_holds"
    return None


def quote_rejection(*, round_trip_loss: float | None, price_impact_pct: float, s: MemesSettings) -> str | None:
    if round_trip_loss is None:
        return "cannot_sell"  # no route back to USDC: a honeypot or dead pool
    if round_trip_loss > s.max_round_trip_loss:
        return "round_trip_cost"
    if price_impact_pct > s.max_price_impact_pct:
        return "price_impact"
    return None


def position_size_usd(*, equity: float, cash: float, s: MemesSettings) -> float:
    """position_pct of equity, but never under the minimum trade while there is cash for it: an agent that must make
    money keeps trying until the money runs out instead of idling once 3% is less than $1."""
    size = min(max(equity * s.position_pct, s.min_trade_usd), cash)
    return round(size, 2) if size >= s.min_trade_usd else 0.0


def exit_decision(pos: MemePosition, pair: Pair | None, now: datetime, s: MemesSettings) -> tuple[str, float] | None:
    """(reason, fraction of the position to sell) or None. Without a fresh price only the time limit applies."""
    expired = now - pos.opened_at >= timedelta(hours=s.max_hold_hours)
    if pair is None:
        return ("max_hold", 1.0) if expired else None  # Jupiter can still route a token DexScreener stopped listing
    price, entry, peak = pair.price_usd, pos.entry_price_usd, max(pos.peak_price_usd, pair.price_usd)
    if pair.liquidity_usd < pos.entry_liquidity_usd * (1 - s.liquidity_drain_pct / 100):
        return ("liquidity_drained", 1.0)
    if price <= entry * (1 - s.stop_loss_pct / 100):
        return ("stop_loss", 1.0)
    if not pos.take_profit_done and price >= entry * (1 + s.take_profit_pct / 100):
        return ("take_profit", s.take_profit_fraction)
    if peak >= entry * (1 + s.trailing_activation_pct / 100) and price <= peak * (1 - s.trailing_stop_pct / 100):
        return ("trailing_stop", 1.0)
    if expired:
        return ("max_hold", 1.0)
    return None

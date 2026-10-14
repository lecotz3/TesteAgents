from datetime import timedelta

import pytest

from desk.config import MemesSettings
from desk.memes.rules import exit_decision, market_rejection, position_size_usd, quote_rejection, safety_rejection
from tests.factories import NOW
from tests.meme_factories import make_meme_position, make_pair, make_safety

S = MemesSettings()


def test_healthy_token_passes_every_filter():
    assert market_rejection(make_pair(), NOW, S) is None
    assert safety_rejection(make_safety(), S) is None
    assert quote_rejection(round_trip_loss=0.02, price_impact_pct=0.5, s=S) is None


@pytest.mark.parametrize("overrides,reason", [
    ({"liquidity_usd": 49_000.0}, "low_liquidity"),
    ({"created_at": NOW - timedelta(minutes=30)}, "too_new"),
    ({"created_at": NOW - timedelta(hours=80)}, "too_old"),
    ({"created_at": None}, "too_new"),
    ({"volume_h1_usd": 20_000.0}, "low_volume"),
    ({"buys_h1": 90, "sells_h1": 90}, "few_trades"),
    ({"buys_h1": 400, "sells_h1": 600}, "selling_pressure"),
    ({"change_h1_pct": 200.0}, "vertical_pump"),
    ({"change_h1_pct": -25.0}, "falling"),
    ({"market_cap_usd": 100_000.0}, "market_cap_out_of_range"),
    ({"market_cap_usd": 30_000_000.0}, "market_cap_out_of_range"),
])
def test_market_filters(overrides, reason):
    assert market_rejection(make_pair(**overrides), NOW, S) == reason


@pytest.mark.parametrize("overrides,reason", [
    ({"rugged": True}, "rugged"),
    ({"mint_authority": "Abc"}, "mint_authority"),
    ({"freeze_authority": "Abc"}, "freeze_authority"),
    ({"danger_risks": ("Single holder ownership",)}, "danger_risk"),
    ({"lp_locked_pct": 50.0}, "lp_unlocked"),
    ({"top10_pct": 45.0}, "concentrated_holders"),
    ({"insider_pct": 15.0}, "insiders"),
    ({"creator_pct": 8.0}, "creator_holds"),
    ({"transfer_fee_pct": 1.0}, "transfer_fee"),
])
def test_safety_filters(overrides, reason):
    assert safety_rejection(make_safety(**overrides), S) == reason


def test_quote_filters_catch_expensive_or_unsellable_tokens():
    assert quote_rejection(round_trip_loss=None, price_impact_pct=0.1, s=S) == "cannot_sell"
    assert quote_rejection(round_trip_loss=0.08, price_impact_pct=0.5, s=S) == "round_trip_cost"
    assert quote_rejection(round_trip_loss=0.02, price_impact_pct=3.0, s=S) == "price_impact"


def test_position_size_is_a_small_slice_of_equity_capped_by_cash():
    assert position_size_usd(equity=100.0, cash=100.0, s=S) == pytest.approx(3.0)
    assert position_size_usd(equity=100.0, cash=2.0, s=S) == pytest.approx(2.0)
    assert position_size_usd(equity=10.0, cash=10.0, s=S) == 1.0  # 3% is $0.30: it still makes the $1 minimum trade
    assert position_size_usd(equity=10.0, cash=0.5, s=S) == 0.0  # the money ran out


def price(p):  # position bought at 0.0015
    return make_pair(price_usd=p, liquidity_usd=100_000.0)


@pytest.mark.parametrize("pos_overrides,pair,expected", [
    ({}, price(0.0015), None),
    ({}, price(0.0010), ("stop_loss", 1.0)),  # -33%
    ({}, price(0.0031), ("take_profit", 0.5)),  # +107%: sell half
    ({"take_profit_done": True, "peak_price_usd": 0.0040}, price(0.0027), ("trailing_stop", 1.0)),  # -32% from peak
    ({"take_profit_done": True, "peak_price_usd": 0.0040}, price(0.0035), None),
    ({"opened_at": NOW - timedelta(hours=25)}, price(0.0016), ("max_hold", 1.0)),
    ({}, make_pair(price_usd=0.0016, liquidity_usd=40_000.0), ("liquidity_drained", 1.0)),
    ({}, None, None),  # no price this cycle: hold, never sell blind
    ({"opened_at": NOW - timedelta(hours=25)}, None, ("max_hold", 1.0)),  # ...unless it is past its time anyway
])
def test_exit_rules(pos_overrides, pair, expected):
    assert exit_decision(make_meme_position(**pos_overrides), pair, NOW, S) == expected


def test_trailing_stop_uses_the_new_peak_before_take_profit_too():
    pos = make_meme_position(peak_price_usd=0.0028)  # ran to +87% and came back
    assert exit_decision(pos, price(0.0019), NOW, S) == ("trailing_stop", 1.0)


def test_low_organic_score_is_rejected_but_unknown_is_not():
    assert market_rejection(make_pair(organic_score=30.0), NOW, S) == "low_organic_score"
    assert market_rejection(make_pair(organic_score=80.0), NOW, S) is None
    assert market_rejection(make_pair(organic_score=None), NOW, S) is None

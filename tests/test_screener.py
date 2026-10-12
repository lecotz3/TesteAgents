from datetime import timedelta

import pytest

from desk.config import ScanSettings
from desk.screener import pick_for_analysis, rejection_reason, screen
from tests.factories import NOW, make_market

S = ScanSettings()


@pytest.mark.parametrize("overrides,reason", [
    ({"closed": True}, "not_tradable"),
    ({"enable_order_book": False}, "not_tradable"),
    ({"accepting_orders": False}, "not_tradable"),
    ({"liquidity": 4999.0}, "low_liquidity"),
    ({"end_date": NOW + timedelta(hours=1, minutes=59)}, "resolves_too_soon"),
    ({"outcome_prices": (0.98, 0.02)}, "price_out_of_band"),
    ({"outcome_prices": (0.02, 0.98)}, "price_out_of_band"),
    ({"description": "   "}, "no_rules"),
])
def test_each_filter(overrides, reason):
    assert rejection_reason(make_market(**overrides), NOW, S, set()) == reason


def test_good_market_passes():
    assert rejection_reason(make_market(), NOW, S, set()) is None


def test_already_held_is_rejected():
    assert rejection_reason(make_market(), NOW, S, {"1001"}) == "already_held"


def test_category_skip_and_allow_lists():
    m = make_market(category="sports")
    assert rejection_reason(m, NOW, ScanSettings(skip_categories=["sports"]), set()) == "skipped_category"
    assert rejection_reason(m, NOW, ScanSettings(allow_categories=["weather"]), set()) == "category_not_allowed"
    assert rejection_reason(m, NOW, ScanSettings(allow_categories=["sports"]), set()) is None


def test_screen_keeps_only_passing():
    good, bad = make_market(id="1"), make_market(id="2", liquidity=10.0)
    assert screen([good, bad], NOW, S, set()) == [good]


def test_pick_for_analysis_orders_by_volume_and_skips_fresh_and_cooldown():
    markets = [make_market(id=str(i), volume_24h=float(i)) for i in range(10)]
    picked = pick_for_analysis(markets, fresh_ids={"9"}, cooldown_ids={"8"}, budget=3)
    assert [m.id for m in picked] == ["7", "6", "5"]
    assert pick_for_analysis(markets, set(), set(), 0) == []

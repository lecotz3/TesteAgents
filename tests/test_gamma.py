from datetime import datetime, timezone

import httpx
import pytest
import respx

from desk.gamma import GammaClient, category_from_fee_type, parse_market
from tests.factories import raw_market

GAMMA = "gamma-api.polymarket.com"


def test_parse_market_reads_real_field_shapes():
    m = parse_market(raw_market())
    assert m is not None
    assert m.outcomes == ("Yes", "No")
    assert m.token_ids == ("111", "222")
    assert m.outcome_prices == (0.62, 0.38)
    assert m.end_date.tzinfo is not None
    assert m.category == "weather"
    assert m.fee_rate == 0.05 and m.fee_exponent == 1.0
    assert m.min_order_size == 5.0 and m.tick_size == 0.01


def test_fees_disabled_means_zero_rate():
    assert parse_market(raw_market(feesEnabled=False)).fee_rate == 0.0


def test_fees_enabled_without_schedule_uses_default_rate():
    assert parse_market(raw_market(feeSchedule=None)).fee_rate == 0.05


@pytest.mark.parametrize("overrides", [
    {"outcomes": '["A", "B", "C"]'},
    {"clobTokenIds": None},
    {"endDate": None},
    {"outcomePrices": "not json"},
])
def test_malformed_or_non_binary_markets_are_dropped(overrides):
    assert parse_market(raw_market(**overrides)) is None


@pytest.mark.parametrize("fee_type,expected", [
    ("politics_fees", "politics"),
    ("sports_fees_nfl_cfb_oct26", "sports"),
    ("sports_fees_v2", "sports"),
    ("economics_fees", "economics"),
    (None, "other"),
    ("", "other"),
])
def test_category_from_fee_type(fee_type, expected):
    assert category_from_fee_type(fee_type) == expected


@respx.mock
def test_active_markets_paginates_until_short_page():
    calls = []

    def handler(request):
        offset = int(request.url.params["offset"])
        calls.append((offset, int(request.url.params["limit"]), request.url.params["order"]))
        count = 100 if offset == 0 else 3
        return httpx.Response(200, json=[raw_market(id=str(offset + i)) for i in range(count)])

    respx.get(host=GAMMA, path="/markets").mock(side_effect=handler)
    with httpx.Client() as http:
        markets = GammaClient(http).active_markets(1000)
    assert len(markets) == 103
    assert calls == [(0, 100, "volume24hr"), (100, 100, "volume24hr")]


@respx.mock
def test_active_markets_respects_limit_and_dedupes():
    def handler(request):
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        return httpx.Response(200, json=[raw_market(id=str(i % 120)) for i in range(offset, offset + limit)])

    respx.get(host=GAMMA, path="/markets").mock(side_effect=handler)
    with httpx.Client() as http:
        markets = GammaClient(http).active_markets(150)
    assert len(markets) == 120


@respx.mock
def test_market_by_id_and_slug():
    respx.get(host=GAMMA, path="/markets/1001").mock(return_value=httpx.Response(200, json=raw_market()))
    respx.get(host=GAMMA, path="/markets/999").mock(return_value=httpx.Response(404))
    respx.get(host=GAMMA, path="/markets", params={"slug": "will-it-rain-in-nyc"}).mock(
        return_value=httpx.Response(200, json=[raw_market()])
    )
    with httpx.Client() as http:
        gamma = GammaClient(http)
        assert gamma.market_by_id("1001").id == "1001"
        assert gamma.market_by_id("999") is None
        assert gamma.market_by_slug("will-it-rain-in-nyc").slug == "will-it-rain-in-nyc"


def test_sports_market_trades_only_until_game_start():
    m = parse_market(raw_market(gameStartTime="2026-10-12 21:00:00+00"))
    assert m.game_start == datetime(2026, 10, 12, 21, 0, tzinfo=timezone.utc)
    assert m.trade_deadline == m.game_start  # endDate is two days later


def test_market_without_game_trades_until_end_date():
    m = parse_market(raw_market())
    assert m.game_start is None and m.trade_deadline == m.end_date


def test_event_id_groups_sibling_markets():
    assert parse_market(raw_market(events=[{"id": "606422", "slug": "fed-decision"}])).event_id == "606422"
    assert parse_market(raw_market(negRiskMarketID="0x09ac")).event_id == "0x09ac"
    assert parse_market(raw_market()).event_id is None

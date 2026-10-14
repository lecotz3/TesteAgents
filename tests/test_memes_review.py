import json
from datetime import timedelta

import httpx
import pytest
import respx

from desk.config import MemesSettings
from desk.memes.review import (
    ROUND_TRIP_COST,
    GeckoTerminal,
    Outcome,
    format_report,
    reviewable,
    review,
    simulate,
    summarize,
)
from desk.memes.sources import PacedApi
from tests.factories import NOW
from tests.meme_factories import MINT, make_meme_position

GECKO = "https://api.geckoterminal.com/api/v2"
POOL = "PooL1111111111111111111111111111111111111111"
S = MemesSettings()
MIN = timedelta(minutes=1)


def series(*prices, start=NOW, step=MIN):
    return [(start + i * step, p) for i, p in enumerate(prices)]


# --- the replay ---------------------------------------------------------------------------------------------------
def test_stop_loss_sells_everything_at_the_first_close_30pct_below_entry():
    ret, why = simulate(series(1.0, 0.9, 0.69, 5.0), NOW, 100_000.0, S)
    assert why == "stop_loss"
    assert ret == pytest.approx(0.69 - 1 - ROUND_TRIP_COST)


def test_take_profit_sells_half_then_the_trailing_stop_sells_the_rest():
    ret, why = simulate(series(1.0, 2.0, 2.5, 1.7), NOW, 100_000.0, S)
    assert why == "trailing_stop"
    assert ret == pytest.approx(0.5 * 2.0 + 0.5 * 1.7 - 1 - ROUND_TRIP_COST)


def test_flat_price_is_sold_by_the_time_limit():
    closes = series(*([1.0] * 30), step=timedelta(hours=1))
    ret, why = simulate(closes, NOW, 100_000.0, S)
    assert why == "max_hold"
    assert ret == pytest.approx(-ROUND_TRIP_COST)


def test_a_window_whose_trading_stopped_is_sold_at_the_last_price():
    ret, why = simulate(series(1.0, 1.1, 0.95), NOW, 100_000.0, S)
    assert why == "max_hold"
    assert ret == pytest.approx(0.95 - 1 - ROUND_TRIP_COST)


def test_no_candles_means_no_result():
    assert simulate([], NOW, 100_000.0, S) is None


# --- GeckoTerminal ------------------------------------------------------------------------------------------------
def gecko(http, page_size=1000):
    return GeckoTerminal(PacedApi(http, GECKO, min_interval=0), page_size=page_size)


def pools_payload(*pools):
    return {"data": [{"id": f"solana_{addr}", "type": "pool", "attributes": {"address": addr, "reserve_in_usd": str(r)}}
                     for addr, r in pools]}


def ohlcv(*rows):
    return {"data": {"attributes": {"ohlcv_list": [[int(t.timestamp()), p, p, p, p, 10.0] for t, p in rows]}}}


@respx.mock
def test_main_pool_is_the_one_with_the_most_liquidity():
    respx.get(f"{GECKO}/networks/solana/tokens/{MINT}/pools").mock(
        return_value=httpx.Response(200, json=pools_payload(("small", 900.5), (POOL, 120000.0), ("mid", 5000))))
    with httpx.Client() as http:
        assert gecko(http).main_pool(MINT) == POOL


@respx.mock
def test_unknown_token_has_no_pool():
    respx.get(f"{GECKO}/networks/solana/tokens/{MINT}/pools").mock(return_value=httpx.Response(404, json={}))
    with httpx.Client() as http:
        assert gecko(http).main_pool(MINT) is None


@respx.mock
def test_closes_page_backwards_and_come_back_oldest_first_inside_the_window():
    start, end = NOW, NOW + 5 * MIN
    route = respx.get(f"{GECKO}/networks/solana/pools/{POOL}/ohlcv/minute")
    route.side_effect = [
        httpx.Response(200, json=ohlcv((NOW + 4 * MIN, 1.4), (NOW + 3 * MIN, 1.3))),
        httpx.Response(200, json=ohlcv((NOW + 2 * MIN, 1.2), (NOW + MIN, 1.1))),
        httpx.Response(200, json=ohlcv((NOW, 1.0), (NOW - MIN, 0.9))),
    ]
    with httpx.Client() as http:
        closes = gecko(http, page_size=2).closes(POOL, MINT, start, end)
    assert closes == series(1.0, 1.1, 1.2, 1.3, 1.4)
    first = route.calls[0].request.url.params
    assert first["token"] == MINT and first["currency"] == "usd" and first["aggregate"] == "1"
    assert first["before_timestamp"] == str(int(end.timestamp()))
    assert route.calls[1].request.url.params["before_timestamp"] == str(int((NOW + 3 * MIN).timestamp()))


# --- which decisions are reviewed -----------------------------------------------------------------------------------
def decision(name, mint=MINT, ts=NOW, symbol="SEALS", price=1.0, liquidity=100_000.0):
    return {"ts": ts, "mint": mint, "symbol": symbol, "price_usd": price, "liquidity_usd": liquidity,
            "market_cap_usd": 1e6, "decision": name, "detail": ""}


def test_operational_decisions_are_left_out_and_repeats_keep_the_first():
    picked = reviewable([
        decision("vetter_avoid", ts=NOW), decision("vetter_avoid", ts=NOW + timedelta(hours=30)),
        decision("vet_error"), decision("buy_failed"), decision("buy_uncertain"), decision("wallet_usdc_short"),
        decision("bought", mint="other"), decision("lp_unlocked", mint="third"),
    ])
    assert [(d["mint"], d["decision"], d["ts"]) for d in picked] == [
        (MINT, "vetter_avoid", NOW), ("other", "bought", NOW), ("third", "lp_unlocked", NOW)]


# --- the review run -------------------------------------------------------------------------------------------------
class FakeGecko:
    def __init__(self, pools=None, closes=None, fail=None):
        self.pools, self.by_pool, self.fail = pools or {}, closes or {}, fail
        self.calls = 0

    def main_pool(self, mint):
        self.calls += 1
        if self.fail:
            raise self.fail
        return self.pools.get(mint)

    def closes(self, pool, mint, start, end):
        self.calls += 1
        return [(t, p) for t, p in self.by_pool.get(pool, []) if start <= t < end]


def test_review_replays_finished_windows_and_counts_young_ones_as_open(tmp_path):
    now = NOW + timedelta(hours=30)
    fake = FakeGecko(pools={MINT: POOL}, closes={POOL: series(1.0, 0.5)})
    outcomes = review([decision("bought"), decision("vetter_avoid", mint="young", ts=now - timedelta(hours=2))],
                      fake, tmp_path / "memes-review.json", S, now)
    by = {o.decision: o for o in outcomes}
    assert by["bought"].status == "closed" and by["bought"].exit == "stop_loss"
    assert by["bought"].ret == pytest.approx(0.5 - 1 - ROUND_TRIP_COST)
    assert by["vetter_avoid"].status == "open" and by["vetter_avoid"].ret is None


def test_finished_windows_are_cached_and_never_fetched_again(tmp_path):
    now, cache = NOW + timedelta(hours=30), tmp_path / "memes-review.json"
    fake = FakeGecko(pools={MINT: POOL}, closes={POOL: series(1.0, 0.5)})
    first = review([decision("bought"), decision("lp_unlocked", mint="gone")], fake, cache, S, now)
    calls = fake.calls
    again = review([decision("bought"), decision("lp_unlocked", mint="gone")], fake, cache, S, now)
    assert fake.calls == calls
    assert again == first
    assert {o.status for o in again} == {"closed", "no_data"}
    assert json.loads(cache.read_text(encoding="utf-8"))["rules"]


def test_changing_the_exit_rules_discards_the_cache(tmp_path):
    now, cache = NOW + timedelta(hours=30), tmp_path / "memes-review.json"
    fake = FakeGecko(pools={MINT: POOL}, closes={POOL: series(1.0, 0.75)})
    assert review([decision("bought")], fake, cache, S, now)[0].exit == "max_hold"
    tighter = S.model_copy(update={"stop_loss_pct": 20})
    assert review([decision("bought")], fake, cache, tighter, now)[0].exit == "stop_loss"


def test_a_failed_fetch_is_reported_and_retried_next_time(tmp_path):
    now, cache = NOW + timedelta(hours=30), tmp_path / "memes-review.json"
    out = review([decision("bought")], FakeGecko(fail=httpx.ConnectError("down")), cache, S, now)
    assert out[0].status == "error"
    fixed = FakeGecko(pools={MINT: POOL}, closes={POOL: series(1.0, 0.5)})
    assert review([decision("bought")], fixed, cache, S, now)[0].status == "closed"


def test_each_run_fetches_at_most_max_fetch_new_windows(tmp_path):
    now = NOW + timedelta(hours=30)
    fake = FakeGecko(pools={f"m{i}": POOL for i in range(3)}, closes={POOL: series(1.0, 0.5)})
    out = review([decision("bought", mint=f"m{i}") for i in range(3)], fake, tmp_path / "r.json", S, now, max_fetch=2)
    assert [o.status for o in out] == ["closed", "closed", "pending"]


# --- the report -----------------------------------------------------------------------------------------------------
def outcome(name, ret, status="closed", mint=MINT, symbol="SEALS", exit_="stop_loss"):
    return Outcome(mint=mint, symbol=symbol, decision=name, ts=NOW, status=status, ret=ret,
                   exit=exit_ if status == "closed" else None)


def test_summary_per_decision_reason():
    rows = {r.decision: r for r in summarize([
        outcome("vetter_avoid", -0.5), outcome("vetter_avoid", 0.3), outcome("vetter_avoid", -0.1),
        outcome("vetter_avoid", None, status="open"), outcome("bought", 1.2), outcome("lp_unlocked", None, "no_data"),
    ])}
    veto = rows["vetter_avoid"]
    assert (veto.count, veto.wins, veto.open) == (3, 1, 1)
    assert veto.average == pytest.approx(-0.1) and veto.worst == -0.5 and veto.best == 0.3
    assert rows["lp_unlocked"].count == 0 and rows["lp_unlocked"].no_data == 1 and rows["lp_unlocked"].average is None
    assert list(rows)[0] == "bought"  # buys first, then the filters in pipeline order


def test_report_explains_how_to_read_it_and_checks_the_real_paper_trades():
    real = make_meme_position(cost_usd=3.0, proceeds_usd=4.5, status="closed", close_reason="trailing_stop")
    text = format_report([outcome("bought", 0.48, exit_="trailing_stop"), outcome("vetter_avoid", -0.4)], [real], S)
    assert "Comprou" in text and "Claude vetou" in text
    assert "-40%" in text and "+48%" in text
    assert "evitou" in text  # how to read a filter's average
    assert "SEALS" in text and "real +50%" in text and "simulado +48%" in text

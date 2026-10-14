from datetime import timedelta

import pytest

from desk.config import MemesSettings
from desk.memes.movers import biggest_moves, day_peaks, parse_mover
from tests.factories import NOW

S = MemesSettings()
FPAD = "8L9bgqk34bsM1QVWQPuxHbvFAorg25wCjgGKh2cQpump"


def jup_item(mint=FPAD, symbol="FPAD", age=timedelta(hours=1), change_1h=79.07, **o):
    """Shaped like Jupiter's /tokens/v2/toptraded/1h answer for FPAD (observed 2026-10-13)."""
    created = (NOW - age).isoformat().replace("+00:00", "Z")
    stats = lambda change, buy, sell, buys, sells, traders: {  # noqa: E731
        "priceChange": change, "buyVolume": buy, "sellVolume": sell, "numBuys": buys, "numSells": sells,
        "numTraders": traders, "holderChange": 6.9}
    item = {
        "id": mint, "symbol": symbol, "name": "fPad", "usdPrice": 0.000734, "mcap": 731090.0, "liquidity": 37416.0,
        "holderCount": 1245, "organicScore": 0, "createdAt": created, "firstPool": {"id": "pool", "createdAt": created},
        "twitter": "https://x.com/fpad", "stats5m": stats(5.24, 32228.1, 33400.9, 4155, 1548, 288),
        "stats1h": stats(change_1h, 688227.5, 712932.7, 67656, 16229, 1066),
        "stats6h": stats(67.64, 776693.7, 772911.2, 67909, 16323, 1078),
        "stats24h": stats(67.64, 776693.7, 772911.2, 67909, 16323, 1078),
    }
    item.update(o)
    return item


def test_parse_mover_reads_jupiter_stats():
    m = parse_mover(jup_item(), NOW, S)
    assert m.mint == FPAD and m.symbol == "FPAD" and m.age_hours == pytest.approx(1.0)
    assert m.change_5m == pytest.approx(5.24) and m.change_1h == pytest.approx(79.07) and m.change_24h == pytest.approx(67.64)
    assert m.volume_1h == pytest.approx(688227.5 + 712932.7) and m.traders_1h == 1066
    assert m.mcap == 731090.0 and m.liquidity == 37416.0 and m.holders == 1245 and m.organic_score == 0
    assert m.link == f"https://fomo.family/tokens/solana/{FPAD}"


def test_the_robot_says_why_it_would_skip_a_mover():
    assert parse_mover(jup_item(), NOW, S).verdict == "low_liquidity"  # $37k of liquidity, below the $50k floor
    calm = jup_item(liquidity=400_000.0, organicScore=80, age=timedelta(hours=5), change_1h=12.0,
                    mcap=2_000_000.0)
    assert parse_mover(calm, NOW, S).verdict is None


@pytest.mark.parametrize("broken", [{"stats1h": None}, {"id": None}, {"mcap": "x"}])
def test_unusable_items_are_skipped(broken):
    assert parse_mover(jup_item(**broken), NOW, S) is None


def test_biggest_moves_are_young_tokens_ranked_by_the_size_of_the_1h_move():
    items = [
        jup_item("A", "A", change_1h=40.0), jup_item("B", "B", change_1h=-65.0), jup_item("C", "C", change_1h=5.0),
        jup_item("OLD", "ZEC", age=timedelta(days=400), change_1h=90.0),  # an old major, not a memecoin
        jup_item("A", "A", change_1h=40.0),  # the same token in two Jupiter lists
    ]
    moves = biggest_moves(items, NOW, S.model_copy(update={"movers_keep": 2}))
    assert [m.symbol for m in moves] == ["B", "A"]


def test_day_peaks_keep_each_tokens_biggest_hour_and_its_market_cap_now():
    rows = [
        {"ts": NOW - timedelta(hours=5), "mint": "A", "symbol": "A", "mcap": 1e6, "change_1h": 120.0, "volume_1h": 5e5},
        {"ts": NOW - timedelta(hours=4), "mint": "A", "symbol": "A", "mcap": 3e6, "change_1h": 300.0, "volume_1h": 9e5},
        {"ts": NOW - timedelta(minutes=3), "mint": "A", "symbol": "A", "mcap": 0.8e6, "change_1h": -20.0, "volume_1h": 1e5},
        {"ts": NOW - timedelta(hours=2), "mint": "B", "symbol": "B", "mcap": 2e5, "change_1h": 60.0, "volume_1h": 4e4},
    ]
    a, b = day_peaks(rows, limit=10)
    assert (a["symbol"], a["peak_change_1h"], a["peak_mcap"], a["mcap_now"]) == ("A", 300.0, 3e6, 0.8e6)
    assert a["peak_at"] == NOW - timedelta(hours=4) and a["last_seen"] == NOW - timedelta(minutes=3)
    assert b["symbol"] == "B" and b["mcap_now"] == 2e5

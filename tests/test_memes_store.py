from datetime import timedelta

import pytest

from desk.memes.models import MemeFill
from desk.memes.store import MemeStore
from desk.store import ModeMismatch
from tests.factories import NOW
from tests.meme_factories import MINT, make_meme_position


@pytest.fixture
def store(tmp_path):
    s = MemeStore(tmp_path / "memes.db", start_cash=100.0)
    yield s
    s.close()


def buy_fill(**o):
    return MemeFill(**{"side": "BUY", "amount_raw": 2_000_000_000, "usd": 3.0, "fee_usd": 0.002, **o})


def test_open_position_debits_cash_and_records_the_trade(store):
    pid = store.open_position(make_meme_position(cost_usd=3.002), buy_fill(), NOW, "aprovado")
    [pos] = store.open_positions()
    assert pos.id == pid and pos.amount_raw == 2_000_000_000 and pos.opened_at == NOW - timedelta(hours=1)
    assert store.cash() == pytest.approx(100.0 - 3.002)
    [t] = store.trades()
    assert t["side"] == "BUY" and t["usd"] == 3.0 and t["cash_delta"] == pytest.approx(-3.002)
    assert t["amount_raw"] == 2_000_000_000 and t["mint"] == MINT


def test_huge_raw_amounts_survive_the_roundtrip(store):
    big = 10**20  # more than SQLite's 64-bit integers hold
    store.open_position(make_meme_position(amount_raw=big), buy_fill(amount_raw=big), NOW, "x")
    assert store.open_positions()[0].amount_raw == big


def test_partial_sell_keeps_position_open_and_credits_cash(store):
    pid = store.open_position(make_meme_position(cost_usd=3.002), buy_fill(), NOW, "x")
    pos = store.open_positions()[0]
    sell = MemeFill("SELL", 1_000_000_000, 3.1, 0.002)
    store.reduce_position(pos, sell, NOW + timedelta(hours=1), "take_profit", price_usd=0.0031)
    [after] = store.open_positions()
    assert after.id == pid and after.amount_raw == 1_000_000_000
    assert after.take_profit_done and after.proceeds_usd == pytest.approx(3.098)
    assert store.cash() == pytest.approx(100.0 - 3.002 + 3.098)


def test_selling_everything_closes_the_position(store):
    store.open_position(make_meme_position(cost_usd=3.002), buy_fill(), NOW, "x")
    pos = store.open_positions()[0]
    store.reduce_position(pos, MemeFill("SELL", 2_000_000_000, 2.0, 0.002), NOW, "stop_loss", price_usd=0.001)
    assert store.open_positions() == []
    [closed] = store.closed_positions()
    assert closed.status == "closed" and closed.close_reason == "stop_loss" and closed.closed_at == NOW
    assert closed.amount_raw == 0 and closed.proceeds_usd == pytest.approx(1.998)
    assert [t["side"] for t in store.trades()] == ["SELL", "BUY"]


def test_write_off_closes_without_cash(store):
    store.open_position(make_meme_position(), buy_fill(), NOW, "x")
    pos = store.open_positions()[0]
    cash = store.cash()
    store.write_off(pos, NOW, "missing_in_wallet")
    assert store.open_positions() == [] and store.cash() == cash
    assert store.closed_positions()[0].close_reason == "missing_in_wallet"


def test_mark_price_tracks_last_price_and_raises_peak_only(store):
    store.open_position(make_meme_position(), buy_fill(), NOW, "x")
    pos = store.open_positions()[0]
    assert pos.last_price_usd is None
    store.mark_price(pos.id, 0.004)
    store.mark_price(pos.id, 0.003)
    after = store.open_positions()[0]
    assert after.peak_price_usd == 0.004 and after.last_price_usd == 0.003


def test_vets_roundtrip(store):
    store.save_vet(MINT, NOW, "avoid", {"summary": "copycat"})
    assert store.get_vet(MINT) == {"ts": NOW, "verdict": "avoid", "summary": "copycat"}
    assert store.get_vet("nope") is None


def test_decisions_use_token_fields(store):
    store.record_cycle(NOW, {"equity": 100.0}, [
        {"mint": MINT, "symbol": "SEALS", "price_usd": 0.0015, "liquidity_usd": 1e5, "market_cap_usd": 1e6,
         "decision": "lp_unlocked"},
    ])
    [d] = store.recent_decisions(5)
    assert d["symbol"] == "SEALS" and d["decision"] == "lp_unlocked" and d["detail"] == ""


def test_mode_is_pinned(store):
    store.ensure_mode("paper")
    with pytest.raises(ModeMismatch):
        store.ensure_mode("live")


def test_position_keeps_its_x_link(store):
    store.open_position(make_meme_position(x_url="https://x.com/a/status/1"), buy_fill(), NOW, "x")
    assert store.open_positions()[0].x_url == "https://x.com/a/status/1"


def test_old_database_gets_the_x_link_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, symbol TEXT NOT NULL, "
               "name TEXT NOT NULL, decimals INTEGER NOT NULL, amount_raw TEXT NOT NULL, cost_usd REAL NOT NULL, "
               "entry_price_usd REAL NOT NULL, peak_price_usd REAL NOT NULL, entry_liquidity_usd REAL NOT NULL, "
               "opened_at TEXT NOT NULL, take_profit_done INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'open', "
               "closed_at TEXT, proceeds_usd REAL NOT NULL DEFAULT 0, close_reason TEXT, last_price_usd REAL)")
    db.commit()
    db.close()
    s = MemeStore(path, start_cash=100.0)
    s.open_position(make_meme_position(x_url="https://x.com/b"), buy_fill(), NOW, "x")
    assert s.open_positions()[0].x_url == "https://x.com/b"
    s.close()


def test_read_only_view_of_an_unmigrated_database_still_works(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    s = MemeStore(path, start_cash=100.0)
    s.open_position(make_meme_position(), buy_fill(), NOW, "x")
    s.close()
    db = sqlite3.connect(path)
    db.execute("ALTER TABLE positions DROP COLUMN x_url")  # as if written by the previous version
    db.commit()
    db.close()
    ro = MemeStore.open_readonly(path)
    assert ro.open_positions()[0].x_url is None
    ro.close()

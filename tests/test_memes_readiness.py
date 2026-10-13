from datetime import timedelta

import pytest

from desk.config import MemesSettings
from desk.memes.models import MemeFill
from desk.memes.readiness import readiness
from desk.memes.store import MemeStore
from tests.factories import NOW
from tests.meme_factories import make_meme_position


@pytest.fixture
def store(tmp_path):
    s = MemeStore(tmp_path / "memes.db", start_cash=100.0)
    yield s
    s.close()


def close_trade(store, pnl, when):
    pos = make_meme_position(opened_at=when)
    store.open_position(pos, MemeFill("BUY", pos.amount_raw, 3.0, 0.0), when, "x")
    store.reduce_position(pos, MemeFill("SELL", pos.amount_raw, 3.0 + pnl, 0.0), when + timedelta(hours=1), "stop_loss",
                          price_usd=0.001)


def test_fresh_agent_is_far_from_ready(store):
    r = readiness(store, MemesSettings(), NOW)
    assert not r.ready and r.days == 0 and r.closed == 0
    assert "0/14 dias" in r.summary and "0/30 trades" in r.summary


def test_ready_after_enough_days_trades_and_profit(store):
    start = NOW - timedelta(days=15)
    store.record_equity(start, 100.0, 100.0, 0.0)
    for i in range(30):
        close_trade(store, 0.5 if i % 3 else -0.5, start + timedelta(hours=8 * i))
    store.record_equity(NOW, 105.0, 105.0, 0.0)
    r = readiness(store, MemesSettings(), NOW)
    assert r.ready and r.days == 15 and r.closed == 30 and r.wins == 20
    assert r.net == pytest.approx(5.0) and r.win_rate == pytest.approx(20 / 30)
    assert "pronto" in r.summary.lower()


def test_losing_paper_is_never_ready(store):
    start = NOW - timedelta(days=20)
    store.record_equity(start, 100.0, 100.0, 0.0)
    for i in range(40):
        close_trade(store, -0.2, start + timedelta(hours=8 * i))
    store.record_equity(NOW, 92.0, 92.0, 0.0)
    r = readiness(store, MemesSettings(), NOW)
    assert not r.ready and "resultado" in r.summary

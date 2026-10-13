import math

import pytest

from desk.config import EconomySettings, Settings
from desk.economy import economics
from desk.store import Store
from tests.factories import NOW


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "desk.db", start_cash=100.0)
    yield s
    s.close()


def test_runway_assumes_it_keeps_spending_its_full_budget(store):
    store.charge(NOW, "rent", 0.67, "aluguel")
    store.record_equity(NOW, 99.33, 99.33, 0.0)
    e = economics(store, Settings(economy=EconomySettings(max_daily_burn_pct=0.015)), NOW)
    assert e.shutdown_line == pytest.approx(60.0)
    # Spending 1.5% of a shrinking equity every day: 99.33 * 0.985^d = 60
    assert e.runway_days == pytest.approx(math.log(60 / 99.33) / math.log(0.985))
    assert e.trading_pnl == 0.0 and e.net == pytest.approx(-0.67)


def test_runway_is_zero_below_the_line(store):
    store.record_equity(NOW, 100.0, 100.0, 0.0)
    store.record_equity(NOW, 55.0, 55.0, 0.0)
    assert economics(store, Settings(), NOW).runway_days == 0.0

from datetime import timedelta

import pytest

from desk.models import TradeRecord
from desk.store import ModeMismatch, Store
from tests.factories import NOW, make_analysis, make_position


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "desk.db", start_cash=100.0)
    yield s
    s.close()


def test_initial_state(store):
    assert store.cash() == 100.0 and store.peak() == 100.0
    assert not store.is_halted() and store.consecutive_failures() == 0
    assert store.last_equity() is None


def test_reopen_keeps_state(tmp_path):
    s = Store(tmp_path / "desk.db", start_cash=100.0)
    s.adjust_cash(-30.0)
    s.close()
    s2 = Store(tmp_path / "desk.db", start_cash=999.0)
    assert s2.cash() == pytest.approx(70.0)
    s2.close()


def test_peak_only_moves_up_until_reset(store):
    store.record_equity(NOW, 120.0, 120.0, 0.0)
    store.record_equity(NOW, 90.0, 90.0, 0.0)
    assert store.peak() == 120.0 and store.last_equity() == 90.0
    store.reset_peak(90.0)
    assert store.peak() == 90.0


def test_flags_and_meta(store):
    store.set_halted(True)
    assert store.is_halted()
    store.set_consecutive_failures(2)
    assert store.consecutive_failures() == 2
    store.set_meta("last_monthly_report", "2026-10")
    assert store.get_meta("last_monthly_report") == "2026-10"


def test_positions_roundtrip_and_close(store):
    pid = store.add_position(make_position())
    [pos] = store.open_positions()
    assert pos.id == pid and pos.end_date == make_position().end_date and pos.category == "weather"
    store.close_position(pid, NOW, 1.0, 9.67, "settled")
    assert store.open_positions() == []
    [closed] = store.closed_positions()
    assert closed.status == "closed" and closed.proceeds == 9.67 and closed.close_reason == "settled"
    assert closed.closed_at == NOW


def test_trades_newest_first(store):
    for i in range(3):
        store.add_trade(TradeRecord(NOW + timedelta(minutes=i), 1, "1001", "BUY", 1.0, 0.5, 0.0, -0.5, f"t{i}"))
    assert [t.reason for t in store.trades()] == ["t2", "t1", "t0"]
    assert len(store.trades(limit=2)) == 2


def test_analysis_roundtrip_and_freshness(store):
    a = make_analysis(created_at=NOW - timedelta(hours=1))
    store.save_analysis(a)
    assert store.get_analysis("1001") == a
    assert store.get_analysis("nope") is None
    assert store.analyses_since(NOW - timedelta(hours=3)) == {"1001"}
    assert store.analyses_since(NOW - timedelta(minutes=30)) == set()


def test_failures_and_cooldown(store):
    store.record_analysis_failure("1001", NOW, "boom")
    assert store.failed_since(NOW - timedelta(minutes=30)) == {"1001"}
    assert store.failed_since(NOW + timedelta(minutes=1)) == set()
    store.save_analysis(make_analysis())
    assert store.failed_since(NOW - timedelta(minutes=30)) == set()


def test_position_keeps_event_id(store):
    store.add_position(make_position(event_id="606422"))
    assert store.open_positions()[0].event_id == "606422"


def test_old_database_gets_event_id_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = Store(path, start_cash=100.0)
    old.close()
    db = sqlite3.connect(path)
    db.execute("ALTER TABLE positions DROP COLUMN event_id")
    db.commit()
    db.close()
    reopened = Store(path, start_cash=100.0)
    try:
        reopened.add_position(make_position(event_id="e1"))
        assert reopened.open_positions()[0].event_id == "e1"
    finally:
        reopened.close()


def test_mode_is_pinned_on_first_use(store):
    store.ensure_mode("paper")
    store.ensure_mode("paper")
    with pytest.raises(ModeMismatch):
        store.ensure_mode("live")


def test_database_with_history_but_no_mode_counts_as_paper(store):
    store.record_equity(NOW, 100.0, 100.0, 0.0)  # written before modes were pinned: only paper existed
    with pytest.raises(ModeMismatch):
        store.ensure_mode("live")

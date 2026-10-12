from datetime import timedelta

import httpx
import pytest

from desk.analyst.base import AnalystError
from desk.config import Settings
from desk.cycle import Deps, run_cycle
from desk.executor.base import ExecutionUncertain
from desk.executor.paper import PaperExecutor
from desk.journal import Journal
from desk.store import Store
from tests.factories import NOW, CapturingNotifier, make_analysis, make_book, make_market, make_position


class FakeMarkets:
    def __init__(self, markets):
        self.markets = list(markets)
        self.by_id = {m.id: m for m in self.markets}

    def active_markets(self, limit_total):
        return self.markets[:limit_total]

    def market_by_id(self, market_id):
        return self.by_id.get(market_id)


class FailingScan(FakeMarkets):
    def active_markets(self, limit_total):
        raise httpx.ConnectError("down")


class FakeBooks:
    def __init__(self, books):
        self.books = books

    def get_book(self, token_id):
        return self.books[token_id]


class FailingBooks:
    def get_book(self, token_id):
        raise httpx.ConnectError("down")


class FakeAnalyst:
    name = "fake"

    def __init__(self, results=None, postmortem_text="pm"):
        self.results = results or {}
        self.calls = []
        self.postmortems = []
        self.postmortem_text = postmortem_text

    def estimate(self, market, now):
        self.calls.append(market.id)
        result = self.results.get(market.id)
        if isinstance(result, Exception):
            raise result
        if result is None:
            raise AnalystError("sem resultado falso")
        return make_analysis(market_id=market.id, fair_prob=result, created_at=now)

    def postmortem(self, summary):
        self.postmortems.append(summary)
        return self.postmortem_text


def books_for(market, yes_ask=0.62, yes_bid=0.61, depth=1000.0):
    yes, no = market.token_ids
    return {
        yes: make_book(yes, bids=((yes_bid, depth),), asks=((yes_ask, depth),)),
        no: make_book(no, bids=((round(1 - yes_ask, 2), depth),), asks=((round(1 - yes_bid, 2), depth),)),
    }


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "desk.db", start_cash=100.0)
    yield tmp_path, store
    store.close()


def make_deps(tmp_path, store, markets, books, analyst, settings=None):
    return Deps(
        settings=settings or Settings(),
        store=store,
        markets=FakeMarkets(markets),
        books=FakeBooks(books),
        analyst=analyst,
        executor=PaperExecutor(),
        journal=Journal(tmp_path / "trades.txt"),
        notifier=CapturingNotifier(),
        now=lambda: NOW,
    )


def open_position(store, **overrides):
    pos = make_position(**overrides)
    store.adjust_cash(-pos.cost)
    pos.id = store.add_position(pos)
    return pos


def test_enters_when_fair_value_beats_price_by_more_than_8pp(env):
    tmp_path, store = env
    m = make_market()
    report = run_cycle(make_deps(tmp_path, store, [m], books_for(m), FakeAnalyst({m.id: 0.74})))
    assert report.entries == 1
    [pos] = store.open_positions()
    assert pos.outcome_index == 0 and pos.shares == pytest.approx(9.67) and pos.avg_price == pytest.approx(0.62)
    assert store.cash() == pytest.approx(100 - 9.67 * 0.62)
    text = (tmp_path / "trades.txt").read_text(encoding="utf-8")
    assert "ENTRADA [PAPER]" in text and "Condição de saída" in text


def test_position_in_a_game_must_be_closed_before_kickoff(env):
    tmp_path, store = env
    kickoff = NOW + timedelta(hours=5)
    m = make_market(game_start=kickoff)
    run_cycle(make_deps(tmp_path, store, [m], books_for(m), FakeAnalyst({m.id: 0.74})))
    [pos] = store.open_positions()
    assert pos.end_date == kickoff


def test_small_gap_is_filtered_before_fetching_books(env):
    tmp_path, store = env
    m = make_market()
    report = run_cycle(make_deps(tmp_path, store, [m], {}, FakeAnalyst({m.id: 0.66})))
    assert report.entries == 0 and report.skips["prefilter_no_edge"] == 1


def test_gap_below_threshold_after_books_is_skipped(env):
    tmp_path, store = env
    m = make_market()
    report = run_cycle(make_deps(tmp_path, store, [m], books_for(m), FakeAnalyst({m.id: 0.69})))
    assert report.entries == 0 and report.skips["edge_below_min"] == 1


def test_exits_position_resolving_within_one_hour(env):
    tmp_path, store = env
    m = make_market(end_date=NOW + timedelta(minutes=30))
    pos = open_position(store, end_date=m.end_date)
    store.save_analysis(make_analysis(created_at=NOW))
    report = run_cycle(make_deps(tmp_path, store, [m], books_for(m), FakeAnalyst()))
    assert report.exits == 1 and store.open_positions() == []
    assert store.cash() == pytest.approx(100 - pos.cost + 9.67 * 0.61)
    assert store.closed_positions()[0].close_reason == "resolving_soon"


def test_drawdown_of_40pct_halts_and_blocks_entries(env):
    tmp_path, store = env
    store.adjust_cash(-45.0)
    m = make_market()
    analyst = FakeAnalyst({m.id: 0.74})
    deps = make_deps(tmp_path, store, [m], books_for(m), analyst)
    report = run_cycle(deps)
    assert report.halted and store.is_halted()
    assert report.entries == 0
    assert analyst.postmortems
    assert any("PARADO" in s for s in deps.notifier.sent)
    assert "POST-MORTEM" in (tmp_path / "trades.txt").read_text(encoding="utf-8")


def test_halted_desk_still_manages_exits(env):
    tmp_path, store = env
    store.set_halted(True)
    m = make_market(end_date=NOW + timedelta(minutes=30))
    open_position(store, end_date=m.end_date)
    store.save_analysis(make_analysis(created_at=NOW))
    report = run_cycle(make_deps(tmp_path, store, [m], books_for(m), FakeAnalyst()))
    assert report.exits == 1 and report.halted


def test_analysis_budget_limits_calls_per_cycle(env):
    tmp_path, store = env
    markets = [make_market(id=str(i), slug=f"m{i}", token_ids=(f"{i}a", f"{i}b"), volume_24h=float(i)) for i in range(10)]
    analyst = FakeAnalyst({m.id: 0.62 for m in markets})
    run_cycle(make_deps(tmp_path, store, markets, {}, analyst))
    assert len(analyst.calls) == 6
    assert analyst.calls[0] == "9"


def test_fresh_analysis_is_reused_next_cycle(env):
    tmp_path, store = env
    m = make_market()
    analyst = FakeAnalyst({m.id: 0.62})
    deps = make_deps(tmp_path, store, [m], {}, analyst)
    run_cycle(deps)
    run_cycle(deps)
    assert analyst.calls == [m.id]


def test_analyst_failures_stop_after_three_and_set_cooldown(env):
    tmp_path, store = env
    markets = [make_market(id=str(i), slug=f"m{i}", token_ids=(f"{i}a", f"{i}b")) for i in range(5)]
    analyst = FakeAnalyst({m.id: AnalystError("boom") for m in markets})
    report = run_cycle(make_deps(tmp_path, store, markets, {}, analyst))
    assert len(analyst.calls) == 3
    assert len(store.failed_since(NOW - timedelta(minutes=1))) == 3
    assert len(report.errors) == 3


def test_settles_resolved_market(env):
    tmp_path, store = env
    m = make_market(closed=True, outcome_prices=(1.0, 0.0))
    pos = open_position(store)
    report = run_cycle(make_deps(tmp_path, store, [m], {}, FakeAnalyst()))
    assert report.settled == 1
    assert store.cash() == pytest.approx(100 - pos.cost + 9.67)


def test_reanalysis_exits_when_fair_value_moves_more_than_5pp(env):
    tmp_path, store = env
    m = make_market()
    open_position(store)
    store.save_analysis(make_analysis(created_at=NOW - timedelta(hours=2)))
    analyst = FakeAnalyst({m.id: 0.60})
    report = run_cycle(make_deps(tmp_path, store, [m], books_for(m), analyst))
    assert analyst.calls[0] == m.id
    assert report.exits == 1
    assert store.closed_positions()[0].close_reason == "fair_value_changed"


def test_unsellable_position_past_end_date_is_kept_and_retried(env):
    tmp_path, store = env
    m = make_market(end_date=NOW - timedelta(hours=1))
    open_position(store, end_date=m.end_date)
    store.save_analysis(make_analysis(created_at=NOW))
    empty = {"111": make_book("111", bids=(), asks=()), "222": make_book("222", bids=(), asks=())}
    report = run_cycle(make_deps(tmp_path, store, [m], empty, FakeAnalyst()))
    assert report.exits == 0 and len(store.open_positions()) == 1
    assert any("liquidez insuficiente" in e for e in report.errors)


def test_book_outage_does_not_trigger_false_drawdown(env):
    tmp_path, store = env
    m = make_market()
    open_position(store, shares=50, avg_price=0.62)
    store.save_analysis(make_analysis(created_at=NOW))
    deps = make_deps(tmp_path, store, [m], {}, FakeAnalyst())
    deps.books = FailingBooks()
    report = run_cycle(deps)
    assert not report.halted
    assert report.equity == pytest.approx(100.0)
    assert any("livro indisponível" in e for e in report.errors)


def test_scan_outage_still_processes_exits(env):
    tmp_path, store = env
    m = make_market(end_date=NOW + timedelta(minutes=30))
    open_position(store, end_date=m.end_date)
    store.save_analysis(make_analysis(created_at=NOW))
    deps = make_deps(tmp_path, store, [m], books_for(m), FakeAnalyst())
    deps.markets = FailingScan([m])
    report = run_cycle(deps)
    assert report.exits == 1
    assert any("varredura falhou" in e for e in report.errors)


def test_max_open_positions_stops_scanning(env):
    tmp_path, store = env
    for i in range(5):
        open_position(store, market_id=f"held{i}", token_id=f"h{i}")
        store.save_analysis(make_analysis(market_id=f"held{i}", created_at=NOW))
    m = make_market()
    books = {f"h{i}": make_book(f"h{i}", bids=((0.61, 1000.0),), asks=((0.62, 1000.0),)) for i in range(5)}
    analyst = FakeAnalyst({m.id: 0.74})
    report = run_cycle(make_deps(tmp_path, store, [m], books, analyst))
    assert report.entries == 0 and analyst.calls == [] and report.scanned == 0


def test_exited_market_is_not_reentered_during_cooldown(env):
    tmp_path, store = env
    m = make_market()
    open_position(store)  # YES at 0.62, entered on fair 0.74
    store.save_analysis(make_analysis(created_at=NOW - timedelta(hours=2)))
    analyst = FakeAnalyst({m.id: 0.80})  # +6pp: exits, yet the edge (18pp) would buy straight back
    report = run_cycle(make_deps(tmp_path, store, [m], books_for(m), analyst))
    assert report.exits == 1 and report.entries == 0
    later = make_deps(tmp_path, store, [m], books_for(m), analyst)
    later.now = lambda: NOW + timedelta(hours=7)
    assert run_cycle(later).entries == 1


class UncertainBuys(PaperExecutor):
    mode = "live"

    def buy(self, *args, **kwargs):
        raise ExecutionUncertain("resultado incerto (TransportError); confira a carteira no Polymarket")


def test_uncertain_live_buy_alerts_and_blocks_the_market(env):
    tmp_path, store = env
    m = make_market()
    deps = make_deps(tmp_path, store, [m], books_for(m), FakeAnalyst({m.id: 0.74}))
    deps.executor = UncertainBuys()
    run_cycle(deps)
    assert store.open_positions() == []
    assert any("confira" in msg for msg in deps.notifier.sent)
    deps.executor = PaperExecutor()  # would buy if the market were not blocked
    deps.now = lambda: NOW + timedelta(hours=1)
    assert run_cycle(deps).entries == 0

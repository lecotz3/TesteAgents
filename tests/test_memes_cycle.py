from datetime import timedelta

import httpx
import pytest

from desk.analyst.base import AnalystError
from desk.config import MemesSettings
from desk.executor.base import ExecutionError, ExecutionUncertain
from desk.journal import Journal
from desk.memes.cycle import MemeDeps, run_meme_cycle
from desk.memes.executor import PaperMemeExecutor
from desk.memes.models import MemeFill
from desk.memes.store import MemeStore
from desk.memes.vetter import Vet
from tests.factories import NOW, CapturingNotifier
from tests.meme_factories import MINT, FakeDex, FakeJupiter, FakeRug, make_meme_position, make_pair, make_safety

GOOD_VET = Vet("buy", "medium", (), "seal rescue meme", "organic community")


class FakeVetter:
    def __init__(self, vet=GOOD_VET, raises=None):
        self.result, self.raises, self.calls = vet, raises, []

    def vet(self, pair, safety, now):
        self.calls.append(pair.mint)
        if self.raises:
            raise self.raises
        return self.result


@pytest.fixture
def store(tmp_path):
    s = MemeStore(tmp_path / "memes.db", start_cash=100.0)
    yield s
    s.close()


def deps(tmp_path, store, *, dex=None, rug=None, jup=None, vetter=None, executor=None, notifier=None, now=NOW,
         **settings):
    jup = jup or FakeJupiter()
    return MemeDeps(
        settings=MemesSettings(**settings), store=store, dex=dex or FakeDex({MINT: make_pair()}),
        rugcheck=rug or FakeRug(), jupiter=jup, vetter=FakeVetter() if vetter is None else vetter,
        executor=executor or PaperMemeExecutor(jup, network_fee_usd=0.002), journal=Journal(tmp_path / "memes.txt"),
        notifier=notifier or CapturingNotifier(), now=lambda: now,
    )


def test_buys_a_token_that_passes_every_check(tmp_path, store):
    notifier = CapturingNotifier()
    report = run_meme_cycle(deps(tmp_path, store, notifier=notifier))
    assert report.entries == 1 and report.errors == []
    [pos] = store.open_positions()
    assert pos.mint == MINT and pos.cost_usd == pytest.approx(3.002)
    assert pos.entry_price_usd == pytest.approx(0.0015 / 0.99, rel=1e-3)  # what was actually paid per token
    assert store.cash() == pytest.approx(100.0 - 3.002 - 0.01)  # the trade and the Claude check
    assert store.get_vet(MINT)["verdict"] == "buy"
    assert [d["decision"] for d in store.recent_decisions(5)] == ["bought"]
    assert "fomo.family/tokens/solana/" + MINT in notifier.sent[0] and "COMPROU" in notifier.sent[0]
    assert "COMPRA MEMECOIN [PAPER]" in (tmp_path / "memes.txt").read_text(encoding="utf-8")


def test_vet_costs_money(tmp_path, store):
    run_meme_cycle(deps(tmp_path, store, cost_per_vet_usd=0.05))
    assert store.total_costs() == pytest.approx(0.05)
    assert store.analyst_calls_since(NOW - timedelta(hours=1)) == 1


def test_market_filter_rejects_without_calling_rugcheck(tmp_path, store):
    rug = FakeRug()
    report = run_meme_cycle(deps(tmp_path, store, dex=FakeDex({MINT: make_pair(liquidity_usd=1000.0)}), rug=rug))
    assert report.entries == 0 and report.skips["low_liquidity"] == 1 and rug.calls == []


def test_unsafe_token_is_rejected_and_not_rechecked_for_hours(tmp_path, store):
    rug = FakeRug(default=make_safety(freeze_authority="X"))
    run_meme_cycle(deps(tmp_path, store, rug=rug))
    run_meme_cycle(deps(tmp_path, store, rug=rug, now=NOW + timedelta(hours=1)))
    assert store.open_positions() == [] and rug.calls == [MINT]
    assert store.recent_decisions(5)[0]["decision"] == "freeze_authority"


def test_expensive_round_trip_is_rejected(tmp_path, store):
    vetter = FakeVetter()
    report = run_meme_cycle(deps(tmp_path, store, jup=FakeJupiter(leg_loss=0.04), vetter=vetter))
    assert report.entries == 0 and report.skips["round_trip_cost"] == 1 and vetter.calls == []


def test_token_without_a_route_back_is_rejected(tmp_path, store):
    report = run_meme_cycle(deps(tmp_path, store, jup=FakeJupiter(no_route={MINT})))
    assert report.entries == 0 and report.skips["cannot_sell"] == 1


def test_price_far_from_dexscreener_is_rejected(tmp_path, store):
    report = run_meme_cycle(deps(tmp_path, store, jup=FakeJupiter(prices={MINT: 0.003})))
    assert report.entries == 0 and report.skips["price_mismatch"] == 1


def test_vetter_veto_blocks_the_token(tmp_path, store):
    vetter = FakeVetter(Vet("avoid", "high", ("copycat",), "fake", "copy of an older token"))
    report = run_meme_cycle(deps(tmp_path, store, vetter=vetter))
    run_meme_cycle(deps(tmp_path, store, vetter=vetter, now=NOW + timedelta(hours=2)))
    assert report.entries == 0 and vetter.calls == [MINT]
    assert store.recent_decisions(5)[0]["decision"] == "vetter_avoid"
    assert "copy of an older token" in store.recent_decisions(5)[0]["detail"]


def test_vetter_failure_skips_without_buying(tmp_path, store):
    report = run_meme_cycle(deps(tmp_path, store, vetter=FakeVetter(raises=AnalystError("timeout"))))
    assert report.entries == 0 and report.skips["vet_error"] == 1 and report.errors


def test_daily_vet_cap_means_no_unvetted_buys(tmp_path, store):
    vetter = FakeVetter()
    report = run_meme_cycle(deps(tmp_path, store, vetter=vetter, max_vets_per_day=0))
    assert report.entries == 0 and report.skips["vet_cap"] == 1 and vetter.calls == []


def test_without_analyst_it_trades_on_the_code_checks_alone(tmp_path, store):
    vetter = FakeVetter()
    report = run_meme_cycle(deps(tmp_path, store, vetter=vetter, analyst="none"))
    assert report.entries == 1 and vetter.calls == []


def test_alerts_only_mode_never_buys(tmp_path, store):
    notifier = CapturingNotifier()
    report = run_meme_cycle(deps(tmp_path, store, notifier=notifier, auto_trade=False))
    run_meme_cycle(deps(tmp_path, store, notifier=notifier, auto_trade=False, now=NOW + timedelta(minutes=5)))
    assert report.entries == 0 and store.open_positions() == [] and store.cash() == pytest.approx(100.0 - 0.01)
    assert len(notifier.sent) == 1 and "aprovada" in notifier.sent[0].lower()  # alerted once, not every cycle


def test_respects_max_positions_and_never_rebuys_a_held_token(tmp_path, store):
    other = "B" * 44
    dex = FakeDex({MINT: make_pair(), other: make_pair(mint=other, symbol="OTHER")})
    run_meme_cycle(deps(tmp_path, store, dex=dex, max_positions=1))
    assert len(store.open_positions()) == 1
    report = run_meme_cycle(deps(tmp_path, store, dex=dex, max_positions=2, now=NOW + timedelta(minutes=3)))
    assert {p.mint for p in store.open_positions()} == {MINT, other} and report.entries == 1


def open_held(store, **overrides):
    pos = make_meme_position(**overrides)
    store.open_position(pos, MemeFill("BUY", pos.amount_raw, pos.cost_usd, 0.002), NOW - timedelta(hours=1), "x")


def test_stop_loss_sells_everything_and_blocks_reentry(tmp_path, store):
    open_held(store)
    notifier = CapturingNotifier()
    dex = FakeDex({MINT: make_pair(price_usd=0.0010, liquidity_usd=100_000.0)}, discovered=[MINT])
    report = run_meme_cycle(deps(tmp_path, store, dex=dex, jup=FakeJupiter(prices={MINT: 0.0010}), notifier=notifier))
    assert report.exits == 1 and report.entries == 0
    [closed] = store.closed_positions()
    assert closed.close_reason == "stop_loss" and closed.proceeds_usd == pytest.approx(2.0 * 0.99 - 0.002, rel=1e-3)
    assert store.cash() == pytest.approx(100.0 - 3.002 + closed.proceeds_usd)
    assert MINT in store.blocked_ids(NOW + timedelta(hours=12))
    assert "VENDEU" in notifier.sent[0] and "stop" in notifier.sent[0].lower()


def test_take_profit_sells_half_and_keeps_riding(tmp_path, store):
    open_held(store)
    dex = FakeDex({MINT: make_pair(price_usd=0.0032, liquidity_usd=100_000.0)}, discovered=[])
    run_meme_cycle(deps(tmp_path, store, dex=dex, jup=FakeJupiter(prices={MINT: 0.0032})))
    [pos] = store.open_positions()
    assert pos.take_profit_done and pos.amount_raw == 1_000_000_000 and pos.peak_price_usd == 0.0032


def test_missing_price_holds_and_values_at_last_known_price(tmp_path, store):
    open_held(store)
    store.mark_price(store.open_positions()[0].id, 0.0012)
    report = run_meme_cycle(deps(tmp_path, store, dex=FakeDex({}, discovered=[])))
    assert report.exits == 0 and len(store.open_positions()) == 1
    assert report.equity == pytest.approx(100.0 - 3.002 + 2000 * 0.0012)


def test_failed_sell_keeps_the_position_and_alerts_once(tmp_path, store):
    open_held(store)
    notifier = CapturingNotifier()
    dex = FakeDex({MINT: make_pair(price_usd=0.0010, liquidity_usd=100_000.0)}, discovered=[])
    for minutes in (0, 3):
        run_meme_cycle(deps(tmp_path, store, dex=dex, jup=FakeJupiter(no_route={MINT}), notifier=notifier,
                            now=NOW + timedelta(minutes=minutes)))
    assert len(store.open_positions()) == 1
    assert len(notifier.sent) == 1 and "não conseguiu vender" in notifier.sent[0]


def test_drawdown_switches_it_off_but_exits_keep_running(tmp_path, store):
    open_held(store, amount_raw=60_000_000_000, cost_usd=90.0)  # 60k tokens bought at 0.0015
    store.record_equity(NOW - timedelta(hours=2), 100.0, 100.0, 0.0)
    notifier = CapturingNotifier()
    vetter = FakeVetter()
    dex = FakeDex({MINT: make_pair(price_usd=0.0005, liquidity_usd=100_000.0)}, discovered=[MINT])
    report = run_meme_cycle(deps(tmp_path, store, dex=dex, jup=FakeJupiter(prices={MINT: 0.0005}), vetter=vetter,
                                 notifier=notifier))
    assert report.halted and store.is_halted() and report.exits == 1 and vetter.calls == []
    assert any("DESLIGADO" in m for m in notifier.sent)
    assert "POST-MORTEM" in (tmp_path / "memes.txt").read_text(encoding="utf-8")


def test_discovery_outage_is_reported_not_fatal(tmp_path, store):
    report = run_meme_cycle(deps(tmp_path, store, dex=FakeDex(fail=httpx.ConnectError("down"))))
    assert report.entries == 0 and report.errors and store.recent_cycles(1)


def test_cycle_is_recorded_with_equity(tmp_path, store):
    run_meme_cycle(deps(tmp_path, store))
    [cycle] = store.recent_cycles(1)
    assert cycle["entries"] == 1 and store.last_equity() is not None


class LiveLikeExecutor(PaperMemeExecutor):
    mode = "live"

    def __init__(self, jup, *, funds=None, wallet_amount=None, buy_raises=None):
        super().__init__(jup, network_fee_usd=0.002)
        self.funds, self.wallet_amount, self.buy_raises = funds, wallet_amount, buy_raises

    def funds_problem(self, usd):
        return self.funds

    def sellable(self, mint, amount_raw):
        return amount_raw if self.wallet_amount is None else min(amount_raw, self.wallet_amount)

    def buy(self, mint, usd):
        if self.buy_raises:
            raise self.buy_raises
        return super().buy(mint, usd)


def test_live_wallet_short_skips_the_buy(tmp_path, store):
    jup = FakeJupiter()
    report = run_meme_cycle(deps(tmp_path, store, jup=jup, executor=LiveLikeExecutor(jup, funds="wallet_usdc_short")))
    assert report.entries == 0 and report.skips["wallet_usdc_short"] == 1


def test_uncertain_buy_blocks_the_token_and_alerts(tmp_path, store):
    jup, notifier = FakeJupiter(), CapturingNotifier()
    ex = LiveLikeExecutor(jup, buy_raises=ExecutionUncertain("lost response"))
    run_meme_cycle(deps(tmp_path, store, jup=jup, executor=ex, notifier=notifier))
    assert store.open_positions() == [] and MINT in store.blocked_ids(NOW + timedelta(hours=20))
    assert any("carteira" in m for m in notifier.sent)


def test_failed_buy_is_recorded(tmp_path, store):
    jup = FakeJupiter()
    ex = LiveLikeExecutor(jup, buy_raises=ExecutionError("slippage"))
    report = run_meme_cycle(deps(tmp_path, store, jup=jup, executor=ex))
    assert report.entries == 0 and store.recent_decisions(1)[0]["decision"] == "buy_failed"


def test_tokens_gone_from_the_wallet_are_written_off(tmp_path, store):
    open_held(store)
    jup, notifier = FakeJupiter(prices={MINT: 0.0010}), CapturingNotifier()
    dex = FakeDex({MINT: make_pair(price_usd=0.0010, liquidity_usd=100_000.0)}, discovered=[])
    for minutes in (0, 3):  # one zero reading can be a lagging RPC: it must repeat before the write-off
        run_meme_cycle(deps(tmp_path, store, dex=dex, jup=jup, executor=LiveLikeExecutor(jup, wallet_amount=0),
                            notifier=notifier, now=NOW + timedelta(minutes=minutes)))
        if minutes == 0:
            assert len(store.open_positions()) == 1 and notifier.sent == []
    assert store.closed_positions()[0].close_reason == "missing_in_wallet"
    assert any("carteira" in m for m in notifier.sent)


def test_a_single_zero_balance_reading_is_forgotten_when_the_tokens_show_up(tmp_path, store):
    open_held(store)
    jup = FakeJupiter(prices={MINT: 0.0010})
    dex = FakeDex({MINT: make_pair(price_usd=0.0010, liquidity_usd=100_000.0)}, discovered=[])
    run_meme_cycle(deps(tmp_path, store, dex=dex, jup=jup, executor=LiveLikeExecutor(jup, wallet_amount=0)))
    run_meme_cycle(deps(tmp_path, store, dex=dex, jup=jup, executor=LiveLikeExecutor(jup, buy_raises=None),
                        now=NOW + timedelta(minutes=3)))
    assert store.closed_positions()[0].close_reason == "stop_loss"


def test_claude_checks_per_cycle_are_capped_so_exits_are_never_starved(tmp_path, store):
    other = "B" * 44
    dex = FakeDex({MINT: make_pair(), other: make_pair(mint=other, symbol="OTHER", volume_h1_usd=100_000.0)})
    vetter = FakeVetter()
    report = run_meme_cycle(deps(tmp_path, store, dex=dex, vetter=vetter, max_vets_per_cycle=1))
    assert vetter.calls == [MINT] and report.entries == 1 and report.skips["vet_cycle_cap"] == 1

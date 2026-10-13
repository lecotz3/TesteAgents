from datetime import timedelta

import httpx
import pytest

from desk.analyst.base import AnalystError
from desk.config import MemesSettings
from desk.executor.base import ExecutionError, ExecutionUncertain
from desk.journal import Journal
from desk.memes.cycle import MemeDeps, run_meme_cycle, run_meme_exits
from desk.memes.executor import PaperMemeExecutor, PriceMoved
from desk.memes.models import MemeFill
from desk.memes.store import MemeStore
from desk.memes.vetter import Vet
from tests.factories import NOW, CapturingNotifier
from desk.memes.x import XUnavailable
from tests.meme_factories import (
    MINT, FakeDex, FakeJupiter, FakeRug, FakeTokens, FakeX, make_meme_position, make_pair, make_post, make_profile,
    make_safety,
)

GOOD_VET = Vet("buy", "medium", (), "seal rescue meme", "organic community")


class FakeVetter:
    def __init__(self, vet=GOOD_VET, raises=None):
        self.result, self.raises, self.calls = vet, raises, []

    def vet(self, pair, safety, now, x=None):
        self.calls.append(pair.mint)
        self.x_seen = x
        if self.raises:
            raise self.raises
        return self.result


@pytest.fixture
def store(tmp_path):
    s = MemeStore(tmp_path / "memes.db", start_cash=100.0)
    yield s
    s.close()


def deps(tmp_path, store, *, dex=None, rug=None, jup=None, vetter=None, executor=None, notifier=None, now=NOW,
         x=None, tokens=None, **settings):
    jup = jup or FakeJupiter()
    return MemeDeps(
        settings=MemesSettings(**settings), store=store, dex=dex or FakeDex({MINT: make_pair()}),
        rugcheck=rug or FakeRug(), jupiter=jup, vetter=FakeVetter() if vetter is None else vetter,
        executor=executor or PaperMemeExecutor(jup, network_fee_usd=0.002), journal=Journal(tmp_path / "memes.txt"),
        notifier=notifier or CapturingNotifier(), now=lambda: now, x=x, tokens=tokens,
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

    def __init__(self, jup, *, funds=None, wallet_amount=None, buy_raises=None, balance=0, sell_raises=None):
        super().__init__(jup, network_fee_usd=0.002)
        self.funds, self.wallet_amount, self.buy_raises, self.balance = funds, wallet_amount, buy_raises, balance
        self.sell_raises, self.sells, self.min_outs = sell_raises, 0, []

    def sell(self, mint, amount_raw):
        self.sells += 1
        if self.sell_raises:
            raise self.sell_raises
        return super().sell(mint, amount_raw)

    def wallet_balance(self, mint):
        if isinstance(self.balance, Exception):
            raise self.balance
        return self.balance

    def funds_problem(self, usd):
        return self.funds

    def sellable(self, mint, amount_raw):
        return amount_raw if self.wallet_amount is None else min(amount_raw, self.wallet_amount)

    def buy(self, mint, usd, min_out=0):
        self.min_outs.append(min_out)
        if self.buy_raises:
            raise self.buy_raises
        return super().buy(mint, usd, min_out)


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


def uncertain_buy(tmp_path, store, notifier):
    jup = FakeJupiter()
    run_meme_cycle(deps(tmp_path, store, jup=jup, notifier=notifier,
                        executor=LiveLikeExecutor(jup, buy_raises=ExecutionUncertain("lost response"))))
    assert store.open_positions() == []
    return jup


def test_uncertain_buy_that_landed_is_adopted_with_exit_rules(tmp_path, store):
    notifier = CapturingNotifier()
    jup = uncertain_buy(tmp_path, store, notifier)
    cash = store.cash()
    dex = FakeDex({MINT: make_pair()}, discovered=[])
    held = 1_980_000_000
    for minutes in (1, 2):  # the fast exits loop finds the tokens; the next run must not adopt them twice
        run_meme_exits(deps(tmp_path, store, dex=dex, jup=jup, notifier=notifier, now=NOW + timedelta(minutes=minutes),
                            executor=LiveLikeExecutor(jup, balance=held)))
    [pos] = store.open_positions()
    assert pos.amount_raw == held and pos.opened_at == NOW  # max hold counts from the real buy
    assert pos.cost_usd == pytest.approx(3.0, abs=0.05) and store.cash() == pytest.approx(cash - pos.cost_usd)
    assert pos.entry_price_usd == pytest.approx((pos.cost_usd - 0.002) / (held / 10 ** pos.decimals))
    assert any("assumiu" in m for m in notifier.sent)
    assert "compra incerta confirmada" in (tmp_path / "memes.txt").read_text(encoding="utf-8")


def test_uncertain_buy_that_never_landed_is_dropped_after_ten_minutes(tmp_path, store):
    notifier = CapturingNotifier()
    jup = uncertain_buy(tmp_path, store, notifier)
    cash, sent = store.cash(), len(notifier.sent)
    dex = FakeDex({MINT: make_pair()}, discovered=[])
    run_meme_exits(deps(tmp_path, store, dex=dex, jup=jup, notifier=notifier, now=NOW + timedelta(minutes=3),
                        executor=LiveLikeExecutor(jup, balance=0)))
    assert len(notifier.sent) == sent  # a lagging RPC gets a few minutes
    for minutes in (11, 12):
        run_meme_exits(deps(tmp_path, store, dex=dex, jup=jup, notifier=notifier, now=NOW + timedelta(minutes=minutes),
                            executor=LiveLikeExecutor(jup, balance=0)))
    assert store.open_positions() == [] and store.cash() == cash
    assert len(notifier.sent) == sent + 1 and "não entrou" in notifier.sent[-1]


def test_uncertain_buy_waits_when_the_wallet_cannot_be_read(tmp_path, store):
    notifier = CapturingNotifier()
    jup = uncertain_buy(tmp_path, store, notifier)
    dex = FakeDex({MINT: make_pair()}, discovered=[])
    report = run_meme_exits(deps(tmp_path, store, dex=dex, jup=jup, notifier=notifier, now=NOW + timedelta(minutes=30),
                                 executor=LiveLikeExecutor(jup, balance=httpx.ConnectError("rpc down"))))
    assert report.errors and store.open_positions() == []
    run_meme_exits(deps(tmp_path, store, dex=dex, jup=jup, notifier=notifier, now=NOW + timedelta(minutes=31),
                        executor=LiveLikeExecutor(jup, balance=5)))
    assert len(store.open_positions()) == 1  # never dropped just because the RPC was down


def uncertain_stop_loss(tmp_path, store, notifier):
    open_held(store)
    jup = FakeJupiter(prices={MINT: 0.0010})
    dex = FakeDex({MINT: make_pair(price_usd=0.0010, liquidity_usd=100_000.0)}, discovered=[])
    run_meme_cycle(deps(tmp_path, store, dex=dex, jup=jup, notifier=notifier,
                        executor=LiveLikeExecutor(jup, sell_raises=ExecutionUncertain("lost response"))))
    assert len(store.open_positions()) == 1  # nothing is booked while the answer is unknown
    return jup, dex


def test_uncertain_sell_whose_tokens_left_the_wallet_is_credited_at_the_last_price(tmp_path, store):
    notifier = CapturingNotifier()
    jup, dex = uncertain_stop_loss(tmp_path, store, notifier)
    cash = store.cash()
    ex = LiveLikeExecutor(jup, wallet_amount=0)
    run_meme_exits(deps(tmp_path, store, dex=dex, jup=jup, notifier=notifier, executor=ex,
                        now=NOW + timedelta(minutes=1)))
    [closed] = store.closed_positions()
    assert closed.close_reason == "stop_loss" and ex.sells == 0
    assert closed.proceeds_usd == pytest.approx(2000 * 0.0010 - 0.002)  # 2000 tokens at the price of the attempt
    assert store.cash() == pytest.approx(cash + closed.proceeds_usd)
    assert any("provavelmente" in m for m in notifier.sent)


def test_uncertain_sell_that_did_not_land_waits_then_sells_again(tmp_path, store):
    notifier = CapturingNotifier()
    jup, dex = uncertain_stop_loss(tmp_path, store, notifier)
    ex = LiveLikeExecutor(jup)  # the tokens are all still in the wallet
    run_meme_exits(deps(tmp_path, store, dex=dex, jup=jup, notifier=notifier, executor=ex,
                        now=NOW + timedelta(minutes=3)))
    assert ex.sells == 0 and len(store.open_positions()) == 1  # no second order while the first may still land
    run_meme_exits(deps(tmp_path, store, dex=dex, jup=jup, notifier=notifier, executor=ex,
                        now=NOW + timedelta(minutes=11)))
    assert ex.sells == 1 and store.closed_positions()[0].close_reason == "stop_loss"


def test_uncertain_partial_sell_credits_only_what_left_the_wallet(tmp_path, store):
    open_held(store)
    notifier = CapturingNotifier()
    jup = FakeJupiter(prices={MINT: 0.0032})
    dex = FakeDex({MINT: make_pair(price_usd=0.0032, liquidity_usd=100_000.0)}, discovered=[])
    run_meme_cycle(deps(tmp_path, store, dex=dex, jup=jup, notifier=notifier,
                        executor=LiveLikeExecutor(jup, sell_raises=ExecutionUncertain("lost"))))  # take profit: half
    run_meme_exits(deps(tmp_path, store, dex=dex, jup=jup, notifier=notifier, now=NOW + timedelta(minutes=1),
                        executor=LiveLikeExecutor(jup, wallet_amount=1_000_000_000)))
    [pos] = store.open_positions()
    assert pos.amount_raw == 1_000_000_000 and pos.take_profit_done
    assert pos.proceeds_usd == pytest.approx(1000 * 0.0032 - 0.002)


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


STATUS = "https://x.com/elonmusk/status/1"
OTHER = "B" * 44


def test_x_narrative_is_checked_and_reaches_claude_the_journal_and_the_alert(tmp_path, store):
    vetter, notifier = FakeVetter(), CapturingNotifier()
    report = run_meme_cycle(deps(tmp_path, store, dex=FakeDex({MINT: make_pair(x_url=STATUS)}), x=FakeX(post=make_post()),
                                 vetter=vetter, notifier=notifier))
    assert report.entries == 1 and report.x_checked == 1
    assert vetter.x_seen.post.views == 36693
    assert "@elonmusk" in notifier.sent[0] and "36.7k views" in notifier.sent[0]
    assert "X: post de @elonmusk" in (tmp_path / "memes.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("x,url,reason", [
    (FakeX(post=None), STATUS, "x_link_dead"),
    (FakeX(post=make_post(views=300, likes=4)), STATUS, "x_weak_narrative"),
    (FakeX(profile=make_profile(followers=3)), "https://x.com/CealOnSol", "x_no_community"),
    (FakeX(), None, "no_x_link"),
])
def test_x_rules_reject_before_rugcheck(tmp_path, store, x, url, reason):
    rug = FakeRug()
    report = run_meme_cycle(deps(tmp_path, store, dex=FakeDex({MINT: make_pair(x_url=url)}), x=x, rug=rug))
    assert report.entries == 0 and report.skips[reason] == 1 and rug.calls == []
    [d] = store.recent_decisions(1)
    assert d["decision"] == reason and MINT in store.blocked_ids(NOW + timedelta(hours=1))


def test_stronger_narrative_is_checked_first(tmp_path, store):
    dex = FakeDex({
        MINT: make_pair(x_url="https://x.com/someone/status/1", volume_h1_usd=900_000.0),
        OTHER: make_pair(mint=OTHER, symbol="OTHER", x_url="https://x.com/elonmusk/status/2", volume_h1_usd=60_000.0),
    })

    class TwoPosts(FakeX):
        def post(self, handle, post_id):
            self.calls.append((handle, post_id))
            return make_post(views=8_000) if handle == "someone" else make_post(views=5_000_000)

    rug = FakeRug()
    run_meme_cycle(deps(tmp_path, store, dex=dex, x=TwoPosts(), rug=rug, max_candidates_checked=1))
    assert rug.calls == [OTHER]  # less volume, far bigger narrative


def test_x_outage_skips_without_blocking(tmp_path, store):
    report = run_meme_cycle(deps(tmp_path, store, dex=FakeDex({MINT: make_pair(x_url=STATUS)}),
                                 x=FakeX(raises=XUnavailable("HTTP 503"))))
    assert report.entries == 0 and report.skips["x_unavailable"] == 1 and report.errors
    assert MINT not in store.blocked_ids(NOW)


def test_x_can_be_switched_off(tmp_path, store):
    x = FakeX()
    report = run_meme_cycle(deps(tmp_path, store, x=x, x_enabled=False))
    assert report.entries == 1 and x.calls == []


def test_jupiter_lists_add_candidates_and_enrich_them(tmp_path, store):
    from desk.memes.sources import TokenInfo

    dex = FakeDex({MINT: make_pair(x_url=None)}, discovered=[])  # DexScreener feeds found nothing this time
    tokens = FakeTokens(discovered=[MINT], info={MINT: TokenInfo(organic_score=80.0, twitter=STATUS)})
    vetter = FakeVetter()
    report = run_meme_cycle(deps(tmp_path, store, dex=dex, tokens=tokens, x=FakeX(post=make_post()), vetter=vetter))
    assert report.discovered == 1 and report.entries == 1
    assert vetter.x_seen.post.author == "elonmusk"  # the X link came from Jupiter


def test_low_organic_score_from_jupiter_is_rejected(tmp_path, store):
    from desk.memes.sources import TokenInfo

    tokens = FakeTokens(info={MINT: TokenInfo(organic_score=20.0, twitter=None)})
    rug = FakeRug()
    report = run_meme_cycle(deps(tmp_path, store, tokens=tokens, rug=rug))
    assert report.entries == 0 and report.skips["low_organic_score"] == 1 and rug.calls == []


def test_jupiter_outage_does_not_stop_the_cycle(tmp_path, store):
    report = run_meme_cycle(deps(tmp_path, store, tokens=FakeTokens(fail=httpx.ConnectError("down"))))
    assert report.entries == 1 and report.errors  # DexScreener alone still works


def test_safety_rejections_show_the_numbers_behind_them(tmp_path, store):
    run_meme_cycle(deps(tmp_path, store, rug=FakeRug(default=make_safety(lp_locked_pct=57.0))))
    [d] = store.recent_decisions(1)
    assert d["decision"] == "lp_unlocked" and "LP travada 57%" in d["detail"]


def test_bought_row_says_what_x_showed(tmp_path, store):
    run_meme_cycle(deps(tmp_path, store, dex=FakeDex({MINT: make_pair(x_url=STATUS)}), x=FakeX(post=make_post())))
    [d] = store.recent_decisions(1)
    assert d["decision"] == "bought" and "@elonmusk" in d["detail"]


def held_with_x(store, url=STATUS):
    pos = make_meme_position(x_url=url)
    store.open_position(pos, MemeFill("BUY", pos.amount_raw, pos.cost_usd, 0.002), NOW - timedelta(hours=1), "x")


def test_buy_remembers_the_x_link(tmp_path, store):
    run_meme_cycle(deps(tmp_path, store, dex=FakeDex({MINT: make_pair(x_url=STATUS)}), x=FakeX(post=make_post())))
    assert store.open_positions()[0].x_url == STATUS


def test_deleted_x_post_after_buying_sells_everything(tmp_path, store):
    held_with_x(store)
    notifier = CapturingNotifier()
    dex = FakeDex({MINT: make_pair(price_usd=0.0016, liquidity_usd=100_000.0)}, discovered=[])
    report = run_meme_cycle(deps(tmp_path, store, dex=dex, x=FakeX(post=None), notifier=notifier,
                                 jup=FakeJupiter(prices={MINT: 0.0016})))
    assert report.exits == 1 and store.closed_positions()[0].close_reason == "x_link_dead"
    assert any("apagado" in m for m in notifier.sent)


def test_x_outage_never_sells_a_held_token(tmp_path, store):
    held_with_x(store)
    dex = FakeDex({MINT: make_pair(price_usd=0.0016, liquidity_usd=100_000.0)}, discovered=[])
    report = run_meme_cycle(deps(tmp_path, store, dex=dex, x=FakeX(raises=XUnavailable("HTTP 503"))))
    assert report.exits == 0 and len(store.open_positions()) == 1


def test_live_x_post_keeps_the_position(tmp_path, store):
    held_with_x(store)
    dex = FakeDex({MINT: make_pair(price_usd=0.0016, liquidity_usd=100_000.0)}, discovered=[])
    report = run_meme_cycle(deps(tmp_path, store, dex=dex, x=FakeX(post=make_post())))
    assert report.exits == 0 and len(store.open_positions()) == 1


def test_a_bad_day_pauses_new_buys_but_not_sells(tmp_path, store):
    store.record_equity(NOW - timedelta(hours=30), 100.0, 100.0, 0.0)
    store.record_equity(NOW - timedelta(hours=20), 100.0, 100.0, 0.0)
    store.adjust_cash(-20.0)  # lost $20 since then
    notifier = CapturingNotifier()
    report = run_meme_cycle(deps(tmp_path, store, notifier=notifier))
    run_meme_cycle(deps(tmp_path, store, notifier=notifier, now=NOW + timedelta(minutes=3)))
    assert report.entries == 0 and report.skips["daily_loss_pause"] == 1
    assert sum("pausou" in m for m in notifier.sent) == 1  # alerted once a day, not every cycle


def test_a_small_dip_keeps_buying(tmp_path, store):
    store.record_equity(NOW - timedelta(hours=25), 100.0, 100.0, 0.0)
    store.adjust_cash(-10.0)
    assert run_meme_cycle(deps(tmp_path, store)).entries == 1


def test_daily_pause_can_be_switched_off(tmp_path, store):
    store.record_equity(NOW - timedelta(hours=25), 100.0, 100.0, 0.0)
    store.adjust_cash(-20.0)
    assert run_meme_cycle(deps(tmp_path, store, daily_loss_pause_pct=0)).entries == 1


def test_exits_only_run_sells_without_discovering_or_buying(tmp_path, store):
    from desk.memes.cycle import run_meme_exits

    open_held(store)

    class NoDiscovery(FakeDex):
        def discover(self):
            raise AssertionError("an exits-only run never discovers")

    dex = NoDiscovery({MINT: make_pair(price_usd=0.0010, liquidity_usd=100_000.0)})
    report = run_meme_exits(deps(tmp_path, store, dex=dex, jup=FakeJupiter(prices={MINT: 0.0010})))
    assert report.exits == 1 and store.closed_positions()[0].close_reason == "stop_loss"
    assert store.last_equity() is not None and store.recent_cycles(1) == []  # equity yes, "last cycle" untouched


def test_exits_wait_when_another_run_is_selling(tmp_path, store):
    from contextlib import contextmanager

    from desk.memes.cycle import run_meme_exits

    @contextmanager
    def busy():
        yield False

    open_held(store)
    dex = FakeDex({MINT: make_pair(price_usd=0.0010, liquidity_usd=100_000.0)}, discovered=[])
    d = deps(tmp_path, store, dex=dex, jup=FakeJupiter(prices={MINT: 0.0010}))
    d.exit_guard = busy
    assert run_meme_exits(d).exits == 0 and run_meme_cycle(d).exits == 0
    assert len(store.open_positions()) == 1


def test_a_long_gap_with_open_positions_is_reported_once_when_the_agent_is_back(tmp_path, store):
    open_held(store)
    store.record_equity(NOW - timedelta(minutes=47), 100.0, 97.0, 3.0)
    notifier = CapturingNotifier()
    dex = FakeDex({MINT: make_pair(price_usd=0.0015, liquidity_usd=100_000.0)}, discovered=[])
    for minutes in (0, 1):
        run_meme_exits(deps(tmp_path, store, dex=dex, notifier=notifier, now=NOW + timedelta(minutes=minutes)))
    gaps = [m for m in notifier.sent if "sem rodar" in m]
    assert len(gaps) == 1 and "47 min" in gaps[0] and "SEALS" in gaps[0]


def test_a_gap_without_positions_is_not_worth_an_alert(tmp_path, store):
    store.record_equity(NOW - timedelta(hours=8), 100.0, 100.0, 0.0)
    notifier = CapturingNotifier()
    run_meme_exits(deps(tmp_path, store, dex=FakeDex({}, discovered=[]), notifier=notifier))
    assert notifier.sent == []


def test_the_buy_order_must_not_be_worse_than_the_approved_quote(tmp_path, store):
    jup = FakeJupiter()
    ex = LiveLikeExecutor(jup)
    run_meme_cycle(deps(tmp_path, store, jup=jup, executor=ex))
    approved = int(3.0 / 0.0015 * 1e6 * 0.99)  # what check_quotes saw for the $3 buy
    assert len(ex.min_outs) == 1 and ex.min_outs[0] == pytest.approx(approved * 0.97, abs=2)


def test_price_moved_while_vetting_skips_the_buy(tmp_path, store):
    jup = FakeJupiter()
    ex = LiveLikeExecutor(jup, buy_raises=PriceMoved("ordem 8% pior que a cotação aprovada"))
    report = run_meme_cycle(deps(tmp_path, store, jup=jup, executor=ex))
    assert report.entries == 0 and report.skips["price_moved"] == 1 and store.open_positions() == []
    assert store.recent_decisions(1)[0]["decision"] == "price_moved"


def test_low_sol_while_holding_positions_is_alerted_once_a_day(tmp_path, store):
    open_held(store)
    jup, notifier = FakeJupiter(), CapturingNotifier()
    dex = FakeDex({MINT: make_pair(price_usd=0.0015, liquidity_usd=100_000.0)}, discovered=[])
    for minutes in (0, 3):
        run_meme_cycle(deps(tmp_path, store, dex=dex, jup=jup, notifier=notifier, now=NOW + timedelta(minutes=minutes),
                            executor=LiveLikeExecutor(jup, funds="wallet_sol_short")))
    alerts = [m for m in notifier.sent if "SOL" in m]
    assert len(alerts) == 1 and "vendas" in alerts[0]


class RentExecutor(LiveLikeExecutor):
    def __init__(self, jup, raises=None):
        super().__init__(jup)
        self.reclaims, self.raises = 0, raises

    def reclaim_rent(self):
        self.reclaims += 1
        if self.raises:
            raise self.raises
        return 2


def test_live_cycle_reclaims_token_account_rent_at_most_hourly(tmp_path, store):
    jup = FakeJupiter()
    ex = RentExecutor(jup)
    dex = FakeDex({}, discovered=[])
    for minutes in (0, 3, 30, 61):
        run_meme_cycle(deps(tmp_path, store, dex=dex, jup=jup, executor=ex, now=NOW + timedelta(minutes=minutes)))
    assert ex.reclaims == 2
    run_meme_exits(deps(tmp_path, store, dex=dex, jup=jup, executor=ex, now=NOW + timedelta(hours=3)))
    assert ex.reclaims == 2  # the 1-minute loop stays lean


def test_a_failed_rent_sweep_is_only_an_error(tmp_path, store):
    jup = FakeJupiter()
    report = run_meme_cycle(deps(tmp_path, store, dex=FakeDex({}, discovered=[]), jup=jup,
                                 executor=RentExecutor(jup, raises=httpx.ConnectError("rpc down"))))
    assert any("aluguel" in e for e in report.errors)


def test_sell_all_sells_every_position_now_and_pauses_buying(tmp_path, store):
    from desk.memes.cycle import run_meme_sell_all

    open_held(store)
    open_held(store, mint=OTHER, symbol="OTHER")
    notifier = CapturingNotifier()
    dex = FakeDex({MINT: make_pair(price_usd=0.0016), OTHER: make_pair(mint=OTHER, symbol="OTHER", price_usd=0.0016)})
    report = run_meme_sell_all(deps(tmp_path, store, dex=dex, notifier=notifier))
    assert report.exits == 2 and store.open_positions() == [] and store.is_halted()
    assert {p.close_reason for p in store.closed_positions()} == {"manual"}
    assert any("Venda geral" in m for m in notifier.sent)
    assert run_meme_cycle(deps(tmp_path, store, dex=dex, now=NOW + timedelta(minutes=3))).entries == 0


def test_the_reason_for_stopping_purchases_is_recorded(tmp_path, store):
    from desk.memes.cycle import run_meme_sell_all

    run_meme_sell_all(deps(tmp_path, store, dex=FakeDex({}, discovered=[])))
    assert store.get_meta("halt_reason") == "manual"

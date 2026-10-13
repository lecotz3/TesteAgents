import httpx
import pytest
import respx

from desk import cli
from desk.config import ConfigError
from desk.memes import app as memes_app
from desk.memes.cycle import MemeCycleReport
from desk.memes.executor import LiveMemeExecutor, PaperMemeExecutor
from desk.memes.models import MemeFill
from tests.factories import NOW, CapturingNotifier
from tests.meme_factories import make_meme_position


def write_config(tmp_path, memes='mode = "paper"\n'):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'mode = "paper"\n\n[memes]\n{memes}', encoding="utf-8")
    return cfg


@pytest.fixture
def app(tmp_path):
    a = memes_app.build_memes_app(write_config(tmp_path), tmp_path / ".env")
    a.notifier = CapturingNotifier()
    yield a
    a.close()


def test_memes_have_their_own_database_and_journal(app, tmp_path):
    assert (tmp_path / "state" / "memes.db").exists()
    assert app.journal.path == tmp_path / "state" / "memes.txt"
    assert app.store.cash() == 100.0


def test_live_memes_refuse_the_paper_database(tmp_path):
    memes_app.build_memes_app(write_config(tmp_path), tmp_path / ".env").close()
    with pytest.raises(ConfigError, match="memes.db"):
        memes_app.build_memes_app(write_config(tmp_path, 'mode = "live"\n'), tmp_path / ".env")


def test_paper_executor_by_default(app):
    assert isinstance(memes_app.make_meme_executor(app), PaperMemeExecutor)


def live_app(tmp_path, env_text):
    (tmp_path / ".env").write_text(env_text, encoding="utf-8")
    a = memes_app.build_memes_app(write_config(tmp_path, 'mode = "live"\n'), tmp_path / ".env")
    a.notifier = CapturingNotifier()
    return a


@pytest.mark.parametrize("env_text,needs", [
    ("MEMES_PRIVATE_KEY=abc\n", "MEMES_LIVE"),
    ("MEMES_LIVE=I_UNDERSTAND\n", "MEMES_PRIVATE_KEY"),
    ("MEMES_LIVE=yes\nMEMES_PRIVATE_KEY=abc\n", "MEMES_LIVE"),
])
def test_live_needs_both_switches(tmp_path, env_text, needs):
    a = live_app(tmp_path, env_text)
    try:
        with pytest.raises(ConfigError, match=needs):
            memes_app.make_meme_executor(a)
    finally:
        a.close()


def generated_key():
    from solders.keypair import Keypair
    kp = Keypair()
    return str(kp), str(kp.pubkey())


def unsigned_swap_tx(owner: str) -> str:
    """A gasless-style transaction like Jupiter's: Jupiter pays the fee, the wallet signs its own slot."""
    import base64

    from solders.hash import Hash
    from solders.keypair import Keypair
    from solders.message import MessageV0
    from solders.pubkey import Pubkey
    from solders.signature import Signature
    from solders.system_program import TransferParams, transfer
    from solders.transaction import VersionedTransaction

    ix = transfer(TransferParams(from_pubkey=Pubkey.from_string(owner), to_pubkey=Keypair().pubkey(), lamports=1))
    msg = MessageV0.try_compile(Keypair().pubkey(), [ix], [], Hash.default())
    return base64.b64encode(bytes(VersionedTransaction.populate(msg, [Signature.default()] * 2))).decode()


def test_live_with_both_switches_builds_a_signing_executor(tmp_path):
    pytest.importorskip("solders")
    key, owner = generated_key()
    a = live_app(tmp_path, f"MEMES_LIVE=I_UNDERSTAND\nMEMES_PRIVATE_KEY={key}\n")
    try:
        ex = memes_app.make_meme_executor(a)
        assert isinstance(ex, LiveMemeExecutor) and ex.owner == owner
        assert key not in repr(a.secrets) and key not in repr(ex.__dict__)
    finally:
        a.close()


def test_vetter_follows_the_analyst_setting(tmp_path):
    a = memes_app.build_memes_app(write_config(tmp_path, 'analyst = "none"\n'), tmp_path / ".env")
    try:
        assert memes_app.make_vetter(a) is None
    finally:
        a.close()


def test_cycle_runs_and_resets_failures(app, monkeypatch, capsys):
    app.store.set_consecutive_failures(2)
    monkeypatch.setattr(memes_app, "run_meme_cycle", lambda deps: MemeCycleReport(equity=100.0))
    assert memes_app.cmd_memes_cycle(app) == 0
    assert app.store.consecutive_failures() == 0 and "equity=$100.00" in capsys.readouterr().out


def test_cycle_skips_when_locked(app, monkeypatch):
    monkeypatch.setattr(memes_app, "run_meme_cycle", lambda deps: (_ for _ in ()).throw(AssertionError("locked")))
    lock = app.settings.state_dir / "memes.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("1")
    assert memes_app.cmd_memes_cycle(app) == 0


def test_cycle_crashes_are_counted_and_alerted_every_third(app, monkeypatch):
    def boom(deps):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(memes_app, "run_meme_cycle", boom)
    assert [memes_app.cmd_memes_cycle(app) for _ in range(3)] == [1, 1, 1]
    assert app.store.consecutive_failures() == 3 and len(app.notifier.sent) == 1


def test_disabled_agent_does_nothing(tmp_path, monkeypatch):
    a = memes_app.build_memes_app(write_config(tmp_path, "enabled = false\n"), tmp_path / ".env")
    monkeypatch.setattr(memes_app, "run_meme_cycle", lambda deps: (_ for _ in ()).throw(AssertionError("off")))
    try:
        assert memes_app.cmd_memes_cycle(a) == 0
    finally:
        a.close()


def test_live_cycle_without_switches_is_a_counted_config_error(tmp_path):
    a = live_app(tmp_path, "")
    try:
        assert memes_app.cmd_memes_cycle(a) == 2 and a.store.consecutive_failures() == 1
    finally:
        a.close()


def test_status_shows_money_positions_and_trades(app, capsys):
    pos = make_meme_position()
    app.store.open_position(pos, MemeFill("BUY", pos.amount_raw, 3.0, 0.002), NOW, "x")
    app.store.mark_price(pos.id, 0.003)
    assert memes_app.cmd_memes_status(app) == 0
    out = capsys.readouterr().out
    assert "Memecoins | modo: paper" in out and "SEALS" in out and "+100%" in out and "BUY" in out
    assert "Prova em paper: 0/14 dias, 0/30 trades fechados" in out


def test_resume_clears_halt_and_resets_peak(app):
    app.store.set_halted(True)
    app.store.record_equity(NOW, 55.0, 55.0, 0.0)
    assert memes_app.cmd_memes_resume(app) == 0
    assert not app.store.is_halted() and app.store.peak() == 55.0


def test_live_check_in_paper_is_not_ready(app, capsys):
    assert memes_app.cmd_memes_live_check(app) == 1
    assert "NÃO está pronto" in capsys.readouterr().out


@respx.mock
def test_live_check_reads_the_wallet(tmp_path, capsys):
    pytest.importorskip("solders")
    key, owner = generated_key()
    a = live_app(tmp_path, f"MEMES_LIVE=I_UNDERSTAND\nMEMES_PRIVATE_KEY={key}\nMEMES_RPC_URL=https://rpc.test\n"
                           "TELEGRAM_BOT_TOKEN=123:abc\nTELEGRAM_CHAT_ID=42\n")

    def reply(request):
        body = request.read().decode()
        if "getBalance" in body:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"value": 50_000_000}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"value": [
            {"account": {"data": {"parsed": {"info": {"tokenAmount": {"amount": "25000000"}}}}}}]}})

    respx.post("https://rpc.test").mock(side_effect=reply)
    order = respx.get("https://api.jup.ag/swap/v2/order").mock(return_value=httpx.Response(200, json={
        "transaction": unsigned_swap_tx(owner), "requestId": "r1", "inAmount": "1000000", "outAmount": "5000000"}))
    a.jupiter().min_interval = 0
    try:
        assert memes_app.cmd_memes_live_check(a) == 0
    finally:
        a.close()
    out = capsys.readouterr().out
    assert owner in out and "0.0500 SOL" in out and "$25.00" in out and "PRONTO" in out
    assert order.calls[0].request.url.params["taker"] == owner  # a real order for this wallet...
    assert "nada foi enviado" in out  # ...signed locally and never executed (no /execute route exists here)
    assert "Aviso: o paper ainda não provou" in out  # a warning, not a block: the decision is the owner's
    assert key not in out


def test_main_dispatches_memes_commands(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(memes_app, "cmd_memes_status", lambda a: seen.append(a.store) or 0)
    assert cli.main(["--config", str(write_config(tmp_path)), "--env-file", str(tmp_path / ".env"), "memes-status"]) == 0
    assert seen and not (tmp_path / "state" / "desk.db").exists()  # the desk's database is not touched


def test_live_check_requires_telegram(tmp_path, capsys):
    pytest.importorskip("solders")
    key, _ = generated_key()
    a = live_app(tmp_path, f"MEMES_LIVE=I_UNDERSTAND\nMEMES_PRIVATE_KEY={key}\n")
    try:
        assert memes_app.cmd_memes_live_check(a) == 1
    finally:
        a.close()
    out = capsys.readouterr().out
    assert "Telegram" in out and "NÃO está pronto" in out


@pytest.mark.parametrize("body,expected", [("", True), ("x_enabled = false\n", False)])
def test_cycle_reads_x_through_fxtwitter_unless_switched_off(tmp_path, monkeypatch, body, expected):
    from desk.memes.x import FxTwitter

    seen = []
    monkeypatch.setattr(memes_app, "run_meme_cycle", lambda deps: seen.append(deps) or MemeCycleReport())
    a = memes_app.build_memes_app(write_config(tmp_path, body), tmp_path / ".env")
    try:
        assert memes_app.cmd_memes_cycle(a) == 0
    finally:
        a.close()
    assert isinstance(seen[0].x, FxTwitter) is expected


def test_cycle_gets_jupiter_token_lists(tmp_path, monkeypatch):
    from desk.memes.sources import JupiterTokens

    seen = []
    monkeypatch.setattr(memes_app, "run_meme_cycle", lambda deps: seen.append(deps) or MemeCycleReport())
    a = memes_app.build_memes_app(write_config(tmp_path), tmp_path / ".env")
    try:
        memes_app.cmd_memes_cycle(a)
    finally:
        a.close()
    assert isinstance(seen[0].tokens, JupiterTokens)


def test_exits_command_runs_the_fast_loop_with_the_shared_exit_lock(app, monkeypatch):
    seen = []
    monkeypatch.setattr(memes_app, "run_meme_exits", lambda deps: seen.append(deps) or MemeCycleReport())
    assert memes_app.cmd_memes_exits(app) == 0
    deps = seen[0]
    assert deps.vetter is None  # the fast loop never calls Claude
    with deps.exit_guard() as free:
        assert free
        with deps.exit_guard() as again:
            assert not again  # a second seller waits


def test_full_cycle_uses_the_same_exit_lock(app, monkeypatch):
    seen = []
    monkeypatch.setattr(memes_app, "run_meme_cycle", lambda deps: seen.append(deps) or MemeCycleReport())
    memes_app.cmd_memes_cycle(app)
    (app.settings.state_dir / "memes-exits.lock").write_text("1")
    with seen[0].exit_guard() as free:
        assert not free


def test_exits_command_does_nothing_when_disabled(tmp_path, monkeypatch):
    a = memes_app.build_memes_app(write_config(tmp_path, "enabled = false\n"), tmp_path / ".env")
    monkeypatch.setattr(memes_app, "run_meme_exits", lambda deps: (_ for _ in ()).throw(AssertionError("off")))
    try:
        assert memes_app.cmd_memes_exits(a) == 0
    finally:
        a.close()


def test_main_knows_the_exits_command(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(memes_app, "cmd_memes_exits", lambda a: seen.append(1) or 0)
    assert cli.main(["--config", str(write_config(tmp_path)), "--env-file", str(tmp_path / ".env"), "memes-exits"]) == 0
    assert seen == [1]


@respx.mock
def test_live_check_fails_when_jupiter_will_not_build_the_swap(tmp_path, capsys):
    pytest.importorskip("solders")
    key, _ = generated_key()
    a = live_app(tmp_path, f"MEMES_LIVE=I_UNDERSTAND\nMEMES_PRIVATE_KEY={key}\nMEMES_RPC_URL=https://rpc.test\n"
                           "TELEGRAM_BOT_TOKEN=123:abc\nTELEGRAM_CHAT_ID=42\n")

    def reply(request):
        if "getBalance" in request.read().decode():
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"value": 50_000_000}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"value": [
            {"account": {"data": {"parsed": {"info": {"tokenAmount": {"amount": "25000000"}}}}}}]}})

    respx.post("https://rpc.test").mock(side_effect=reply)
    respx.get("https://api.jup.ag/swap/v2/order").mock(return_value=httpx.Response(200, json={
        "transaction": "", "requestId": "r1", "errorCode": 1, "errorMessage": "Insufficient funds"}))
    a.jupiter().min_interval = 0
    try:
        assert memes_app.cmd_memes_live_check(a) == 1
    finally:
        a.close()
    out = capsys.readouterr().out
    assert "Insufficient funds" in out and "NÃO está pronto" in out


def test_one_jupiter_gateway_with_the_optional_key_serves_quotes_swaps_and_lists(tmp_path):
    (tmp_path / ".env").write_text("MEMES_JUPITER_API_KEY=jk-1\n", encoding="utf-8")
    a = memes_app.build_memes_app(write_config(tmp_path), tmp_path / ".env")
    try:
        deps = memes_app.build_deps(a, with_vetter=False)
        gate = a.jupiter()
        assert deps.jupiter._api is gate and deps.tokens._api is gate and deps.executor._jup._api is gate
        assert gate.min_interval < 2.0  # the free key's tier: 1 request per second
    finally:
        a.close()


def test_status_lists_uncertain_buys_still_being_checked(app, capsys):
    import json

    app.store.set_meta("pending_buy:" + "M" * 44, json.dumps({"symbol": "LOST", "usd": 3.0, "at": NOW.isoformat()}))
    assert memes_app.cmd_memes_status(app) == 0
    out = capsys.readouterr().out
    assert "Compras incertas" in out and "LOST" in out and "$3.00" in out


def test_sell_all_needs_yes_and_changes_nothing_without_it(app, capsys):
    pos = make_meme_position()
    app.store.open_position(pos, MemeFill("BUY", pos.amount_raw, 3.0, 0.002), NOW, "x")
    assert memes_app.cmd_memes_sell_all(app) == 1
    out = capsys.readouterr().out
    assert "--yes" in out and "SEALS" in out
    assert len(app.store.open_positions()) == 1 and not app.store.is_halted()


def test_sell_all_with_yes_runs_the_emergency_exit(app, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(memes_app, "run_meme_sell_all", lambda deps: seen.append(deps) or MemeCycleReport(exits=2))
    assert memes_app.cmd_memes_sell_all(app, yes=True) == 0
    assert seen and seen[0].vetter is None  # Claude is never asked during an emergency exit
    assert "2 venda(s)" in capsys.readouterr().out


def test_main_passes_yes_to_sell_all(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(memes_app, "cmd_memes_sell_all", lambda a, yes=False: seen.append(yes) or 0)
    args = ["--config", str(write_config(tmp_path)), "--env-file", str(tmp_path / ".env"), "memes-sell-all"]
    assert cli.main(args + ["--yes"]) == 0 and cli.main(args) == 0
    assert seen == [True, False]


def test_waiting_for_a_lock_retries_until_it_is_free():
    from contextlib import contextmanager

    answers, slept = [False, False, True], []

    @contextmanager
    def lock():
        yield answers.pop(0)

    with memes_app.wait_for(lock, seconds=60, sleep=slept.append) as free:
        assert free
    assert slept == [5, 5]


def test_waiting_for_a_lock_gives_up_after_the_deadline():
    from contextlib import contextmanager

    @contextmanager
    def lock():
        yield False

    clock = iter(range(0, 1000, 5))
    with memes_app.wait_for(lock, seconds=12, sleep=lambda s: None, clock=lambda: next(clock)) as free:
        assert not free

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
    try:
        assert memes_app.cmd_memes_live_check(a) == 0
    finally:
        a.close()
    out = capsys.readouterr().out
    assert owner in out and "0.0500 SOL" in out and "$25.00" in out and "PRONTO" in out
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

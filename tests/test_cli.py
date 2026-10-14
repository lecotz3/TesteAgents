import httpx
import pytest
import respx

from desk import cli
from desk.cycle import CycleReport
from desk.geoblock import GeoStatus
from tests.factories import NOW, CapturingNotifier, FakeSdkClient


def write_config(tmp_path, body='mode = "paper"\nbankroll_start_usd = 100.0\n'):
    cfg = tmp_path / "config.toml"
    cfg.write_text(body, encoding="utf-8")
    return cfg


@pytest.fixture
def app(tmp_path):
    a = cli.build_app(write_config(tmp_path), tmp_path / ".env")
    a.notifier = CapturingNotifier()
    yield a
    a.store.close()
    a.http.close()


def test_state_dir_is_relative_to_config(app, tmp_path):
    assert app.settings.state_dir == tmp_path / "state"


def test_resume_clears_halt_and_resets_peak(app):
    app.store.set_halted(True)
    app.store.record_equity(NOW, 55.0, 55.0, 0.0)
    assert cli.cmd_resume(app) == 0
    assert not app.store.is_halted()
    assert app.store.peak() == 55.0


def test_monthly_reports_once_per_month(app):
    assert cli.cmd_monthly(app, now=NOW) == 0
    assert cli.cmd_monthly(app, now=NOW) == 0
    assert len(app.notifier.sent) == 1
    assert "[PAPER]" in app.notifier.sent[0] and "Cobre a assinatura" in app.notifier.sent[0]
    cli.cmd_monthly(app, force=True, now=NOW)
    assert len(app.notifier.sent) == 2


def test_monthly_reports_deficit(app):
    app.store.adjust_cash(-70.0)
    app.store.record_equity(NOW, 30.0, 30.0, 0.0)
    cli.cmd_monthly(app, now=NOW)
    assert "Faltam $40.00" in app.notifier.sent[0]


def test_cycle_skips_when_locked(app, monkeypatch):
    def boom(deps):
        raise AssertionError("run_cycle must not run while locked")

    monkeypatch.setattr(cli, "run_cycle", boom)
    lock = app.settings.state_dir / "desk.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("1")
    assert cli.cmd_cycle(app) == 0


def test_cycle_failures_are_counted_and_alerted_every_third(app, monkeypatch):
    def boom(deps):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "run_cycle", boom)
    assert [cli.cmd_cycle(app) for _ in range(3)] == [1, 1, 1]
    assert app.store.consecutive_failures() == 3
    assert len(app.notifier.sent) == 1


def test_cycle_success_resets_failures(app, monkeypatch, capsys):
    app.store.set_consecutive_failures(2)
    monkeypatch.setattr(cli, "run_cycle", lambda deps: CycleReport(equity=100.0))
    assert cli.cmd_cycle(app) == 0
    assert app.store.consecutive_failures() == 0
    assert "equity=$100.00" in capsys.readouterr().out


def test_live_mode_without_switches_refuses_to_run(tmp_path):
    a = cli.build_app(write_config(tmp_path, 'mode = "live"\n'), tmp_path / ".env")
    a.notifier = CapturingNotifier()
    try:
        assert cli.cmd_cycle(a) == 2
        assert a.store.consecutive_failures() == 1
    finally:
        a.store.close()
        a.http.close()


def test_status_prints_summary(app, capsys):
    assert cli.cmd_status(app) == 0
    out = capsys.readouterr().out
    assert "Modo: paper" in out and "Equity: $100.00" in out and "Posições abertas (0/5)" in out


def test_main_reports_config_errors(tmp_path):
    assert cli.main(["--config", str(tmp_path / "missing.toml"), "status"]) == 2


def live_check(tmp_path, monkeypatch, client, capsys, telegram="TELEGRAM_BOT_TOKEN=123:abc\nTELEGRAM_CHAT_ID=42\n"):
    env = tmp_path / ".env"
    env.write_text("DESK_LIVE=I_UNDERSTAND\nPOLY_PRIVATE_KEY=0xkey\n" + telegram, encoding="utf-8")
    a = cli.build_app(write_config(tmp_path, 'mode = "live"\nbankroll_start_usd = 100.0\n'), env)
    monkeypatch.setattr(cli, "check_geoblock", lambda http: GeoStatus(blocked=False, country="PT", region=""))
    monkeypatch.setattr(cli.SdkTrader, "connect", classmethod(lambda cls, key, wallet: cls(client)))
    try:
        return cli.cmd_live_check(a), capsys.readouterr().out
    finally:
        a.store.close()
        a.http.close()


def test_live_check_ready_shows_balance(tmp_path, monkeypatch, capsys):
    code, out = live_check(tmp_path, monkeypatch, FakeSdkClient(balance=100_000_000), capsys)
    assert code == 0
    assert "$100.00" in out and "PRONTO para modo real" in out


def test_live_check_flags_closed_only_account(tmp_path, monkeypatch, capsys):
    code, out = live_check(tmp_path, monkeypatch, FakeSdkClient(balance=100_000_000, closed_only=True), capsys)
    assert code == 1
    assert "closed-only" in out and "NÃO está pronto" in out


def test_live_mode_refuses_the_paper_state_dir(tmp_path):
    paper = cli.build_app(write_config(tmp_path), tmp_path / ".env")
    paper.store.close()
    paper.http.close()
    with pytest.raises(cli.ConfigError, match="desk.db") as err:
        cli.build_app(write_config(tmp_path, 'mode = "live"\n'), tmp_path / ".env")
    assert "memes" in str(err.value)  # renaming the whole folder would also reset the memecoin agent


def test_live_check_requires_telegram(tmp_path, monkeypatch, capsys):
    code, out = live_check(tmp_path, monkeypatch, FakeSdkClient(balance=100_000_000), capsys, telegram="")
    assert code == 1 and "Telegram" in out and "NÃO está pronto" in out


def test_crash_while_building_the_cycle_is_counted(app, monkeypatch):
    def broken_executor(settings, secrets, http):
        raise RuntimeError("SDK could not derive credentials")

    monkeypatch.setattr(cli, "make_executor", broken_executor)
    assert cli.cmd_cycle(app) == 2
    assert app.store.consecutive_failures() == 1


def test_status_shows_what_the_desk_cost_and_earned(app, capsys):
    app.store.charge(NOW, "rent", 0.67, "aluguel")
    app.store.record_equity(NOW, 99.33, 99.33, 0.0)
    cli.cmd_status(app)
    out = capsys.readouterr().out
    assert "Custos de operação: $0.67" in out
    assert "Resultado de trading: $+0.00" in out and "Líquido: $-0.67" in out


def test_monthly_report_says_whether_it_paid_for_itself(app):
    app.store.charge(NOW, "rent", 20.0, "aluguel do mês")
    app.store.record_equity(NOW, 80.0, 80.0, 0.0)
    cli.cmd_monthly(app, now=NOW)
    assert "custos $20.00" in app.notifier.sent[0] and "não se pagou" in app.notifier.sent[0]


def test_dashboard_command_serves_on_localhost(app, monkeypatch, capsys):
    served = {}

    class FakeServer:
        server_address = ("127.0.0.1", 8765)

        def serve_forever(self):
            served["yes"] = True

        def server_close(self):
            served["closed"] = True

    def fake_make_server(state_dir, settings, **kwargs):
        served["state_dir"] = state_dir
        return FakeServer()

    opened = []
    monkeypatch.setattr(cli, "make_server", fake_make_server)
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    assert cli.cmd_dashboard(app, port=8765, open_browser=True) == 0
    assert served == {"state_dir": app.settings.state_dir, "yes": True, "closed": True}
    assert opened == ["http://127.0.0.1:8765/"]
    assert "http://127.0.0.1:8765/" in capsys.readouterr().out


def busy_port(state_dir, settings, **kwargs):
    raise OSError("address already in use")


@respx.mock
def test_dashboard_command_opens_the_panel_already_running_in_the_background(app, monkeypatch, capsys):
    respx.get("http://127.0.0.1:8765/api/state").mock(return_value=httpx.Response(200, json={"now": "x"}))
    opened = []
    monkeypatch.setattr(cli, "make_server", busy_port)
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    assert cli.cmd_dashboard(app, port=8765, open_browser=True) == 0
    assert opened == ["http://127.0.0.1:8765/"]
    assert "já está rodando" in capsys.readouterr().out


@respx.mock
def test_dashboard_command_reports_a_port_taken_by_something_else(app, monkeypatch, capsys):
    respx.get("http://127.0.0.1:8765/api/state").mock(side_effect=httpx.ConnectError("refused"))
    opened = []
    monkeypatch.setattr(cli, "make_server", busy_port)
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    assert cli.cmd_dashboard(app, port=8765, open_browser=True) == 2
    assert opened == []
    assert "--port 8766" in capsys.readouterr().out


def test_monthly_report_includes_the_memecoin_agent(app):
    from desk.memes.store import MemeStore

    memes = MemeStore(app.settings.state_dir / "memes.db", start_cash=100.0)
    memes.record_equity(NOW, 104.5, 104.5, 0.0)
    memes.charge(NOW, "vet", 0.25, "x")
    memes.close()
    cli.cmd_monthly(app, force=True, now=NOW)
    message = app.notifier.sent[-1]
    assert "Memecoins" in message and "$104.50" in message and "+4.50" in message and "0/30 trades" in message


def test_monthly_report_without_memes_db_is_unchanged(app):
    cli.cmd_monthly(app, force=True, now=NOW)
    assert "Memecoins" not in app.notifier.sent[-1]

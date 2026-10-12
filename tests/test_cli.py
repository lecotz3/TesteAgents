import pytest

from desk import cli
from desk.cycle import CycleReport
from tests.factories import NOW, CapturingNotifier


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

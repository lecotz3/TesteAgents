import json
import subprocess
import threading
from datetime import timedelta

import httpx
import pytest

from desk.config import Settings
from desk.dashboard import downsample, make_server, snapshot, tail, windows_scheduler
from desk.models import TradeRecord
from desk.store import Store
from tests.factories import NOW, make_position


@pytest.fixture
def state(tmp_path):
    state_dir = tmp_path / "state"
    store = Store(state_dir / "desk.db", start_cash=100.0)
    store.ensure_mode("paper")
    store.charge(NOW - timedelta(hours=1), "rent", 0.67, "aluguel")
    store.record_equity(NOW - timedelta(minutes=10), 99.33, 93.33, 6.0)
    pos = make_position()
    pos.id = store.add_position(pos)
    store.add_trade(TradeRecord(NOW, pos.id, pos.market_id, "BUY", 9.67, 0.62, 0.0, -6.0, "vantagem 12.0pp"))
    store.record_cycle(NOW - timedelta(minutes=10), {"scanned": 968, "entries": 1, "skips": {"low_confidence": 2},
                                                     "errors": []}, [
        {"market_id": "1001", "slug": "will-it-rain-in-nyc", "question": "Will it rain in NYC on Oct 14?",
         "price": 0.62, "fair_prob": 0.74, "edge_pp": 12.0, "confidence": "medium", "decision": "entered",
         "detail": "Yes: 9.67 cotas"},
    ])
    store.close()
    (state_dir / "desk.log").write_text("".join(f"linha {i}\n" for i in range(100)), encoding="utf-8")
    (state_dir / "trades.txt").write_text("ENTRADA [PAPER] Will it rain?\n", encoding="utf-8")
    (tmp_path / ".env").write_text("POLY_PRIVATE_KEY=0xsupersecret\n", encoding="utf-8")
    return state_dir


def no_scheduler():
    return {"installed": True, "state": "Ready", "next_run": "2026-10-12T09:10:00-03:00"}


def test_snapshot_shows_money_positions_and_decisions(state):
    snap = snapshot(state, Settings(), NOW, no_scheduler)
    assert snap["mode"] == "paper" and snap["halted"] is False
    assert snap["economics"]["costs_total"] == pytest.approx(0.67)
    assert snap["economics"]["equity"] == pytest.approx(99.33)
    assert snap["positions"][0]["question"] == "Will it rain in NYC on Oct 14?"
    assert snap["trades"][0]["question"] == "Will it rain in NYC on Oct 14?"
    assert snap["decisions"][0]["decision"] == "entered"
    assert snap["cycles"][0]["scanned"] == 968
    assert snap["last_cycle_minutes_ago"] == pytest.approx(10)
    assert snap["scheduler"]["state"] == "Ready"
    assert snap["log_tail"][-1] == "linha 99" and len(snap["log_tail"]) == 40
    json.dumps(snap)  # everything is JSON-ready


def test_snapshot_never_contains_secrets(state):
    assert "supersecret" not in json.dumps(snapshot(state, Settings(), NOW, no_scheduler))


def test_tail_handles_missing_files_and_long_logs(tmp_path):
    assert tail(tmp_path / "nope.log", 5) == []
    log = tmp_path / "big.log"
    log.write_text("x" * 200_000 + "\nfim\n", encoding="utf-8")
    assert tail(log, 1) == ["fim"]


def test_downsample_keeps_ends_and_size():
    points = [(i, float(i)) for i in range(1000)]
    small = downsample(points, 300)
    assert len(small) <= 300 and small[0] == points[0] and small[-1] == points[-1]
    assert downsample(points[:10], 300) == points[:10]


def test_scheduler_status_parses_powershell_json():
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout='{"installed": true, "state": "Ready"}', stderr="")

    assert windows_scheduler(run=fake_run, platform="win32") == {"installed": True, "state": "Ready"}
    assert windows_scheduler(run=fake_run, platform="linux") is None

    def broken(cmd, **kwargs):
        raise FileNotFoundError("powershell")

    assert windows_scheduler(run=broken, platform="win32")["installed"] is None


@pytest.fixture
def server(state):
    srv = make_server(state, Settings(), port=0, scheduler=no_scheduler, clock=lambda: NOW)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def base(srv):
    host, port = srv.server_address[:2]
    return f"http://{host}:{port}"


def test_server_listens_only_on_localhost(server):
    assert server.server_address[0] == "127.0.0.1"


def test_server_serves_page_and_state(server):
    page = httpx.get(base(server) + "/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert "Polymarket Desk" in page.text
    state = httpx.get(base(server) + "/api/state")
    assert state.status_code == 200 and state.json()["economics"]["equity"] == pytest.approx(99.33)
    assert httpx.get(base(server) + "/nada").status_code == 404


def test_server_rejects_foreign_host_header(server):
    # DNS rebinding: a web page on another site must not read the desk through the browser.
    assert httpx.get(base(server) + "/api/state", headers={"Host": "evil.example"}).status_code == 403

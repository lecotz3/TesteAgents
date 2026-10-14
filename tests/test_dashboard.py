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
from desk.memes.models import MemeFill
from desk.memes.store import MemeStore
from tests.factories import NOW, make_position
from tests.meme_factories import MINT, make_meme_position


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


def test_snapshot_without_the_memes_agent_has_no_memes_section(state):
    assert snapshot(state, Settings(), NOW, no_scheduler)["memes"] is None


def add_memes(state_dir):
    store = MemeStore(state_dir / "memes.db", start_cash=100.0)
    store.ensure_mode("paper")
    pos = make_meme_position()
    store.open_position(pos, MemeFill("BUY", pos.amount_raw, 3.0, 0.002), NOW - timedelta(hours=1), "ida e volta 2%")
    store.mark_price(pos.id, 0.003)
    store.charge(NOW - timedelta(minutes=30), "vet", 0.01, "SEALS")
    store.record_equity(NOW - timedelta(minutes=5), 102.99, 96.99, 6.0)
    store.record_cycle(NOW - timedelta(minutes=5), {"discovered": 59, "entries": 1, "skips": {}, "errors": []}, [
        {"mint": MINT, "symbol": "SEALS", "price_usd": 0.0015, "liquidity_usd": 1e5, "market_cap_usd": 1.3e6,
         "decision": "bought", "detail": "$3.00"},
    ])
    store.close()
    (state_dir / "memes.txt").write_text("COMPRA MEMECOIN [PAPER] SEALS\n", encoding="utf-8")


def test_snapshot_shows_the_memes_agent(state):
    add_memes(state)
    memes = snapshot(state, Settings(), NOW, no_scheduler, memes_scheduler=lambda: {"installed": False})["memes"]
    assert memes["mode"] == "paper" and memes["equity"] == pytest.approx(102.99)
    assert memes["costs_total"] == pytest.approx(0.01) and memes["net"] == pytest.approx(2.99)
    [pos] = memes["positions"]
    assert pos["symbol"] == "SEALS" and pos["value"] == pytest.approx(6.0) and pos["change_pct"] == pytest.approx(100)
    assert pos["link"] == "https://fomo.family/tokens/solana/" + MINT
    assert memes["trades"][0]["side"] == "BUY" and memes["decisions"][0]["decision"] == "bought"
    assert memes["cycles"][0]["discovered"] == 59 and memes["last_cycle_minutes_ago"] == pytest.approx(5)
    assert memes["journal_tail"] == ["COMPRA MEMECOIN [PAPER] SEALS"] and memes["scheduler"] == {"installed": False}
    assert memes["shutdown_line"] == pytest.approx(102.99 * 0.6)
    assert memes["readiness"]["ready"] is False and "0/30 trades" in memes["readiness"]["summary"]
    json.dumps(memes)


def test_memes_review_on_the_panel_reads_only_the_cache(state):
    from desk.memes.review import rules_key

    add_memes(state)
    [row] = snapshot(state, Settings(), NOW, no_scheduler)["memes"]["review"]
    assert row["decision"] == "bought" and row["count"] == 0 and row["open"] == 1  # 24h not over yet: no API call
    (state / "memes-review.json").write_text(json.dumps({"rules": rules_key(Settings().memes), "windows": {
        f"{MINT}|bought": {"mint": MINT, "symbol": "SEALS", "decision": "bought", "ts": NOW.isoformat(),
                           "status": "closed", "ret": 0.4, "exit": "take_profit"}}}), encoding="utf-8")
    [row] = snapshot(state, Settings(), NOW, no_scheduler)["memes"]["review"]
    assert row["count"] == 1 and row["average"] == pytest.approx(0.4) and row["wins"] == 1 and row["open"] == 0


def test_panel_shows_the_biggest_moves_now_and_the_peaks_of_the_day(state):
    from desk.memes.movers import parse_mover
    from tests.test_memes_movers import jup_item

    add_memes(state)
    store = MemeStore(state / "memes.db", start_cash=100.0)
    s = Settings().memes
    store.record_movers(NOW - timedelta(hours=2), [parse_mover(jup_item(change_1h=300.0, mcap=3e6), NOW, s)])
    store.record_movers(NOW - timedelta(minutes=3), [parse_mover(jup_item(change_1h=-20.0, mcap=8e5), NOW, s)])
    store.close()
    movers = snapshot(state, Settings(), NOW, no_scheduler)["memes"]["movers"]
    [now] = movers["now"]
    assert now["symbol"] == "FPAD" and now["change_1h"] == -20.0 and now["link"].startswith("https://fomo.family/")
    assert now["verdict"] == "low_liquidity" and movers["updated"] == (NOW - timedelta(minutes=3)).isoformat()
    [peak] = movers["day"]
    assert peak["peak_change_1h"] == 300.0 and peak["peak_mcap"] == 3e6 and peak["mcap_now"] == 8e5
    json.dumps(movers)


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


def test_scheduler_status_can_ask_about_the_memes_task():
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, stdout='{"installed": false}', stderr="")

    assert windows_scheduler(run=fake_run, platform="win32", task="PolymarketDeskMemes") == {"installed": False}
    assert "'PolymarketDeskMemes'" in seen[0] and "'PolymarketDesk'" not in seen[0]


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
    assert "Painel dos robôs" in page.text and "Memecoins (Solana)" in page.text
    state = httpx.get(base(server) + "/api/state")
    assert state.status_code == 200 and state.json()["economics"]["equity"] == pytest.approx(99.33)
    assert httpx.get(base(server) + "/nada").status_code == 404


def test_a_second_dashboard_cannot_take_the_same_port(server, state):
    # On Windows SO_REUSEADDR lets two servers share a port: a second copy would serve in silence
    # instead of failing, so `dashboard` would never notice the panel already running.
    with pytest.raises(OSError):
        make_server(state, Settings(), port=server.server_address[1], scheduler=no_scheduler)


def test_server_rejects_foreign_host_header(server):
    # DNS rebinding: a web page on another site must not read the desk through the browser.
    assert httpx.get(base(server) + "/api/state", headers={"Host": "evil.example"}).status_code == 403


def test_memes_snapshot_lists_uncertain_orders_and_why_buying_stopped(state):
    add_memes(state)
    store = MemeStore(state / "memes.db", start_cash=100.0)
    try:
        store.set_meta("pending_buy:" + "M" * 44, json.dumps({"symbol": "LOST", "usd": 3.0, "at": NOW.isoformat()}))
        pos_id = store.open_positions()[0].id
        store.set_meta(f"uncertain_sell:{pos_id}", json.dumps({"at": NOW.isoformat(), "reason": "stop_loss",
                                                                "amount": 1, "price": 0.001}))
        store.set_halted(True)
        store.set_meta("halt_reason", "manual")
    finally:
        store.close()
    memes = snapshot(state, Settings(), NOW, no_scheduler)["memes"]
    assert memes["halt_reason"] == "manual"
    kinds = {(u["kind"], u["symbol"]) for u in memes["uncertain"]}
    assert kinds == {("compra", "LOST"), ("venda", "SEALS")}
    json.dumps(memes)

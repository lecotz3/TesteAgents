"""Local, read-only dashboard: http://127.0.0.1:8765 shows what the desk is doing and what it costs."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Callable

from desk.config import Settings
from desk.economy import economics
from desk.store import Store

MAX_CURVE_POINTS = 300
CREATE_NO_WINDOW = 0x08000000

# Asks Task Scheduler about the desk task and prints one JSON line (empty task -> installed=false).
_PS_QUERY = (
    "$t = Get-ScheduledTask -TaskName 'PolymarketDesk' -ErrorAction SilentlyContinue; "
    "if (-not $t) { '{\"installed\": false}' } else { "
    "$i = Get-ScheduledTaskInfo -TaskName 'PolymarketDesk'; "
    "@{installed=$true; state=[string]$t.State; "
    "next_run=$i.NextRunTime.ToString('yyyy-MM-ddTHH:mm:sszzz'); "
    "last_run=$i.LastRunTime.ToString('yyyy-MM-ddTHH:mm:sszzz'); "
    "last_result=$i.LastTaskResult} | ConvertTo-Json -Compress }"
)


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else None


def tail(path: Path, lines: int) -> list[str]:
    """Last `lines` lines of a text file, reading only its end (logs grow forever)."""
    if not path.exists():
        return []
    with path.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - 64 * 1024))
        text = fh.read().decode("utf-8", errors="replace")
    return [line for line in text.splitlines() if line.strip()][-lines:]


def downsample(points: list, limit: int) -> list:
    """At most `limit` points, evenly spaced, always keeping the first and the last."""
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    return [points[round(i * step)] for i in range(limit)]


def windows_scheduler(run: Callable = subprocess.run, platform: str = sys.platform) -> dict | None:
    """State of the PolymarketDesk scheduled task, or None off Windows."""
    if platform != "win32":
        return None
    try:
        out = run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_QUERY],
            capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW,
        )
        return json.loads((out.stdout or "").strip() or "{}")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {"installed": None, "error": type(exc).__name__}


class CachedScheduler:
    """PowerShell takes about a second; the page refreshes every 30 s, so ask at most once a minute."""

    def __init__(self, query: Callable[[], dict | None] = windows_scheduler, ttl: float = 60.0):
        self._query = query
        self._ttl = ttl
        self._value: dict | None = None
        self._at = float("-inf")

    def __call__(self) -> dict | None:
        if time.monotonic() - self._at > self._ttl:
            self._value = self._query()
            self._at = time.monotonic()
        return self._value


def snapshot(state_dir: Path, settings: Settings, now: datetime, scheduler: Callable[[], dict | None]) -> dict:
    store = Store.open_readonly(state_dir / "desk.db")
    try:
        econ = economics(store, settings, now)
        positions = store.open_positions()
        closed = store.closed_positions()
        questions = {p.id: p.question for p in positions + closed}
        cycles = store.recent_cycles(30)
        peak = store.peak()
        curve = downsample(store.equity_history(now - timedelta(days=30)), MAX_CURVE_POINTS)
        return {
            "now": _iso(now),
            "mode": settings.mode,
            "analyst": settings.analysis.analyst,
            "halted": store.is_halted(),
            "economics": econ.as_dict(),
            "cash": store.cash(),
            "peak": peak,
            "drawdown": (peak - econ.equity) / peak if peak > 0 else 0.0,
            "drawdown_halt": settings.risk.drawdown_halt,
            "max_positions": settings.risk.max_open_positions,
            "positions": [
                {
                    "question": p.question, "slug": p.slug, "outcome": p.outcome, "shares": p.shares,
                    "avg_price": p.avg_price, "cost": p.cost, "entry_fair_prob": p.entry_fair_prob,
                    "exit_condition": p.exit_condition, "deadline": _iso(p.end_date), "opened_at": _iso(p.opened_at),
                }
                for p in positions
            ],
            "closed": {
                "count": len(closed),
                "wins": sum(1 for p in closed if (p.proceeds or 0.0) > p.cost),
                "pnl": sum((p.proceeds or 0.0) - p.cost for p in closed),
            },
            "trades": [
                {
                    "ts": _iso(t.ts), "question": questions.get(t.position_id, t.market_id), "action": t.action,
                    "shares": t.shares, "price": t.price, "fee": t.fee, "cash_delta": t.cash_delta, "reason": t.reason,
                }
                for t in store.trades(limit=25)
            ],
            "cycles": [{**c, "ts": _iso(c["ts"])} for c in cycles],
            "last_cycle_minutes_ago": (now - cycles[0]["ts"]).total_seconds() / 60 if cycles else None,
            "decisions": [{**d, "ts": _iso(d["ts"])} for d in store.recent_decisions(60)],
            "equity_curve": [[_iso(ts), equity] for ts, equity in curve],
            "analyst_calls_24h": store.analyst_calls_since(now - timedelta(hours=24)),
            "analyst_cap": settings.analysis.max_analyses_per_day,
            "log_tail": tail(state_dir / "desk.log", 40),
            "journal_tail": tail(state_dir / "trades.txt", 80),
            "scheduler": scheduler(),
        }
    finally:
        store.close()


def make_server(
    state_dir: Path, settings: Settings, *, host: str = "127.0.0.1", port: int = 8765,
    scheduler: Callable[[], dict | None] | None = None, clock: Callable[[], datetime] | None = None,
) -> ThreadingHTTPServer:
    page = resources.files("desk").joinpath("dashboard.html").read_bytes()
    ask_scheduler = scheduler or CachedScheduler()
    now = clock or (lambda: datetime.now(timezone.utc))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            port_now = self.server.server_address[1]
            allowed = {f"127.0.0.1:{port_now}", f"localhost:{port_now}"}
            if self.headers.get("Host") not in allowed:
                self._send(403, "text/plain; charset=utf-8", b"forbidden")
                return
            if self.path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", page)
            elif self.path == "/api/state":
                try:
                    body = json.dumps(snapshot(state_dir, settings, now(), ask_scheduler), ensure_ascii=False)
                    self._send(200, "application/json; charset=utf-8", body.encode("utf-8"))
                except Exception as exc:  # noqa: BLE001 - show the error on the page instead of a dead socket
                    body = json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)
                    self._send(500, "application/json; charset=utf-8", body.encode("utf-8"))
            else:
                self._send(404, "text/plain; charset=utf-8", b"not found")

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:  # noqa: A002 - keep the console quiet
            pass

    return ThreadingHTTPServer((host, port), Handler)

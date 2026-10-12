"""SQLite persistence: cash, peak, flags, positions, trades, analyses and equity history."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from desk.models import Analysis, Evidence, Position, TradeRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL, slug TEXT NOT NULL, question TEXT NOT NULL, category TEXT NOT NULL,
    outcome_index INTEGER NOT NULL, outcome TEXT NOT NULL, token_id TEXT NOT NULL,
    shares REAL NOT NULL, avg_price REAL NOT NULL, fee_paid REAL NOT NULL, entry_fair_prob REAL NOT NULL,
    exit_condition TEXT NOT NULL, resolution_source TEXT NOT NULL, end_date TEXT NOT NULL, opened_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', closed_at TEXT, exit_price REAL, proceeds REAL, close_reason TEXT,
    event_id TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, position_id INTEGER NOT NULL, market_id TEXT NOT NULL,
    action TEXT NOT NULL, shares REAL NOT NULL, price REAL NOT NULL, fee REAL NOT NULL,
    cash_delta REAL NOT NULL, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    market_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, backend TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analysis_failures (market_id TEXT PRIMARY KEY, failed_at TEXT NOT NULL, error TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS equity (ts TEXT NOT NULL, equity REAL NOT NULL, cash REAL NOT NULL, positions_value REAL NOT NULL);
CREATE TABLE IF NOT EXISTS entry_blocks (market_id TEXT PRIMARY KEY, until TEXT NOT NULL, reason TEXT NOT NULL);
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class ModeMismatch(Exception):
    """The database belongs to the other mode (paper vs live)."""

    def __init__(self, stored: str, requested: str):
        super().__init__(f"banco de dados do modo {stored}, configuração pede {requested}")
        self.stored = stored
        self.requested = requested


class Store:
    def __init__(self, path: Path, start_cash: float):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._migrate()
        if self.get_meta("cash") is None:
            for key, value in (("cash", start_cash), ("peak", start_cash), ("halted", 0), ("consecutive_failures", 0)):
                self.set_meta(key, str(value))

    def _migrate(self) -> None:
        columns = {r["name"] for r in self._db.execute("PRAGMA table_info(positions)").fetchall()}
        if "event_id" not in columns:  # databases created before event grouping
            with self._db:
                self._db.execute("ALTER TABLE positions ADD COLUMN event_id TEXT")

    def close(self) -> None:
        self._db.close()

    # --- meta / flags -------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._db:
            self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def cash(self) -> float:
        return float(self.get_meta("cash") or 0.0)

    def adjust_cash(self, delta: float) -> None:
        self.set_meta("cash", repr(self.cash() + delta))

    def peak(self) -> float:
        return float(self.get_meta("peak") or 0.0)

    def reset_peak(self, value: float) -> None:
        self.set_meta("peak", repr(value))

    def is_halted(self) -> bool:
        return self.get_meta("halted") == "1"

    def set_halted(self, halted: bool) -> None:
        self.set_meta("halted", "1" if halted else "0")

    def consecutive_failures(self) -> int:
        return int(self.get_meta("consecutive_failures") or 0)

    def set_consecutive_failures(self, count: int) -> None:
        self.set_meta("consecutive_failures", str(count))

    def ensure_mode(self, mode: str) -> None:
        """Pin the database to one mode so live never inherits paper cash, positions or peak."""
        stored = self.get_meta("mode")
        if stored is None:
            has_history = self._db.execute(
                "SELECT EXISTS(SELECT 1 FROM equity) OR EXISTS(SELECT 1 FROM positions)"
            ).fetchone()[0]
            stored = "paper" if has_history else mode  # history from before pinning can only be paper
            self.set_meta("mode", stored)
        if stored != mode:
            raise ModeMismatch(stored, mode)

    # --- equity -------------------------------------------------------------
    def record_equity(self, ts: datetime, equity: float, cash: float, positions_value: float) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO equity (ts, equity, cash, positions_value) VALUES (?, ?, ?, ?)",
                (_iso(ts), equity, cash, positions_value),
            )
        if equity > self.peak():
            self.reset_peak(equity)

    def last_equity(self) -> float | None:
        row = self._db.execute("SELECT equity FROM equity ORDER BY rowid DESC LIMIT 1").fetchone()
        return float(row["equity"]) if row else None

    # --- positions ----------------------------------------------------------
    def add_position(self, pos: Position) -> int:
        with self._db:
            cur = self._db.execute(
                """INSERT INTO positions (market_id, slug, question, category, outcome_index, outcome, token_id,
                       shares, avg_price, fee_paid, entry_fair_prob, exit_condition, resolution_source, end_date, opened_at,
                       event_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pos.market_id, pos.slug, pos.question, pos.category, pos.outcome_index, pos.outcome, pos.token_id,
                    pos.shares, pos.avg_price, pos.fee_paid, pos.entry_fair_prob, pos.exit_condition,
                    pos.resolution_source, _iso(pos.end_date), _iso(pos.opened_at), pos.event_id,
                ),
            )
        return int(cur.lastrowid)

    def open_positions(self) -> list[Position]:
        return self._positions("open")

    def closed_positions(self) -> list[Position]:
        return self._positions("closed")

    def close_position(self, pos_id: int, ts: datetime, exit_price: float, proceeds: float, reason: str) -> None:
        with self._db:
            self._db.execute(
                "UPDATE positions SET status = 'closed', closed_at = ?, exit_price = ?, proceeds = ?, close_reason = ? "
                "WHERE id = ?",
                (_iso(ts), exit_price, proceeds, reason, pos_id),
            )

    def _positions(self, status: str) -> list[Position]:
        rows = self._db.execute("SELECT * FROM positions WHERE status = ? ORDER BY id", (status,)).fetchall()
        return [
            Position(
                market_id=r["market_id"], slug=r["slug"], question=r["question"], category=r["category"],
                outcome_index=r["outcome_index"], outcome=r["outcome"], token_id=r["token_id"],
                shares=r["shares"], avg_price=r["avg_price"], fee_paid=r["fee_paid"],
                entry_fair_prob=r["entry_fair_prob"], exit_condition=r["exit_condition"],
                resolution_source=r["resolution_source"], end_date=_dt(r["end_date"]),
                opened_at=_dt(r["opened_at"]), id=r["id"], status=r["status"], closed_at=_dt(r["closed_at"]),
                exit_price=r["exit_price"], proceeds=r["proceeds"], close_reason=r["close_reason"],
                event_id=r["event_id"],
            )
            for r in rows
        ]

    # --- trades -------------------------------------------------------------
    def add_trade(self, t: TradeRecord) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO trades (ts, position_id, market_id, action, shares, price, fee, cash_delta, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_iso(t.ts), t.position_id, t.market_id, t.action, t.shares, t.price, t.fee, t.cash_delta, t.reason),
            )

    def trades(self, limit: int | None = None) -> list[TradeRecord]:
        sql = "SELECT * FROM trades ORDER BY id DESC"
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return [
            TradeRecord(_dt(r["ts"]), r["position_id"], r["market_id"], r["action"], r["shares"], r["price"],
                        r["fee"], r["cash_delta"], r["reason"])
            for r in self._db.execute(sql, params).fetchall()
        ]

    # --- analyses -----------------------------------------------------------
    def save_analysis(self, a: Analysis) -> None:
        payload = json.dumps({
            "fair_prob": a.fair_prob,
            "official_resolution_source": a.official_resolution_source,
            "confidence": a.confidence,
            "evidence": [[e.claim, e.url, e.published] for e in a.evidence],
            "exit_condition": a.exit_condition,
            "reasoning": a.reasoning,
        })
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO analyses (market_id, created_at, backend, payload) VALUES (?, ?, ?, ?)",
                (a.market_id, _iso(a.created_at), a.backend, payload),
            )
            self._db.execute("DELETE FROM analysis_failures WHERE market_id = ?", (a.market_id,))

    def get_analysis(self, market_id: str) -> Analysis | None:
        row = self._db.execute("SELECT * FROM analyses WHERE market_id = ?", (market_id,)).fetchone()
        if row is None:
            return None
        p = json.loads(row["payload"])
        return Analysis(
            market_id=market_id,
            fair_prob=p["fair_prob"],
            official_resolution_source=p["official_resolution_source"],
            confidence=p["confidence"],
            evidence=tuple(Evidence(*e) for e in p["evidence"]),
            exit_condition=p["exit_condition"],
            reasoning=p["reasoning"],
            created_at=_dt(row["created_at"]),
            backend=row["backend"],
        )

    def analyses_since(self, cutoff: datetime) -> set[str]:
        rows = self._db.execute("SELECT market_id FROM analyses WHERE created_at >= ?", (_iso(cutoff),)).fetchall()
        return {r["market_id"] for r in rows}

    def record_analysis_failure(self, market_id: str, ts: datetime, error: str) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO analysis_failures (market_id, failed_at, error) VALUES (?, ?, ?)",
                (market_id, _iso(ts), error[:500]),
            )

    def failed_since(self, cutoff: datetime) -> set[str]:
        rows = self._db.execute(
            "SELECT market_id FROM analysis_failures WHERE failed_at >= ?", (_iso(cutoff),)
        ).fetchall()
        return {r["market_id"] for r in rows}

    # --- entry blocks -------------------------------------------------------
    def block_entry(self, market_id: str, until: datetime, reason: str) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO entry_blocks (market_id, until, reason) VALUES (?, ?, ?)",
                (market_id, _iso(until), reason),
            )

    def blocked_ids(self, now: datetime) -> set[str]:
        rows = self._db.execute("SELECT market_id FROM entry_blocks WHERE until > ?", (_iso(now),)).fetchall()
        return {r["market_id"] for r in rows}

"""The memecoin agent's own database (state/memes.db): separate cash, peak and history from the Polymarket desk."""
from __future__ import annotations

import json
from datetime import datetime

from desk.memes.models import MemeFill, MemePosition
from desk.store import BASE_SCHEMA, Ledger, _dt, _iso

# Token amounts are stored as TEXT: raw balances can exceed SQLite's 64-bit integers.
MEMES_SCHEMA = BASE_SCHEMA + """
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL,
    decimals INTEGER NOT NULL, amount_raw TEXT NOT NULL, cost_usd REAL NOT NULL, entry_price_usd REAL NOT NULL,
    peak_price_usd REAL NOT NULL, entry_liquidity_usd REAL NOT NULL, opened_at TEXT NOT NULL,
    take_profit_done INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'open', closed_at TEXT,
    proceeds_usd REAL NOT NULL DEFAULT 0, close_reason TEXT, last_price_usd REAL, x_url TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, position_id INTEGER NOT NULL, mint TEXT NOT NULL,
    symbol TEXT NOT NULL, side TEXT NOT NULL, amount_raw TEXT NOT NULL, usd REAL NOT NULL, fee_usd REAL NOT NULL,
    cash_delta REAL NOT NULL, price_usd REAL NOT NULL, reason TEXT NOT NULL, signature TEXT
);
CREATE TABLE IF NOT EXISTS vets (mint TEXT PRIMARY KEY, ts TEXT NOT NULL, verdict TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, mint TEXT NOT NULL, symbol TEXT NOT NULL,
    price_usd REAL, liquidity_usd REAL, market_cap_usd REAL, decision TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
);
"""


class MemeStore(Ledger):
    SCHEMA = MEMES_SCHEMA
    DECISION_FIELDS = ("mint", "symbol", "price_usd", "liquidity_usd", "market_cap_usd", "decision", "detail")

    def _migrate(self) -> None:
        columns = {r["name"] for r in self._db.execute("PRAGMA table_info(positions)").fetchall()}
        if "x_url" not in columns:  # databases created before the X exit
            with self._db:
                self._db.execute("ALTER TABLE positions ADD COLUMN x_url TEXT")

    def _set_cash(self, value: float) -> None:
        """Inside a caller's transaction: cash moves together with the trade that caused it."""
        self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('cash', ?)", (repr(value),))

    def _add_trade(self, ts: datetime, pos: MemePosition, fill: MemeFill, cash_delta: float, price: float,
                   reason: str) -> None:
        self._db.execute(
            "INSERT INTO trades (ts, position_id, mint, symbol, side, amount_raw, usd, fee_usd, cash_delta, price_usd, "
            "reason, signature) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_iso(ts), pos.id, pos.mint, pos.symbol, fill.side, str(fill.amount_raw), fill.usd, fill.fee_usd,
             cash_delta, price, reason[:200], fill.signature),
        )

    # --- positions ----------------------------------------------------------
    def open_position(self, pos: MemePosition, fill: MemeFill, ts: datetime, reason: str) -> int:
        """Insert the position, its BUY trade and the cash debit in one transaction."""
        cost = fill.usd + fill.fee_usd
        with self._db:
            cur = self._db.execute(
                "INSERT INTO positions (mint, symbol, name, decimals, amount_raw, cost_usd, entry_price_usd, "
                "peak_price_usd, entry_liquidity_usd, opened_at, x_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pos.mint, pos.symbol, pos.name, pos.decimals, str(pos.amount_raw), pos.cost_usd,
                 pos.entry_price_usd, pos.peak_price_usd, pos.entry_liquidity_usd, _iso(pos.opened_at), pos.x_url),
            )
            pos.id = int(cur.lastrowid)
            self._add_trade(ts, pos, fill, -cost, pos.entry_price_usd, reason)
            self._set_cash(self.cash() - cost)
        return pos.id

    def reduce_position(self, pos: MemePosition, fill: MemeFill, ts: datetime, reason: str, *, price_usd: float) -> None:
        """A SELL: credit cash, shrink the position and close it when nothing is left, atomically."""
        received = fill.usd - fill.fee_usd
        left = max(pos.amount_raw - fill.amount_raw, 0)
        closing = left == 0
        with self._db:
            self._add_trade(ts, pos, fill, received, price_usd, reason)
            self._set_cash(self.cash() + received)
            self._db.execute(
                "UPDATE positions SET amount_raw = ?, proceeds_usd = proceeds_usd + ?, "
                "take_profit_done = take_profit_done OR ?, status = ?, closed_at = ?, close_reason = ? WHERE id = ?",
                (str(left), received, reason == "take_profit", "closed" if closing else "open",
                 _iso(ts) if closing else None, reason if closing else None, pos.id),
            )

    def write_off(self, pos: MemePosition, ts: datetime, reason: str) -> None:
        """Close a position whose tokens are gone (rugged, or no longer in the wallet) without any cash."""
        with self._db:
            self._db.execute(
                "UPDATE positions SET amount_raw = '0', status = 'closed', closed_at = ?, close_reason = ? WHERE id = ?",
                (_iso(ts), reason, pos.id),
            )

    def mark_price(self, pos_id: int, price: float) -> None:
        with self._db:
            self._db.execute(
                "UPDATE positions SET last_price_usd = ?, peak_price_usd = MAX(peak_price_usd, ?) WHERE id = ?",
                (price, price, pos_id),
            )

    def open_positions(self) -> list[MemePosition]:
        return self._positions("open")

    def closed_positions(self) -> list[MemePosition]:
        return self._positions("closed")

    def _positions(self, status: str) -> list[MemePosition]:
        rows = self._db.execute("SELECT * FROM positions WHERE status = ? ORDER BY id", (status,)).fetchall()
        return [
            MemePosition(
                mint=r["mint"], symbol=r["symbol"], name=r["name"], decimals=r["decimals"],
                amount_raw=int(r["amount_raw"]), cost_usd=r["cost_usd"], entry_price_usd=r["entry_price_usd"],
                peak_price_usd=r["peak_price_usd"], entry_liquidity_usd=r["entry_liquidity_usd"],
                opened_at=_dt(r["opened_at"]), take_profit_done=bool(r["take_profit_done"]), id=r["id"],
                status=r["status"], closed_at=_dt(r["closed_at"]), proceeds_usd=r["proceeds_usd"],
                close_reason=r["close_reason"], last_price_usd=r["last_price_usd"],
                x_url=r["x_url"] if "x_url" in r.keys() else None,  # read-only views never migrate
            )
            for r in rows
        ]

    def trades(self, limit: int | None = None) -> list[dict]:
        sql, params = "SELECT * FROM trades ORDER BY id DESC", ()
        if limit is not None:
            sql, params = sql + " LIMIT ?", (limit,)
        return [
            {**dict(r), "ts": _dt(r["ts"]), "amount_raw": int(r["amount_raw"])}
            for r in self._db.execute(sql, params).fetchall()
        ]

    # --- vetter verdicts ----------------------------------------------------
    def save_vet(self, mint: str, ts: datetime, verdict: str, payload: dict) -> None:
        with self._db:
            self._db.execute("INSERT OR REPLACE INTO vets (mint, ts, verdict, payload) VALUES (?, ?, ?, ?)",
                             (mint, _iso(ts), verdict, json.dumps(payload)))

    def get_vet(self, mint: str) -> dict | None:
        row = self._db.execute("SELECT * FROM vets WHERE mint = ?", (mint,)).fetchone()
        if row is None:
            return None
        return {"ts": _dt(row["ts"]), "verdict": row["verdict"], **json.loads(row["payload"])}

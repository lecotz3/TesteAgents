"""One pass of the 10-minute loop: settle, mark, drawdown, exits, scan, analyse, enter."""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Protocol

import httpx

from desk.analyst.base import Analyst, AnalystError
from desk.clob import simulate_sell
from desk.config import Settings
from desk.decide import PREFILTER_SLACK_PP, EntryPlan, Skip, evaluate_entry, quick_edge_pp
from desk.executor.base import ExecutionError, Executor
from desk.exits import exit_reason, settlement_price
from desk.journal import Journal, format_entry, format_exit, format_note
from desk.models import Analysis, Market, OrderBook, Position, TradeRecord
from desk.notify import Notifier
from desk.screener import pick_for_analysis, screen
from desk.store import Store

log = logging.getLogger("desk")

MAX_ANALYST_FAILURES_PER_CYCLE = 3


class MarketSource(Protocol):
    def active_markets(self, limit_total: int) -> list[Market]: ...

    def market_by_id(self, market_id: str) -> Market | None: ...


class BookSource(Protocol):
    def get_book(self, token_id: str) -> OrderBook: ...


@dataclass
class Deps:
    settings: Settings
    store: Store
    markets: MarketSource
    books: BookSource
    analyst: Analyst
    executor: Executor
    journal: Journal
    notifier: Notifier
    now: Callable[[], datetime]


@dataclass
class CycleReport:
    equity: float = 0.0
    scanned: int = 0
    candidates: int = 0
    analyzed: int = 0
    entries: int = 0
    exits: int = 0
    settled: int = 0
    halted: bool = False
    skips: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        skips = ", ".join(f"{k}={v}" for k, v in sorted(self.skips.items())) or "-"
        return (
            f"equity=${self.equity:.2f} varridos={self.scanned} candidatos={self.candidates} "
            f"analisados={self.analyzed} entradas={self.entries} saidas={self.exits} liquidados={self.settled} "
            f"parado={'sim' if self.halted else 'nao'} pulos[{skips}] erros={len(self.errors)}"
        )


def run_cycle(deps: Deps) -> CycleReport:
    return _Cycle(deps).run()


class _Cycle:
    def __init__(self, deps: Deps):
        self.d = deps
        self.s = deps.settings
        self.store = deps.store
        self.now = deps.now()
        self.report = CycleReport()
        self.analyst_failures = 0
        self.books: dict[str, OrderBook] = {}
        self.held_markets: dict[str, Market] = {}

    def run(self) -> CycleReport:
        self.settle_resolved()
        self.mark_to_market()
        self.check_drawdown()
        self.manage_exits()
        if not self.store.is_halted():
            self.scan_and_enter()
        self.mark_to_market()
        self.report.halted = self.store.is_halted()
        for error in self.report.errors:
            log.warning(error)
        return self.report

    # --- helpers --------------------------------------------------------------
    def error(self, message: str) -> None:
        self.report.errors.append(message)

    def book(self, token_id: str) -> OrderBook:
        if token_id not in self.books:
            self.books[token_id] = self.d.books.get_book(token_id)
        return self.books[token_id]

    def analyst_available(self) -> bool:
        return self.analyst_failures < MAX_ANALYST_FAILURES_PER_CYCLE

    def analyze(self, market: Market) -> Analysis | None:
        try:
            analysis = self.d.analyst.estimate(market, self.now)
        except AnalystError as exc:
            self.analyst_failures += 1
            self.store.record_analysis_failure(market.id, self.now, str(exc))
            self.error(f"analista falhou em {market.slug}: {exc}")
            return None
        self.analyst_failures = 0
        self.store.save_analysis(analysis)
        self.report.analyzed += 1
        return analysis

    def close(self, pos: Position, *, price: float, proceeds: float, fee: float, shares: float, action: str, reason: str) -> None:
        assert pos.id is not None
        self.store.adjust_cash(proceeds)
        self.store.close_position(pos.id, self.now, price, proceeds, reason)
        self.store.add_trade(TradeRecord(self.now, pos.id, pos.market_id, action, shares, price, fee, proceeds, reason))
        self.d.journal.append(
            format_exit(pos=pos, price=price, proceeds=proceeds, reason=reason, mode=self.d.executor.mode, now=self.now)
        )

    # --- 1. settlement --------------------------------------------------------
    def settle_resolved(self) -> None:
        for pos in self.store.open_positions():
            try:
                market = self.d.markets.market_by_id(pos.market_id)
            except httpx.HTTPError as exc:
                self.error(f"gamma falhou para {pos.slug}: {type(exc).__name__}")
                continue
            if market is None:
                continue
            self.held_markets[pos.market_id] = market
            price = settlement_price(pos, market)
            if price is None:
                continue
            self.close(pos, price=price, proceeds=pos.shares * price, fee=0.0, shares=pos.shares,
                       action="SETTLE", reason="settled")
            self.report.settled += 1
            if self.s.mode == "live":
                self.d.notifier.send(
                    f"Mercado resolvido: {pos.question} ({pos.outcome}). Resgate as cotas vencedoras no Polymarket."
                )

    # --- 2. mark to market ----------------------------------------------------
    def mark_to_market(self) -> None:
        cash = self.store.cash()
        value = 0.0
        for pos in self.store.open_positions():
            try:
                bid = self.book(pos.token_id).best_bid
            except httpx.HTTPError as exc:
                self.error(f"livro indisponível para {pos.slug}: {type(exc).__name__}")
                bid = pos.avg_price  # neutral fallback: an outage must not look like a loss
            value += pos.shares * (bid if bid is not None else 0.0)
        equity = cash + value
        self.store.record_equity(self.now, equity, cash, value)
        self.report.equity = equity

    # --- 3. drawdown ----------------------------------------------------------
    def check_drawdown(self) -> None:
        if self.store.is_halted():
            return
        peak = self.store.peak()
        equity = self.report.equity
        if peak <= 0:
            return
        drawdown = (peak - equity) / peak
        if drawdown < self.s.risk.drawdown_halt:
            return
        self.store.set_halted(True)
        summary = self.trade_summary()
        try:
            text = self.d.analyst.postmortem(summary)
        except AnalystError as exc:
            text = f"(post-mortem indisponível: {exc})"
        self.d.journal.append(format_note(
            f"POST-MORTEM: drawdown de {drawdown:.0%} a partir do pico",
            f"{text}\n\nResumo dos trades:\n{summary}",
            self.now,
        ))
        self.d.notifier.send(
            f"Desk PARADO: drawdown {drawdown:.0%} (pico ${peak:.2f}, equity ${equity:.2f}). "
            "Leia o post-mortem em trades.txt e rode 'python -m desk resume' para retomar."
        )

    def trade_summary(self) -> str:
        lines = []
        for p in self.store.closed_positions():
            pnl = (p.proceeds or 0.0) - p.cost
            closed = p.closed_at.strftime("%Y-%m-%d") if p.closed_at else "?"
            lines.append(
                f"{closed} | {p.category} | {p.question} | {p.outcome} | entrada {p.avg_price:.3f} | "
                f"saída {(p.exit_price or 0.0):.3f} | PnL ${pnl:+.2f} | {p.close_reason}"
            )
        for p in self.store.open_positions():
            lines.append(f"ABERTA | {p.category} | {p.question} | {p.outcome} | entrada {p.avg_price:.3f} | cotas {p.shares:.2f}")
        return "\n".join(lines) or "(nenhum trade)"

    # --- 4. exits -------------------------------------------------------------
    def refresh_held_analysis(self, pos: Position, market: Market | None) -> Analysis | None:
        analysis = self.store.get_analysis(pos.market_id)
        max_age = timedelta(minutes=self.s.analysis.held_reanalysis_minutes)
        stale = analysis is None or self.now - analysis.created_at > max_age
        if stale and market is not None and self.analyst_available():
            analysis = self.analyze(market) or analysis
        return analysis

    def manage_exits(self) -> None:
        for pos in self.store.open_positions():
            market = self.held_markets.get(pos.market_id)
            analysis = self.refresh_held_analysis(pos, market)
            try:
                book = self.book(pos.token_id)
            except httpx.HTTPError as exc:
                self.error(f"livro indisponível para {pos.slug}: {type(exc).__name__}")
                continue
            reason = exit_reason(pos, book, analysis, self.now, self.s.exits)
            if reason is None:
                continue
            if market is None:
                self.error(f"saída de {pos.slug} adiada: mercado indisponível na Gamma")
                continue
            sim = simulate_sell(book, pos.shares)
            if not sim.complete:
                self.error(f"saída de {pos.slug} adiada: liquidez insuficiente ({reason})")
                continue
            try:
                fill = self.d.executor.sell(market, pos.token_id, pos.shares, sim.limit_price, book)
            except ExecutionError as exc:
                self.error(f"venda falhou em {pos.slug}: {exc}")
                self.d.notifier.send(f"Venda falhou ({reason}) em {pos.question}: {exc}")
                continue
            self.close(pos, price=fill.avg_price, proceeds=fill.notional - fill.fee, fee=fill.fee,
                       shares=fill.shares, action="SELL", reason=reason)
            self.books.pop(pos.token_id, None)
            self.report.exits += 1

    # --- 5-7. scan, analyse, enter ---------------------------------------------
    def scan_and_enter(self) -> None:
        open_positions = self.store.open_positions()
        if len(open_positions) >= self.s.risk.max_open_positions:
            return
        try:
            markets = self.d.markets.active_markets(self.s.scan.max_markets_scanned)
        except httpx.HTTPError as exc:
            self.error(f"varredura falhou: {type(exc).__name__}")
            return
        self.report.scanned = len(markets)
        candidates = screen(markets, self.now, self.s.scan, {p.market_id for p in open_positions})
        candidates.sort(key=lambda m: m.volume_24h, reverse=True)
        self.report.candidates = len(candidates)

        a = self.s.analysis
        fresh_ids = self.store.analyses_since(self.now - timedelta(hours=a.analysis_ttl_hours))
        cooldown_ids = self.store.failed_since(self.now - timedelta(minutes=a.analyst_failure_cooldown_minutes))
        for market in pick_for_analysis(candidates, fresh_ids, cooldown_ids, a.analyses_per_cycle):
            if not self.analyst_available():
                break
            if self.analyze(market) is not None:
                fresh_ids.add(market.id)

        for market in candidates:
            if len(self.store.open_positions()) >= self.s.risk.max_open_positions:
                break
            if market.id not in fresh_ids:
                continue
            analysis = self.store.get_analysis(market.id)
            if analysis is None:
                continue
            if quick_edge_pp(market, analysis) <= self.s.risk.min_edge_pp - PREFILTER_SLACK_PP:
                self.report.skips["prefilter_no_edge"] += 1
                continue
            try:
                books = (self.book(market.token_ids[0]), self.book(market.token_ids[1]))
            except httpx.HTTPError as exc:
                self.error(f"livro indisponível para {market.slug}: {type(exc).__name__}")
                continue
            plan = evaluate_entry(
                market=market, analysis=analysis, books=books, equity=self.report.equity,
                cash=self.store.cash(), open_count=len(self.store.open_positions()),
                risk=self.s.risk, min_confidence=a.min_confidence,
            )
            if isinstance(plan, Skip):
                self.report.skips[plan.reason] += 1
                continue
            self.enter(market, analysis, plan, books[plan.outcome_index])

    def enter(self, market: Market, analysis: Analysis, plan: EntryPlan, book: OrderBook) -> None:
        try:
            fill = self.d.executor.buy(market, plan.token_id, plan.shares, plan.limit_price, book)
        except ExecutionError as exc:
            self.error(f"compra falhou em {market.slug}: {exc}")
            if self.d.executor.mode == "live":
                self.d.notifier.send(f"Compra falhou em {market.question}: {exc}")
            return
        cost = fill.notional + fill.fee
        self.store.adjust_cash(-cost)
        pos = Position(
            market_id=market.id, slug=market.slug, question=market.question, category=market.category,
            outcome_index=plan.outcome_index, outcome=market.outcomes[plan.outcome_index], token_id=plan.token_id,
            shares=fill.shares, avg_price=fill.avg_price, fee_paid=fill.fee, entry_fair_prob=plan.q,
            exit_condition=analysis.exit_condition, resolution_source=analysis.official_resolution_source or "",
            end_date=market.trade_deadline, opened_at=self.now,
        )
        pos.id = self.store.add_position(pos)
        self.store.add_trade(TradeRecord(
            self.now, pos.id, market.id, "BUY", fill.shares, fill.avg_price, fill.fee, -cost,
            f"vantagem {plan.edge_pp:.1f}pp",
        ))
        self.d.journal.append(format_entry(
            pos=pos, analysis=analysis, fill=fill, edge_pp=plan.edge_pp, mode=self.d.executor.mode, now=self.now
        ))
        self.books.pop(plan.token_id, None)
        self.report.entries += 1

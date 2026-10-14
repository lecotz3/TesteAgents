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
from desk.economy import BROKE_USD, shutdown_line
from desk.executor.base import ExecutionError, ExecutionUncertain, Executor
from desk.exits import exit_reason, settlement_price
from desk.journal import Journal, format_entry, format_exit, format_note
from desk.models import Analysis, Market, OrderBook, Position, TradeRecord
from desk.notify import Notifier
from desk.screener import pick_for_analysis, screen
from desk.store import Store

log = logging.getLogger("desk")

MAX_ANALYST_FAILURES_PER_CYCLE = 3
FETCH_ERRORS = (httpx.HTTPError, ValueError)  # network failures and 200s with a body we cannot parse
UNCERTAIN_ORDER_BLOCK_HOURS = 24  # until the user checks the wallet
DEAD_ANALYST_ALERT_CYCLES = 3  # cycles in a row where every analyst call failed


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

    def as_dict(self) -> dict:
        return {
            "equity": self.equity, "scanned": self.scanned, "candidates": self.candidates, "analyzed": self.analyzed,
            "entries": self.entries, "exits": self.exits, "settled": self.settled, "halted": self.halted,
            "skips": dict(self.skips), "errors": list(self.errors),
        }

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
        self.analyst_calls = 0
        self.cap_reached = False
        self.decisions: list[dict] = []
        self.books: dict[str, OrderBook] = {}
        self.held_markets: dict[str, Market] = {}

    def run(self) -> CycleReport:
        self.settle_resolved()
        self.pay_rent()
        self.mark_to_market()
        self.check_drawdown()
        self.manage_exits()
        if not self.store.is_halted():
            self.scan_and_enter()
        self.mark_to_market()
        self.track_analyst_health()
        self.report.halted = self.store.is_halted()
        for error in self.report.errors:
            log.warning(error)
        self.store.record_cycle(self.now, self.report.as_dict(), self.decisions)
        return self.report

    # --- helpers --------------------------------------------------------------
    def error(self, message: str) -> None:
        self.report.errors.append(message)

    def decide(self, market: Market, analysis: Analysis, decision: str, detail: str = "") -> None:
        """Remember what the desk concluded about a market this cycle (shown on the dashboard)."""
        self.decisions.append({
            "market_id": market.id, "slug": market.slug, "question": market.question,
            "price": market.outcome_prices[0], "fair_prob": analysis.fair_prob,
            "edge_pp": round(quick_edge_pp(market, analysis), 1), "confidence": analysis.confidence,
            "decision": decision, "detail": detail,
        })

    def book(self, token_id: str) -> OrderBook:
        if token_id not in self.books:
            self.books[token_id] = self.d.books.get_book(token_id)
        return self.books[token_id]

    def pay_rent(self) -> None:
        """One day of rent (the subscription, pro rata) the first time the desk runs each UTC day."""
        today = self.now.date().isoformat()
        if self.store.get_meta("rent_day") == today:
            return
        self.store.set_meta("rent_day", today)
        if self.store.is_halted():
            return  # a switched-off desk does not pay rent
        if self.s.daily_rent > 0:
            self.store.charge(self.now, "rent", self.s.daily_rent, f"aluguel {today}")

    def within_budget(self) -> bool:
        cost = self.s.economy.cost_per_analysis_usd
        if cost <= 0:
            return True
        budget = self.s.economy.max_daily_burn_pct * max(self.report.equity, 0.0)
        return self.store.costs_since(self.now - timedelta(hours=24)) + cost <= budget + 1e-9

    def analyst_available(self) -> bool:
        if self.analyst_failures >= MAX_ANALYST_FAILURES_PER_CYCLE or self.cap_reached:
            return False
        if self.store.is_halted():
            return False  # switched off: it stops spending; exits run on the last analysis
        if not self.within_budget():
            self.cap_reached = True
            self.report.skips["daily_budget"] += 1
            return False
        used = self.store.analyst_calls_since(self.now - timedelta(hours=24))
        if used >= self.s.analysis.max_analyses_per_day:
            self.cap_reached = True
            self.report.skips["daily_analysis_cap"] += 1
            return False
        return True

    def track_analyst_health(self) -> None:
        if self.analyst_calls == 0:
            return
        dead = 0 if self.report.analyzed else int(self.store.get_meta("analyst_dead_cycles") or 0) + 1
        self.store.set_meta("analyst_dead_cycles", str(dead))
        if dead == DEAD_ANALYST_ALERT_CYCLES:
            self.d.notifier.send(
                f"O analista falhou em todas as chamadas nos últimos {dead} ciclos (limite do plano, login ou "
                "timeout?). O desk segue com saídas, mas sem análises novas. Veja state/desk.log."
            )

    def analyze(self, market: Market) -> Analysis | None:
        self.analyst_calls += 1
        self.store.record_analyst_call(self.now)
        if self.s.economy.cost_per_analysis_usd > 0:
            self.store.charge(self.now, "analysis", self.s.economy.cost_per_analysis_usd, market.slug)
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
            except FETCH_ERRORS as exc:
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
            except FETCH_ERRORS as exc:
                self.error(f"livro indisponível para {pos.slug}: {type(exc).__name__}")
                bid = pos.avg_price  # neutral fallback: an outage must not look like a loss
            if bid is None:
                bid = pos.avg_price  # an empty bid side is missing data, not a price of zero
            value += pos.shares * bid
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
        line = shutdown_line(peak, self.s.risk.drawdown_halt)
        if equity > line:
            return
        self.store.set_halted(True)
        summary = self.trade_summary()
        if self.s.economy.cost_per_analysis_usd > 0:
            self.store.charge(self.now, "postmortem", self.s.economy.cost_per_analysis_usd, "post-mortem")
        try:
            text = self.d.analyst.postmortem(summary)
        except AnalystError as exc:
            text = f"(post-mortem indisponível: {exc})"
        self.d.journal.append(format_note(
            f"POST-MORTEM: drawdown de {drawdown:.0%} a partir do pico",
            f"{text}\n\nResumo dos trades:\n{summary}",
            self.now,
        ))
        why = "o dinheiro acabou" if line <= BROKE_USD else f"a banca caiu {drawdown:.0%} desde o pico"
        self.d.notifier.send(
            f"Desk DESLIGADO: não fez dinheiro, {why} (pico ${peak:.2f}, "
            f"equity ${equity:.2f}; custos de operação até aqui ${self.store.total_costs():.2f}). "
            "Ele parou de operar e de gastar. Leia o post-mortem em trades.txt; para religar: python -m desk resume"
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
            except FETCH_ERRORS as exc:
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
            self.store.block_entry(pos.market_id, self.now + timedelta(hours=self.s.exits.reentry_cooldown_hours),
                                   f"saiu ({reason})")
            self.books.pop(pos.token_id, None)
            self.report.exits += 1

    # --- 5-7. scan, analyse, enter ---------------------------------------------
    def scan_and_enter(self) -> None:
        open_positions = self.store.open_positions()
        if len(open_positions) >= self.s.risk.max_open_positions:
            return
        try:
            markets = self.d.markets.active_markets(self.s.scan.max_markets_scanned)
        except FETCH_ERRORS as exc:
            if "getaddrinfo" in str(exc):  # the network's DNS refuses Polymarket's addresses
                self.error("varredura falhou: o DNS desta rede não resolve o Polymarket (bloqueio no Brasil; não contorne)")
            else:
                self.error(f"varredura falhou: {type(exc).__name__}")
            return
        self.report.scanned = len(markets)
        excluded = {p.market_id for p in open_positions} | self.store.blocked_ids(self.now)
        held_events = {p.event_id for p in open_positions if p.event_id}  # one view per event, not one per market
        candidates = screen(markets, self.now, self.s.scan, excluded, held_events)
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
            if market.event_id is not None and market.event_id in held_events:
                self.report.skips["event_held"] += 1
                continue
            analysis = self.store.get_analysis(market.id)
            if analysis is None:
                continue
            if quick_edge_pp(market, analysis) <= self.s.risk.min_edge_pp - PREFILTER_SLACK_PP:
                self.report.skips["prefilter_no_edge"] += 1
                self.decide(market, analysis, "prefilter_no_edge")
                continue
            if self.now - analysis.created_at > timedelta(minutes=a.entry_analysis_max_age_minutes):
                # Never buy on a view older than the news: redo it, it is rare and worth the call.
                analysis = self.analyze(market) if self.analyst_available() else None
                if analysis is None:
                    self.report.skips["stale_analysis"] += 1
                    continue
                if quick_edge_pp(market, analysis) <= self.s.risk.min_edge_pp - PREFILTER_SLACK_PP:
                    self.report.skips["prefilter_no_edge"] += 1
                    self.decide(market, analysis, "prefilter_no_edge", "reanalisado antes de comprar")
                    continue
            try:
                books = (self.book(market.token_ids[0]), self.book(market.token_ids[1]))
            except FETCH_ERRORS as exc:
                self.error(f"livro indisponível para {market.slug}: {type(exc).__name__}")
                self.decide(market, analysis, "book_unavailable")
                continue
            plan = evaluate_entry(
                market=market, analysis=analysis, books=books, equity=self.report.equity,
                cash=self.store.cash(), open_count=len(self.store.open_positions()),
                risk=self.s.risk, min_confidence=a.min_confidence,
            )
            if isinstance(plan, Skip):
                self.report.skips[plan.reason] += 1
                self.decide(market, analysis, plan.reason)
                continue
            if self.enter(market, analysis, plan, books[plan.outcome_index]):
                self.decide(market, analysis, "entered",
                            f"{market.outcomes[plan.outcome_index]}: {plan.shares:.2f} cotas, vantagem {plan.edge_pp:.1f}pp")
                if market.event_id is not None:
                    held_events.add(market.event_id)
            else:
                self.decide(market, analysis, "buy_failed")

    def enter(self, market: Market, analysis: Analysis, plan: EntryPlan, book: OrderBook) -> bool:
        try:
            fill = self.d.executor.buy(market, plan.token_id, plan.shares, plan.limit_price, book)
        except ExecutionError as exc:
            self.error(f"compra falhou em {market.slug}: {exc}")
            if isinstance(exc, ExecutionUncertain):
                self.store.block_entry(market.id, self.now + timedelta(hours=UNCERTAIN_ORDER_BLOCK_HOURS),
                                       "ordem incerta")
            if self.d.executor.mode == "live":
                self.d.notifier.send(f"Compra falhou em {market.question}: {exc}")
            return False
        cost = fill.notional + fill.fee
        self.store.adjust_cash(-cost)
        pos = Position(
            market_id=market.id, slug=market.slug, question=market.question, category=market.category,
            outcome_index=plan.outcome_index, outcome=market.outcomes[plan.outcome_index], token_id=plan.token_id,
            shares=fill.shares, avg_price=fill.avg_price, fee_paid=fill.fee, entry_fair_prob=plan.q,
            exit_condition=analysis.exit_condition, resolution_source=analysis.official_resolution_source or "",
            end_date=market.trade_deadline, opened_at=self.now, event_id=market.event_id,
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
        return True

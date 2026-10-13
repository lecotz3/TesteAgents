"""One pass of the memecoin loop: mark, drawdown, exits, then discover → filter → safety → quote → vet → buy."""
from __future__ import annotations

import json
import logging
import math
from collections import Counter
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Callable, Protocol

from desk.analyst.base import AnalystError
from desk.config import MemesSettings
from desk.executor.base import ExecutionError, ExecutionUncertain
from desk.journal import Journal, format_note
from desk.memes.executor import MemeExecutor, PriceMoved, usdc_raw
from desk.memes.models import USDC_MINT, MemeFill, MemePosition, Pair, Quote, Safety
from desk.memes.rules import exit_decision, market_rejection, position_size_usd, quote_rejection, safety_rejection
from desk.memes.sources import FETCH_ERRORS, TokenInfo
from desk.memes.store import MemeStore
from desk.memes.vetter import Vet
from desk.memes.x import XSignal, XSource, XUnavailable, describe_x, narrative_strength, parse_x_link, x_rejection
from desk.notify import Notifier

log = logging.getLogger("desk")

UNCERTAIN_BLOCK_HOURS = 24  # until the user checks the wallet
PENDING_BUY = "pending_buy:"  # meta key prefix of buys whose answer was lost, by mint
PENDING_BUY_MINUTES = 10  # a lost buy that has not shown up in the wallet by then never landed
GAP_ALERT_MINUTES = 15  # longer than this without any run means the PC slept or was off
UNCERTAIN_SELL = "uncertain_sell:"  # meta key prefix of sells whose answer was lost, by position id
DEAD_VETTER_ALERT_CYCLES = 3
EXIT_LABELS = {
    "stop_loss": "stop-loss", "take_profit": "realização de lucro", "trailing_stop": "stop móvel",
    "max_hold": "tempo máximo", "liquidity_drained": "liquidez sumindo",
    "x_link_dead": "o post/perfil do X do token foi apagado",
}


class DexSource(Protocol):
    def discover(self) -> list[str]: ...

    def pairs(self, mints: list[str]) -> dict[str, Pair]: ...


class SafetySource(Protocol):
    def report(self, mint: str, main_pool: str | None = None) -> Safety: ...


class QuoteSource(Protocol):
    def quote(self, input_mint: str, output_mint: str, amount: int) -> Quote | None: ...


class TokenSource(Protocol):
    def discover(self, categories: list[str], interval: str, limit: int = 100) -> list[str]: ...

    def info(self, mints: list[str]) -> dict[str, TokenInfo]: ...


class VetSource(Protocol):
    def vet(self, pair: Pair, safety: Safety, now: datetime, x: XSignal | None = None) -> Vet: ...


@dataclass
class MemeDeps:
    settings: MemesSettings
    store: MemeStore
    dex: DexSource
    rugcheck: SafetySource
    jupiter: QuoteSource
    vetter: VetSource | None
    executor: MemeExecutor
    journal: Journal
    notifier: Notifier
    now: Callable[[], datetime]
    x: XSource | None = None
    tokens: TokenSource | None = None
    # Only one run sells at a time: the full cycle and the 1-minute exits-only run share this guard.
    exit_guard: Callable[[], AbstractContextManager[bool]] = lambda: nullcontext(True)


@dataclass
class MemeCycleReport:
    equity: float = 0.0
    discovered: int = 0
    candidates: int = 0
    x_checked: int = 0
    checked: int = 0
    vetted: int = 0
    approved: int = 0
    entries: int = 0
    exits: int = 0
    halted: bool = False
    skips: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "equity": self.equity, "discovered": self.discovered, "candidates": self.candidates,
            "x_checked": self.x_checked, "checked": self.checked, "vetted": self.vetted, "approved": self.approved, "entries": self.entries,
            "exits": self.exits, "halted": self.halted, "skips": dict(self.skips), "errors": list(self.errors),
        }

    def summary(self) -> str:
        skips = ", ".join(f"{k}={v}" for k, v in sorted(self.skips.items())) or "-"
        return (
            f"equity=${self.equity:.2f} descobertos={self.discovered} candidatos={self.candidates} "
            f"x={self.x_checked} checados={self.checked} vetados={self.vetted} aprovados={self.approved} compras={self.entries} "
            f"vendas={self.exits} parado={'sim' if self.halted else 'nao'} pulos[{skips}] erros={len(self.errors)}"
        )


def safety_detail(x: Safety) -> str:
    risks = f" | riscos: {', '.join(x.danger_risks)}" if x.danger_risks else ""
    return (f"LP travada {x.lp_locked_pct:.0f}% | top10 {x.top10_pct:.0f}% | insiders {x.insider_pct:.0f}% | "
            f"criador {x.creator_pct:.0f}% | taxa {x.transfer_fee_pct:g}%{risks}")


def fomo_link(mint: str) -> str:
    return f"https://fomo.family/tokens/solana/{mint}"


def _usd(x: float) -> str:
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(x) >= size:
            return f"${x / size:.1f}{suffix}"
    return f"${x:.2f}"


def run_meme_cycle(deps: MemeDeps) -> MemeCycleReport:
    return _MemeCycle(deps).run()


def run_meme_exits(deps: MemeDeps) -> MemeCycleReport:
    """The fast loop: prices, drawdown and exit rules only. Never discovers, never buys, never calls Claude."""
    return _MemeCycle(deps).run_exits()


class _MemeCycle:
    def __init__(self, deps: MemeDeps):
        self.d = deps
        self.s = deps.settings
        self.store = deps.store
        self.now = deps.now()
        self.report = MemeCycleReport()
        self.decisions: list[dict] = []
        self.prices: dict[str, float] = {}
        self.held_pairs: dict[str, Pair] = {}
        self.vet_calls = 0
        self.vet_failures = 0
        self.mode = deps.executor.mode

    def run(self) -> MemeCycleReport:
        self.check_gap()
        self.pay_rent()
        self.mark_held()
        self.check_drawdown()
        self.manage_exits()
        self.check_fee_reserve()
        self.reclaim_rent()
        if not self.store.is_halted() and not self.bad_day():
            self.scan_and_enter()
        self.record_equity()
        self.track_vetter_health()
        self.report.halted = self.store.is_halted()
        for error in self.report.errors:
            log.warning("memes: %s", error)
        self.store.record_cycle(self.now, self.report.as_dict(), self.decisions)
        return self.report

    def run_exits(self) -> MemeCycleReport:
        self.check_gap()
        self.mark_held()
        self.check_drawdown()
        self.manage_exits()
        self.record_equity()
        self.report.halted = self.store.is_halted()
        for error in self.report.errors:
            log.warning("memes (saídas): %s", error)
        return self.report

    # --- helpers --------------------------------------------------------------
    def error(self, message: str) -> None:
        self.report.errors.append(message)

    def decide(self, pair: Pair, decision: str, detail: str = "") -> None:
        self.decisions.append({
            "mint": pair.mint, "symbol": pair.symbol, "price_usd": pair.price_usd,
            "liquidity_usd": pair.liquidity_usd, "market_cap_usd": pair.market_cap_usd,
            "decision": decision, "detail": detail[:300],
        })

    def reject(self, pair: Pair, reason: str, detail: str = "", hours: float | None = None) -> None:
        self.report.skips[reason] += 1
        self.decide(pair, reason, detail)
        hours = self.s.rejection_block_hours if hours is None else hours
        if hours > 0:
            self.store.block_entry(pair.mint, self.now + timedelta(hours=hours), reason)

    def notify_trade(self, text: str) -> None:
        if self.mode == "live" or self.s.notify_paper_trades:
            self.d.notifier.send(f"[{self.mode.upper()}] {text}")

    def notify_once(self, key: str, text: str) -> None:
        if self.store.get_meta(key) is None:
            self.store.set_meta(key, self.now.isoformat())
            self.d.notifier.send(text)

    def check_gap(self) -> None:
        """The PC slept or was off with positions open: nobody watched the stops. Say so once, now that it is back."""
        last, held = self.store.last_equity_ts(), self.store.open_positions()
        if last is None or not held or self.now - last < timedelta(minutes=GAP_ALERT_MINUTES):
            return
        minutes = int((self.now - last).total_seconds() // 60)
        log.warning("memes: %d min sem rodar com %d posição(ões) aberta(s)", minutes, len(held))
        self.notify_once(
            f"gap:{last.isoformat()}",
            f"[{self.mode.upper()}] Memecoins: o agente ficou {minutes} min sem rodar (PC dormindo ou desligado?) com "
            f"posições abertas ({', '.join(p.symbol for p in held)}): stops e saídas não foram vigiados nesse tempo. "
            "Ele voltou e confere as saídas agora. Com dinheiro real, deixe o PC sem suspensão (docs/PENDENCIAS.md).",
        )

    def pay_rent(self) -> None:
        today = self.now.date().isoformat()
        if self.store.get_meta("rent_day") == today:
            return
        self.store.set_meta("rent_day", today)
        if self.s.daily_rent_usd > 0 and not self.store.is_halted():
            self.store.charge(self.now, "rent", self.s.daily_rent_usd, f"aluguel {today}")

    # --- 1. mark --------------------------------------------------------------
    def mark_held(self) -> None:
        held = self.store.open_positions()
        if held:
            try:
                self.held_pairs = self.d.dex.pairs([p.mint for p in held])
            except FETCH_ERRORS as exc:
                self.error(f"preços indisponíveis: {type(exc).__name__}")
        for pos in held:
            pair = self.held_pairs.get(pos.mint)
            if pair is not None:
                self.prices[pos.mint] = pair.price_usd
                self.store.mark_price(pos.id, pair.price_usd)
        self.report.equity = self.equity()

    def equity(self) -> float:
        value = sum(
            p.value_at(self.prices.get(p.mint) or p.last_price_usd or p.entry_price_usd)
            for p in self.store.open_positions()
        )
        return self.store.cash() + value

    def record_equity(self) -> None:
        cash = self.store.cash()
        equity = self.equity()
        self.store.record_equity(self.now, equity, cash, equity - cash)
        self.report.equity = equity

    # --- 2. drawdown ----------------------------------------------------------
    def check_drawdown(self) -> None:
        if self.store.is_halted():
            return
        peak, equity = self.store.peak(), self.report.equity
        if peak <= 0 or (peak - equity) / peak < self.s.drawdown_halt:
            return
        drawdown = (peak - equity) / peak
        self.store.set_halted(True)
        self.d.journal.append(format_note(
            f"POST-MORTEM MEMECOINS: queda de {drawdown:.0%} desde o pico", self.trade_summary(), self.now
        ))
        self.d.notifier.send(
            f"Agente de memecoins DESLIGADO: não se pagou. A banca caiu {drawdown:.0%} desde o pico "
            f"(pico ${peak:.2f}, equity ${equity:.2f}; custos ${self.store.total_costs():.2f}). Ele parou de comprar "
            "e de gastar, mas segue vendendo pelas regras de saída. Para religar: python -m desk memes-resume"
        )

    def check_fee_reserve(self) -> None:
        """Live sells need SOL for fees: without it Jupiter only offers gasless swaps, which refuse small sizes."""
        if self.mode != "live" or not self.store.open_positions():
            return
        try:
            problem = self.d.executor.funds_problem(0.0)
        except FETCH_ERRORS:
            return
        if problem == "wallet_sol_short":
            self.notify_once(
                f"sol_low:{self.now.date()}",
                f"[LIVE] Pouco SOL na carteira das memecoins (mínimo {self.s.min_sol_balance:g} SOL) com posições abertas. "
                "Sem SOL para as taxas, a Jupiter só oferece trocas gasless, que recusam valores pequenos e cobram taxa "
                "alta: as vendas podem falhar. Mande SOL para a carteira.",
            )

    def reclaim_rent(self) -> None:
        """Hourly in live mode: close the token accounts that sells left empty, getting their SOL rent back."""
        if self.mode != "live":
            return
        last = self.store.get_meta("rent_sweep")
        if last and self.now - datetime.fromisoformat(last) < timedelta(hours=1):
            return
        self.store.set_meta("rent_sweep", self.now.isoformat())
        try:
            closed = self.d.executor.reclaim_rent()
        except (*FETCH_ERRORS, ExecutionError) as exc:
            self.error(f"não consegui recuperar o aluguel das contas vazias: {type(exc).__name__}: {exc}")
            return
        if closed:
            log.info("memes: %d conta(s) de token vazia(s) fechada(s); o aluguel volta em SOL para a carteira", closed)

    def bad_day(self) -> bool:
        """Lost daily_loss_pause_pct of the bankroll in 24h: stop buying for now, keep managing exits."""
        if self.s.daily_loss_pause_pct <= 0:
            return False
        cutoff = self.now - timedelta(hours=24)
        before = self.store.equity_before(cutoff)
        if before is None:
            window = self.store.equity_history(cutoff)
            before = window[0][1] if window else None
        if not before or before <= 0:
            return False
        drop = (before - self.report.equity) / before
        if drop * 100 < self.s.daily_loss_pause_pct:
            return False
        self.report.skips["daily_loss_pause"] += 1
        self.notify_once(f"daily_loss_pause:{self.now.date()}",
                         f"Memecoins: a banca caiu {drop:.0%} em 24h (de ${before:.2f} para ${self.report.equity:.2f}). "
                         "O agente pausou as compras até a queda das últimas 24h ficar abaixo de "
                         f"{self.s.daily_loss_pause_pct:.0f}%; as vendas pelas regras continuam.")
        return True

    def trade_summary(self) -> str:
        lines = [
            f"{(p.closed_at or self.now):%Y-%m-%d} | {p.symbol} | custo ${p.cost_usd:.2f} | recebido ${p.proceeds_usd:.2f} "
            f"| PnL ${p.proceeds_usd - p.cost_usd:+.2f} | {p.close_reason}"
            for p in self.store.closed_positions()
        ]
        lines += [f"ABERTA | {p.symbol} | custo ${p.cost_usd:.2f}" for p in self.store.open_positions()]
        return "\n".join(lines) or "(nenhum trade)"

    # --- 3. exits -------------------------------------------------------------
    def manage_exits(self) -> None:
        with self.d.exit_guard() as free:
            if not free:
                log.info("memes: outra execução está vendendo agora; saídas ficam para ela")
                return
            self.adopt_uncertain_buys()
            waiting = self.settle_uncertain_sells()
            self._manage_exits(skip=waiting)

    def adopt_uncertain_buys(self) -> None:
        """A buy whose answer was lost may still have landed: tokens found in the wallet become a position with exits."""
        for key, raw in self.store.meta_with_prefix(PENDING_BUY).items():
            mint = key[len(PENDING_BUY):]
            try:
                pending = json.loads(raw)
                at = datetime.fromisoformat(pending["at"])
            except (ValueError, KeyError, TypeError):
                self.store.delete_meta(key)
                continue
            try:
                held = self.d.executor.wallet_balance(mint)
            except FETCH_ERRORS as exc:
                self.error(f"saldo indisponível para a compra incerta de {pending['symbol']}: {type(exc).__name__}")
                continue
            if held > 0:
                self.adopt(mint, pending, at, held)
                self.store.delete_meta(key)
            elif self.now - at >= timedelta(minutes=PENDING_BUY_MINUTES):
                self.store.delete_meta(key)
                self.d.notifier.send(
                    f"[{self.mode.upper()}] A compra incerta de {pending['symbol']} não entrou: nenhum token chegou à "
                    f"carteira em {PENDING_BUY_MINUTES} min, então o USDC não foi gasto. {fomo_link(mint)}"
                )

    def adopt(self, mint: str, pending: dict, at: datetime, held: int) -> None:
        usd, fee = float(pending["usd"]), self.s.network_fee_usd
        tokens = held / 10 ** int(pending["decimals"])
        entry = usd / tokens
        pos = MemePosition(
            mint=mint, symbol=pending["symbol"], name=pending["name"], decimals=int(pending["decimals"]), amount_raw=held,
            cost_usd=usd + fee, entry_price_usd=entry, peak_price_usd=entry,
            entry_liquidity_usd=float(pending["liquidity_usd"]), opened_at=at, x_url=pending.get("x_url"),
        )
        self.store.open_position(pos, MemeFill("BUY", held, usd, fee), self.now, "compra incerta confirmada na carteira")
        self.d.journal.append(format_note(
            f"COMPRA MEMECOIN [{self.mode.upper()}] (compra incerta confirmada na carteira)",
            f"Token: {pos.name} ({pos.symbol}) {mint}\n"
            f"A resposta da Jupiter se perdeu em {at:%Y-%m-%d %H:%M} UTC, mas {tokens:,.2f} tokens apareceram na carteira.\n"
            f"Custo estimado: ${usd:.2f} + rede ${fee:.3f} (o pedido enviado) | preço estimado ${entry:.8g}\n"
            f"A partir de agora valem as regras de saída: stop -{self.s.stop_loss_pct:.0f}%, máximo "
            f"{self.s.max_hold_hours:.0f}h desde a compra.",
            self.now,
        ))
        self.d.notifier.send(
            f"[{self.mode.upper()}] A compra incerta de {pos.symbol} entrou: {tokens:,.2f} tokens na carteira. O agente "
            f"assumiu a posição (custo estimado ${usd:.2f}) e aplica o stop e as demais saídas. {fomo_link(mint)}"
        )

    def settle_uncertain_sells(self) -> set[int]:
        """Tokens that left the wallet after a lost sell answer were sold: credit them at the attempt's price.
        Tokens still there after a few minutes were not, and the exit rules sell again. Returns positions to leave alone."""
        waiting: set[int] = set()
        open_by_id = {p.id: p for p in self.store.open_positions()}
        for key, raw in self.store.meta_with_prefix(UNCERTAIN_SELL).items():
            pos_id = key[len(UNCERTAIN_SELL):]
            pos = open_by_id.get(int(pos_id)) if pos_id.isdigit() else None
            try:
                pending = json.loads(raw)
                at, price, amount = datetime.fromisoformat(pending["at"]), float(pending["price"]), int(pending["amount"])
            except (ValueError, KeyError, TypeError):
                pos = None
            if pos is None:
                self.store.delete_meta(key)
                continue
            try:
                available = self.d.executor.sellable(pos.mint, pos.amount_raw)
            except FETCH_ERRORS as exc:
                self.error(f"saldo indisponível para a venda incerta de {pos.symbol}: {type(exc).__name__}")
                waiting.add(pos.id)
                continue
            sold = min(pos.amount_raw - available, amount)
            if sold > 0:
                self.store.delete_meta(key)
                self.settle_sold(pos, pending.get("reason") or "uncertain_sell", sold, price)
            elif self.now - at >= timedelta(minutes=PENDING_BUY_MINUTES):
                self.store.delete_meta(key)  # it never landed: the exit rules try again right away
            else:
                waiting.add(pos.id)
        return waiting

    def settle_sold(self, pos: MemePosition, reason: str, sold: int, price: float) -> None:
        fill = MemeFill("SELL", sold, sold / 10 ** pos.decimals * price, self.s.network_fee_usd)
        self.store.reduce_position(pos, fill, self.now, reason, price_usd=price)
        closed = sold >= pos.amount_raw
        if closed:
            self.store.block_entry(pos.mint, self.now + timedelta(hours=self.s.reentry_cooldown_hours), f"saiu ({reason})")
        self.report.exits += 1
        self.write_sell(pos, fill, reason, price, closed)
        self.d.notifier.send(
            f"[{self.mode.upper()}] A venda incerta de {pos.symbol} provavelmente entrou: os tokens saíram da carteira. "
            f"Registrei ${fill.usd - fill.fee_usd:.2f}, estimado pelo preço da tentativa; confira o USDC na carteira."
        )

    def _manage_exits(self, skip: set[int] = frozenset()) -> None:
        for pos in self.store.open_positions():
            if pos.id in skip:
                continue  # a sell whose answer was lost may still land: no second order yet
            decision = exit_decision(pos, self.held_pairs.get(pos.mint), self.now, self.s)
            if (decision is None or decision[1] < 1) and self.x_link_gone(pos):
                decision = ("x_link_dead", 1.0)  # the dev deleted the post or the account: a classic rug tell
            if decision is not None:
                self.sell(pos, *decision)

    def x_link_gone(self, pos: MemePosition) -> bool:
        if not self.s.x_enabled or self.d.x is None or not pos.x_url:
            return False
        try:
            x = XSignal.collect(parse_x_link(pos.x_url), self.d.x)
        except XUnavailable as exc:
            self.error(f"X indisponível para {pos.symbol}: {exc}")
            return False  # an outage is never a reason to sell
        return x.kind is not None and not x.found

    def sell(self, pos: MemePosition, reason: str, fraction: float) -> None:
        try:
            available = self.d.executor.sellable(pos.mint, pos.amount_raw)
        except FETCH_ERRORS as exc:
            self.error(f"saldo indisponível para {pos.symbol}: {type(exc).__name__}")
            return
        zero_key = f"zero_balance:{pos.id}"
        if available <= 0 and self.store.get_meta(zero_key) is None:
            self.store.set_meta(zero_key, self.now.isoformat())  # a lagging RPC can read zero once: confirm next cycle
            self.error(f"{pos.symbol}: carteira com saldo zero; confirmo no próximo ciclo antes de dar baixa")
            return
        if available > 0 and self.store.get_meta(zero_key) is not None:
            self.store.delete_meta(zero_key)  # the tokens are there after all: forget the earlier reading
        if available <= 0:
            self.store.write_off(pos, self.now, "missing_in_wallet")
            self.d.notifier.send(
                f"{pos.symbol}: os tokens não estão mais na carteira (venda incerta anterior ou movidos à mão). "
                f"Posição encerrada sem valor no registro; confira a carteira. {fomo_link(pos.mint)}"
            )
            return
        whole = fraction >= 1
        amount = available if whole else min(available, int(pos.amount_raw * fraction))
        try:
            fill = self.d.executor.sell(pos.mint, amount)
        except ExecutionError as exc:
            if isinstance(exc, ExecutionUncertain):
                self.store.set_meta(UNCERTAIN_SELL + str(pos.id), json.dumps({
                    "at": self.now.isoformat(), "reason": reason, "amount": amount,
                    "price": self.prices.get(pos.mint) or pos.last_price_usd or pos.entry_price_usd,
                }))
            self.error(f"venda de {pos.symbol} falhou ({reason}): {exc}")
            extra = (f" A resposta se perdeu: ele confere a carteira nos próximos {PENDING_BUY_MINUTES} min antes de tentar "
                     "de novo." if isinstance(exc, ExecutionUncertain) else "")
            self.notify_once(f"sell_failed:{pos.id}",
                             f"[{self.mode.upper()}] O agente não conseguiu vender {pos.symbol} ({EXIT_LABELS.get(reason, reason)}): "
                             f"{exc}. Ele tenta de novo a cada ciclo.{extra} {fomo_link(pos.mint)}")
            return
        if whole:
            pos.amount_raw = fill.amount_raw  # whatever the wallet still had: the position ends here
        tokens = fill.amount_raw / 10 ** pos.decimals
        price = fill.usd / tokens if tokens else 0.0
        self.store.reduce_position(pos, fill, self.now, reason, price_usd=price)
        closed = whole or fill.amount_raw >= pos.amount_raw
        if closed:
            self.store.block_entry(pos.mint, self.now + timedelta(hours=self.s.reentry_cooldown_hours), f"saiu ({reason})")
        self.report.exits += 1
        self.write_sell(pos, fill, reason, price, closed)

    def write_sell(self, pos: MemePosition, fill: MemeFill, reason: str, price: float, closed: bool) -> None:
        received = fill.usd - fill.fee_usd
        change = price / pos.entry_price_usd - 1 if pos.entry_price_usd else 0.0
        total = pos.proceeds_usd + received
        result = f"resultado final ${total - pos.cost_usd:+.2f}" if closed else "posição segue aberta com o resto"
        label = EXIT_LABELS.get(reason, reason)
        self.d.journal.append(format_note(
            f"VENDA MEMECOIN [{self.mode.upper()}]",
            f"Token: {pos.name} ({pos.symbol}) {pos.mint}\n"
            f"Motivo: {label} | vendido {'tudo' if closed else 'parte'}\n"
            f"Recebido: ${received:.2f} (rede ${fill.fee_usd:.3f}) | preço ${price:.8g} "
            f"(entrada ${pos.entry_price_usd:.8g}, {change:+.0%})\n"
            f"Custo da posição ${pos.cost_usd:.2f}, recebido até aqui ${total:.2f}: {result}"
            + (f"\nAssinatura: {fill.signature}" if fill.signature else ""),
            self.now,
        ))
        self.notify_trade(f"VENDEU {pos.symbol} ({label}): ${received:.2f} a {change:+.0%} da entrada; {result}.")

    # --- 4. discover and enter -------------------------------------------------
    def slots_left(self) -> bool:
        return not self.s.auto_trade or len(self.store.open_positions()) < self.s.max_positions

    def scan_and_enter(self) -> None:
        if not self.slots_left():
            return
        discovered = self.discover()
        excluded = {p.mint for p in self.store.open_positions()} | self.store.blocked_ids(self.now)
        mints = [m for m in discovered if m not in excluded]
        self.report.discovered = len(mints)
        if not mints:
            return
        try:
            pairs = self.d.dex.pairs(mints)
        except FETCH_ERRORS as exc:
            self.error(f"DexScreener falhou: {type(exc).__name__}")
            return
        candidates = []
        for pair in self.enrich(pairs).values():
            reason = market_rejection(pair, self.now, self.s)
            if reason:
                self.report.skips[reason] += 1  # cheap check, not worth a row on the dashboard
            else:
                candidates.append(pair)
        candidates.sort(key=lambda p: p.volume_h1_usd, reverse=True)
        self.report.candidates = len(candidates)
        for pair, x in self.rank_by_x(candidates)[: self.s.max_candidates_checked]:
            if not self.slots_left() or not self.consider(pair, x):
                break

    def discover(self) -> list[str]:
        """DexScreener's new profiles and boosts plus Jupiter's most traded / organic / trending lists."""
        found: dict[str, None] = {}
        try:
            found.update(dict.fromkeys(self.d.dex.discover()))
        except FETCH_ERRORS as exc:
            self.error(f"descoberta no DexScreener falhou: {type(exc).__name__}")
        if self.s.jup_discovery and self.d.tokens is not None:
            try:
                found.update(dict.fromkeys(self.d.tokens.discover(self.s.jup_categories, self.s.jup_interval)))
            except FETCH_ERRORS as exc:
                self.error(f"listas da Jupiter falharam: {type(exc).__name__}")
        return list(found)

    def enrich(self, pairs: dict[str, Pair]) -> dict[str, Pair]:
        """Jupiter's organic score, and its X link when DexScreener has none."""
        if self.d.tokens is None or not pairs:
            return pairs
        try:
            info = self.d.tokens.info(list(pairs))
        except FETCH_ERRORS as exc:
            self.error(f"dados da Jupiter indisponíveis: {type(exc).__name__}")
            return pairs
        return {
            mint: replace(pair, organic_score=info[mint].organic_score, x_url=pair.x_url or info[mint].twitter)
            if mint in info else pair
            for mint, pair in pairs.items()
        }

    def rank_by_x(self, candidates: list[Pair]) -> list[tuple[Pair, XSignal | None]]:
        """Check what each token links to on X, drop the empty or dead claims, strongest narrative first."""
        if not self.s.x_enabled or self.d.x is None:
            return [(pair, None) for pair in candidates]
        ranked = []
        for pair in candidates[: self.s.max_candidates_checked * 2]:  # the pool by volume bounds the free calls
            try:
                x = XSignal.collect(parse_x_link(pair.x_url), self.d.x)
            except XUnavailable as exc:
                self.error(f"X indisponível para {pair.symbol}: {exc}")
                self.report.skips["x_unavailable"] += 1  # no block: it is checked again next cycle
                continue
            self.report.x_checked += 1
            reason = x_rejection(x, self.s)
            if reason:
                self.reject(pair, reason, describe_x(x, self.now))
                continue
            ranked.append((pair, x))
        ranked.sort(key=lambda px: narrative_strength(px[1]), reverse=True)
        return ranked

    def consider(self, pair: Pair, x: XSignal | None = None) -> bool:
        """Run the deep checks on one candidate. False means stop looking at more candidates this cycle."""
        self.report.checked += 1
        try:
            safety = self.d.rugcheck.report(pair.mint, pair.pair_address or None)
        except FETCH_ERRORS as exc:
            self.error(f"RugCheck falhou para {pair.symbol}: {type(exc).__name__}")
            self.report.skips["rugcheck_unavailable"] += 1
            return True
        reason = safety_rejection(safety, self.s)
        if reason:
            self.reject(pair, reason, safety_detail(safety))
            return True

        size = position_size_usd(equity=self.report.equity, cash=self.store.cash(), s=self.s)
        if size <= 0:
            if self.s.auto_trade:
                self.report.skips["no_cash"] += 1
                return False
            size = max(self.report.equity * self.s.position_pct, self.s.min_trade_usd)  # alerts: quote a typical size
        checked = self.check_quotes(pair, safety, size)
        if checked is None:
            return True
        quote, approved_out = checked

        vet = None
        if self.s.analyst != "none" and self.d.vetter is not None:
            if self.store.analyst_calls_since(self.now - timedelta(hours=24)) >= self.s.max_vets_per_day:
                self.report.skips["vet_cap"] += 1
                return False  # never buys unvetted when vetting is on
            if self.vet_calls >= self.s.max_vets_per_cycle:
                self.report.skips["vet_cycle_cap"] += 1
                return False  # the rest waits for the next cycle
            vet = self.vet(pair, safety, x)
            if vet is None:
                return True
            if not vet.approves:
                flags = "; ".join(vet.red_flags)
                self.reject(pair, "vetter_avoid", f"{vet.verdict}/{vet.confidence}: {vet.summary} {flags}".strip(),
                            hours=self.s.vet_block_hours)
                return True
        self.report.approved += 1
        alert = self.approval_text(pair, safety, quote, vet, x)

        if not self.s.auto_trade:
            self.d.notifier.send(f"Memecoin aprovada (só alerta, você decide): {alert}")
            self.decide(pair, "alerted", f"ida e volta {quote:.1%}")
            self.store.block_entry(pair.mint, self.now + timedelta(hours=24), "alertado")
            return True
        return self.buy(pair, safety, size, quote, vet, alert, x, approved_out)

    def check_quotes(self, pair: Pair, safety: Safety, size: float) -> tuple[float, int] | None:
        """Round-trip cost of a buy of `size` sold right back and the tokens it buys, or None to skip the token."""
        amount_in = usdc_raw(size)
        try:
            q_buy = self.d.jupiter.quote(USDC_MINT, pair.mint, amount_in)
            q_sell = self.d.jupiter.quote(pair.mint, USDC_MINT, q_buy.out_amount) if q_buy else None
        except FETCH_ERRORS as exc:
            self.error(f"Jupiter falhou para {pair.symbol}: {type(exc).__name__}")
            self.report.skips["quote_unavailable"] += 1
            return None
        loss = 1 - q_sell.out_amount / amount_in if q_buy and q_sell and amount_in else None
        impact = max((q.price_impact_pct for q in (q_buy, q_sell) if q), default=0.0)
        reason = quote_rejection(round_trip_loss=loss, price_impact_pct=impact, s=self.s)
        if reason:
            detail = f"ida e volta {loss:.1%}, impacto {impact:.1f}%" if loss is not None else "sem rota de volta"
            self.reject(pair, reason, detail)
            return None
        tokens = q_buy.out_amount / 10 ** safety.decimals
        quoted_price = size / tokens if tokens else math.inf
        if pair.price_usd <= 0 or abs(math.log(quoted_price / pair.price_usd)) > math.log(1 + self.s.max_price_mismatch_pct / 100):
            self.reject(pair, "price_mismatch", f"cotação ${quoted_price:.8g} vs DexScreener ${pair.price_usd:.8g}")
            return None
        return loss, q_buy.out_amount

    def vet(self, pair: Pair, safety: Safety, x: XSignal | None) -> Vet | None:
        self.vet_calls += 1
        self.report.vetted += 1
        self.store.record_analyst_call(self.now)
        if self.s.cost_per_vet_usd > 0:
            self.store.charge(self.now, "vet", self.s.cost_per_vet_usd, pair.symbol)
        try:
            vet = self.d.vetter.vet(pair, safety, self.now, x)
        except AnalystError as exc:
            self.vet_failures += 1
            self.error(f"Claude falhou ao checar {pair.symbol}: {exc}")
            self.report.skips["vet_error"] += 1
            self.decide(pair, "vet_error", str(exc))
            return None
        self.store.save_vet(pair.mint, self.now, vet.verdict, vet.as_dict())
        return vet

    def track_vetter_health(self) -> None:
        if self.vet_calls == 0:
            return
        dead = int(self.store.get_meta("vet_dead_cycles") or 0) + 1 if self.vet_failures == self.vet_calls else 0
        self.store.set_meta("vet_dead_cycles", str(dead))
        if dead == DEAD_VETTER_ALERT_CYCLES:
            self.d.notifier.send(
                f"O Claude falhou em todas as checagens de memecoins nos últimos {dead} ciclos (limite do plano, "
                "login ou timeout?). O agente não compra sem essa checagem; as vendas seguem. Veja state/desk.log."
            )

    def approval_text(self, pair: Pair, safety: Safety, loss: float, vet: Vet | None, x: XSignal | None) -> str:
        why = f" | Claude: {vet.narrative}" if vet else ""
        why += f" | X: {describe_x(x, self.now)}" if x else ""
        return (
            f"{pair.symbol} ({pair.name}) | mcap {_usd(pair.market_cap_usd)} | liquidez {_usd(pair.liquidity_usd)} | "
            f"1h: volume {_usd(pair.volume_h1_usd)}, {pair.change_h1_pct:+.0f}% | idade {pair.age_minutes(self.now) / 60:.0f}h | "
            f"top10 {safety.top10_pct:.0f}% | ida e volta {loss:.1%}{why} | {fomo_link(pair.mint)} | {pair.url}"
        )

    def buy(self, pair: Pair, safety: Safety, size: float, loss: float, vet: Vet | None, alert: str,
            x: XSignal | None = None, approved_out: int = 0) -> bool:
        try:
            problem = self.d.executor.funds_problem(size)
        except FETCH_ERRORS as exc:
            self.error(f"saldo da carteira indisponível: {type(exc).__name__}")
            return False
        if problem:
            self.report.skips[problem] += 1
            self.decide(pair, problem)
            self.notify_once(f"{problem}:{self.now.date()}",
                             f"Carteira das memecoins sem saldo ({problem}): o agente não consegue comprar. "
                             "Veja docs/PENDENCIAS.md (carteira Solana).")
            return False
        min_out = int(approved_out * (1 - self.s.max_buy_drift_pct / 100))
        try:
            fill = self.d.executor.buy(pair.mint, size, min_out)
        except PriceMoved as exc:  # the price ran (or a hidden fee appeared) while Claude was checking: never chase
            self.reject(pair, "price_moved", str(exc))
            return True
        except ExecutionUncertain as exc:
            self.error(f"compra de {pair.symbol} incerta: {exc}")
            self.store.block_entry(pair.mint, self.now + timedelta(hours=UNCERTAIN_BLOCK_HOURS), "compra incerta")
            self.decide(pair, "buy_uncertain", str(exc))
            self.store.set_meta(PENDING_BUY + pair.mint, json.dumps({
                "symbol": pair.symbol, "name": pair.name, "decimals": safety.decimals, "usd": size,
                "at": self.now.isoformat(), "liquidity_usd": pair.liquidity_usd, "x_url": pair.x_url,
            }))
            self.d.notifier.send(
                f"Compra de {pair.symbol} ficou incerta ({exc}). O agente confere a carteira nos próximos "
                f"{PENDING_BUY_MINUTES} min: se os tokens chegaram, ele assume a posição com stop. {fomo_link(pair.mint)}"
            )
            return True
        except ExecutionError as exc:
            self.error(f"compra de {pair.symbol} falhou: {exc}")
            self.reject(pair, "buy_failed", str(exc))
            return True
        tokens = fill.amount_raw / 10 ** safety.decimals
        if tokens <= 0:
            self.error(f"compra de {pair.symbol} devolveu zero tokens")
            return True
        entry = fill.usd / tokens
        pos = MemePosition(
            mint=pair.mint, symbol=pair.symbol, name=pair.name, decimals=safety.decimals, amount_raw=fill.amount_raw,
            cost_usd=fill.usd + fill.fee_usd, entry_price_usd=entry, peak_price_usd=entry,
            entry_liquidity_usd=pair.liquidity_usd, opened_at=self.now, x_url=pair.x_url,
        )
        self.store.open_position(pos, fill, self.now, f"ida e volta {loss:.1%}")
        self.prices[pair.mint] = pair.price_usd
        self.report.entries += 1
        self.decide(pair, "bought", f"${fill.usd:.2f} a ${entry:.8g}" + (f" | X: {describe_x(x, self.now)}" if x else ""))
        self.d.journal.append(format_note(
            f"COMPRA MEMECOIN [{self.mode.upper()}]",
            f"Token: {pair.name} ({pair.symbol}) {pair.mint}\n"
            f"Gasto: ${fill.usd:.2f} + rede ${fill.fee_usd:.3f} | tokens {tokens:,.2f} | preço pago ${entry:.8g} "
            f"(DexScreener ${pair.price_usd:.8g})\n"
            f"Mercado: mcap {_usd(pair.market_cap_usd)} | liquidez {_usd(pair.liquidity_usd)} | 1h: volume "
            f"{_usd(pair.volume_h1_usd)}, {pair.buys_h1} compras / {pair.sells_h1} vendas, {pair.change_h1_pct:+.0f}%\n"
            f"Segurança: top10 {safety.top10_pct:.0f}% | insiders {safety.insider_pct:.0f}% | LP travada "
            f"{safety.lp_locked_pct:.0f}% | ida e volta {loss:.1%}\n"
            + (f"X: {describe_x(x, self.now)}\n" if x else "")
            + (f"Claude: {vet.verdict} ({vet.confidence}) | {vet.narrative} | {vet.summary}\n" if vet else "")
            + f"Saídas: stop -{self.s.stop_loss_pct:.0f}%, metade em +{self.s.take_profit_pct:.0f}%, stop móvel "
            f"{self.s.trailing_stop_pct:.0f}% do pico, máximo {self.s.max_hold_hours:.0f}h\n"
            f"Links: {fomo_link(pair.mint)} | {pair.url}"
            + (f"\nAssinatura: {fill.signature}" if fill.signature else ""),
            self.now,
        ))
        self.notify_trade(f"COMPROU ${fill.usd:.2f} de {alert}")
        return True

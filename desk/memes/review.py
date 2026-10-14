"""memes-review: did each filter keep the agent out of losers? For every deep-checked decision, replay the real exit
rules on the token's 1-minute candles (GeckoTerminal, free) for the 24h that followed, as if it had been bought."""
from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Protocol

import httpx

from desk.config import MemesSettings
from desk.memes.models import MemePosition, Pair
from desk.memes.rules import exit_decision
from desk.memes.sources import FETCH_ERRORS, PacedApi

GECKO_URL = "https://api.geckoterminal.com/api/v2"
GECKO_INTERVAL = 2.1  # about 30 calls a minute per IP on the free API
ROUND_TRIP_COST = 0.02  # Jupiter's fee, price impact and network fees of a small buy and its sell, flat
MINUTE = timedelta(minutes=1)
CANDLE_LAG = timedelta(minutes=5)  # GeckoTerminal publishes a minute's candle a little after it ends
MAX_PAGES = 5
OPERATIONAL = ("vet_error", "buy_failed", "buy_uncertain")  # the agent's own trouble, not a judgement on the token
# Buys first, then the filters in the order the cycle applies them.
ORDER = (
    "bought", "alerted", "vetter_avoid", "price_moved",
    "no_x_link", "x_link_dead", "x_weak_narrative", "x_no_community",
    "rugged", "mint_authority", "freeze_authority", "danger_risk", "transfer_fee", "lp_unlocked",
    "concentrated_holders", "insiders", "creator_holds",
    "cannot_sell", "round_trip_cost", "price_impact", "price_mismatch",
)
LABELS = {
    "bought": "Comprou", "alerted": "Alerta enviado", "vetter_avoid": "Claude vetou", "price_moved": "Preço correu",
    "no_x_link": "Sem link do X", "x_link_dead": "Post/perfil do X apagado", "x_weak_narrative": "Narrativa fraca no X",
    "x_no_community": "Perfil do X sem seguidores", "rugged": "Golpe (rug)", "mint_authority": "Pode imprimir tokens",
    "freeze_authority": "Pode congelar tokens", "danger_risk": "Risco grave (RugCheck)",
    "transfer_fee": "Taxa de transferência", "lp_unlocked": "Liquidez destravada", "concentrated_holders": "Poucos donos",
    "insiders": "Insiders demais", "creator_holds": "Criador segura muito", "cannot_sell": "Sem rota de venda",
    "round_trip_cost": "Ida e volta cara", "price_impact": "Impacto alto", "price_mismatch": "Preço destoa",
    "stop_loss": "stop-loss", "take_profit": "realização de lucro", "trailing_stop": "stop móvel",
    "max_hold": "tempo máximo", "liquidity_drained": "liquidez sumindo",
}
BUYS = ("bought", "alerted")


class PriceHistory(Protocol):
    def main_pool(self, mint: str) -> str | None: ...

    def closes(self, pool: str, mint: str, start: datetime, end: datetime) -> list[tuple[datetime, float]]: ...


class GeckoTerminal:
    """Pools and 1-minute candles of Solana tokens. Candles come newest first; minutes without trades are missing."""

    def __init__(self, api: PacedApi, page_size: int = 1000):
        self._api = api
        self._page = page_size

    def main_pool(self, mint: str) -> str | None:
        try:
            pools = self._api.get(f"/networks/solana/tokens/{mint}/pools").json().get("data") or []
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None  # GeckoTerminal never saw the token
            raise
        best = max(pools, key=lambda p: _num((p.get("attributes") or {}).get("reserve_in_usd")), default=None)
        return (best.get("attributes") or {}).get("address") if best else None

    def closes(self, pool: str, mint: str, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        """(minute, close) for start <= minute < end, oldest first. `token` prices the mint even when it is the quote."""
        found: dict[int, float] = {}
        before = int(end.timestamp())
        for _ in range(MAX_PAGES):
            rows = self._api.get(f"/networks/solana/pools/{pool}/ohlcv/minute", params={
                "aggregate": 1, "limit": self._page, "currency": "usd", "token": mint, "before_timestamp": before,
            }).json()["data"]["attributes"]["ohlcv_list"]
            for row in rows:
                ts = int(row[0])
                if start.timestamp() <= ts < end.timestamp():
                    found[ts] = float(row[4])
            if len(rows) < self._page or not rows or min(int(r[0]) for r in rows) < start.timestamp():
                break
            before = min(int(r[0]) for r in rows)
        return [(datetime.fromtimestamp(ts, start.tzinfo), price) for ts, price in sorted(found.items())]


def make_gecko(http: httpx.Client) -> GeckoTerminal:
    return GeckoTerminal(PacedApi(http, GECKO_URL, headers={"Accept": "application/json"}, min_interval=GECKO_INTERVAL))


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _pair(price: float, liquidity: float) -> Pair:
    return Pair(mint="", symbol="", name="", pair_address="", dex="", price_usd=price, liquidity_usd=liquidity,
                market_cap_usd=0.0, volume_h1_usd=0.0, buys_h1=0, sells_h1=0, change_h1_pct=0.0, change_h6_pct=0.0,
                created_at=None, has_socials=False, url="")


def simulate(closes: list[tuple[datetime, float]], opened_at: datetime, liquidity: float,
             s: MemesSettings) -> tuple[float, str] | None:
    """Return after costs and the exit that ended it, buying at the first close and checking the real exit rules on
    every later close, as the 1-minute exits loop does. Liquidity stays at entry: candles do not carry it. A window
    that ends without an exit (trading stopped) is sold at its last price by the time limit."""
    if not closes or closes[0][1] <= 0:
        return None
    entry = closes[0][1]
    pos = MemePosition(mint="", symbol="", name="", decimals=0, amount_raw=0, cost_usd=1.0, entry_price_usd=entry,
                       peak_price_usd=entry, entry_liquidity_usd=liquidity, opened_at=opened_at)
    left, proceeds = 1.0, 0.0
    for ts, price in closes[1:]:
        pos.peak_price_usd = max(pos.peak_price_usd, price)
        decision = exit_decision(pos, _pair(price, liquidity), ts + MINUTE, s)
        if decision is None:
            continue
        reason, fraction = decision
        sold = left if fraction >= 1 else left * fraction
        proceeds += sold * price / entry
        left -= sold
        pos.take_profit_done = pos.take_profit_done or reason == "take_profit"
        if left <= 1e-12:
            return proceeds - 1 - ROUND_TRIP_COST, reason
    proceeds += left * closes[-1][1] / entry
    return proceeds - 1 - ROUND_TRIP_COST, "max_hold"


def reviewable(decisions: list[dict]) -> list[dict]:
    """Deep-checked decisions, oldest first, one per (token, decision): a token re-checked later is the same story."""
    seen: set[tuple[str, str]] = set()
    picked = []
    for d in sorted(decisions, key=lambda d: d["ts"]):
        key = (d["mint"], d["decision"])
        if d["decision"] in OPERATIONAL or d["decision"].startswith("wallet_") or key in seen:
            continue
        seen.add(key)
        picked.append(d)
    return picked


@dataclass(frozen=True)
class Outcome:
    mint: str
    symbol: str
    decision: str
    ts: datetime
    status: str  # closed | no_data | open (window still running) | pending (fetch next run) | error
    ret: float | None = None
    exit: str | None = None


def rules_key(s: MemesSettings) -> str:
    """The cache is only valid for the exit rules it was computed with."""
    return json.dumps([s.stop_loss_pct, s.take_profit_pct, s.take_profit_fraction, s.trailing_stop_pct,
                       s.trailing_activation_pct, s.max_hold_hours, s.liquidity_drain_pct, ROUND_TRIP_COST])


def _load_cache(path: Path, rules: str) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data.get("windows", {}) if isinstance(data, dict) and data.get("rules") == rules else {}


def _save_cache(path: Path, rules: str, windows: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"rules": rules, "windows": windows}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _from_cache(raw: dict) -> Outcome:
    return Outcome(**{**raw, "ts": datetime.fromisoformat(raw["ts"])})


def review(decisions: list[dict], prices: PriceHistory, cache_path: Path, s: MemesSettings, now: datetime, *,
           max_fetch: int = 100, progress: Callable[[int, int, str], None] | None = None) -> list[Outcome]:
    """One outcome per reviewable decision. Finished windows are fetched once and kept in `cache_path`."""
    rules = rules_key(s)
    cache = _load_cache(cache_path, rules)
    window = timedelta(hours=s.max_hold_hours)
    picked = reviewable(decisions)
    to_fetch = sum(1 for d in picked if f"{d['mint']}|{d['decision']}" not in cache and now >= d["ts"] + window + CANDLE_LAG)
    outcomes, fetched = [], 0
    for d in picked:
        key = f"{d['mint']}|{d['decision']}"
        base = dict(mint=d["mint"], symbol=d["symbol"], decision=d["decision"], ts=d["ts"])
        if key in cache:
            outcomes.append(_from_cache(cache[key]))
            continue
        if now < d["ts"] + window + CANDLE_LAG:
            outcomes.append(Outcome(**base, status="open"))
            continue
        if fetched >= max_fetch:
            outcomes.append(Outcome(**base, status="pending"))
            continue
        fetched += 1
        if progress:
            progress(fetched, min(to_fetch, max_fetch), d["symbol"])
        try:
            pool = prices.main_pool(d["mint"])
            closes = prices.closes(pool, d["mint"], d["ts"] - MINUTE, d["ts"] + window) if pool else []
        except FETCH_ERRORS:
            outcomes.append(Outcome(**base, status="error"))
            continue
        result = simulate(closes, d["ts"], d.get("liquidity_usd") or 0.0, s)
        outcome = Outcome(**base, status="closed", ret=result[0], exit=result[1]) if result else Outcome(**base, status="no_data")
        cache[key] = {**asdict(outcome), "ts": outcome.ts.isoformat()}
        _save_cache(cache_path, rules, cache)  # a long run interrupted halfway keeps what it already fetched
        outcomes.append(outcome)
    return outcomes


@dataclass
class ReasonStats:
    decision: str
    count: int = 0
    wins: int = 0
    average: float | None = None
    worst: float | None = None
    best: float | None = None
    open: int = 0
    pending: int = 0
    no_data: int = 0
    errors: int = 0

    @property
    def label(self) -> str:
        return LABELS.get(self.decision, self.decision)


def summarize(outcomes: list[Outcome]) -> list[ReasonStats]:
    groups: dict[str, list[Outcome]] = defaultdict(list)
    for o in outcomes:
        groups[o.decision].append(o)
    rows = []
    for name in sorted(groups, key=lambda n: (ORDER.index(n) if n in ORDER else len(ORDER), n)):
        group = groups[name]
        returns = [o.ret for o in group if o.status == "closed" and o.ret is not None]
        status = [o.status for o in group]
        rows.append(ReasonStats(
            decision=name, count=len(returns), wins=sum(1 for r in returns if r > 0),
            average=sum(returns) / len(returns) if returns else None,
            worst=min(returns, default=None), best=max(returns, default=None),
            open=status.count("open"), pending=status.count("pending"), no_data=status.count("no_data"),
            errors=status.count("error"),
        ))
    return rows


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:+.0%}"


def format_report(outcomes: list[Outcome], real_closed: list[MemePosition], s: MemesSettings) -> str:
    lines = [
        "Revisão das decisões das memecoins: e se cada token checado a fundo tivesse sido comprado?",
        f"Simula as regras de saída reais (stop -{s.stop_loss_pct:.0f}%, metade em +{s.take_profit_pct:.0f}%, stop móvel "
        f"{s.trailing_stop_pct:.0f}%, máximo {s.max_hold_hours:.0f}h) sobre os candles de 1 min da GeckoTerminal, "
        f"descontando {ROUND_TRIP_COST:.0%} de custo de ida e volta.",
        "",
        f"{'Decisão':<28}{'Qtd':>5}{'Média':>8}{'Ganhou':>8}{'Pior':>7}{'Melhor':>8}  Ainda sem resultado",
    ]
    for r in summarize(outcomes):
        waiting = ", ".join(f"{n} {what}" for n, what in ((r.open, "abertas (<24h)"), (r.pending, "na fila"),
                                                           (r.no_data, "sem dados"), (r.errors, "com erro")) if n)
        lines.append(f"{r.label[:27]:<28}{r.count:>5}{_pct(r.average):>8}{f'{r.wins}/{r.count}':>8}"
                     f"{_pct(r.worst):>7}{_pct(r.best):>8}  {waiting}")
    if not outcomes:
        lines.append("(nenhuma decisão checada a fundo ainda)")
    lines += [
        "",
        "Como ler: em 'Comprou', média positiva é bom. Num filtro que rejeitou, média negativa quer dizer que ele evitou "
        "perdedores; média positiva, que ele barrou tokens que teriam dado lucro. Poucos casos ainda não provam nada.",
        "Limites: a liquidez fica fixa na da entrada e a saída pelo post do X apagado não é simulada.",
    ]
    if any(o.status == "pending" for o in outcomes):
        lines.append("Há janelas na fila: rode de novo para buscar o resto (a GeckoTerminal aceita ~30 chamadas/min).")
    simulated = {o.mint: o for o in outcomes if o.decision in BUYS and o.status == "closed"}
    checks = [
        f"  - {p.symbol}: real {(p.proceeds_usd - p.cost_usd) / p.cost_usd:+.0%} ({LABELS.get(p.close_reason or '', p.close_reason)}) | "
        f"simulado {simulated[p.mint].ret:+.0%} ({LABELS.get(simulated[p.mint].exit or '', simulated[p.mint].exit)})"
        for p in real_closed if p.mint in simulated and p.cost_usd > 0
    ]
    if checks:
        lines += ["", "Conferência: compras em paper fechadas x simulação (devem ficar próximas):", *checks]
    return "\n".join(lines)

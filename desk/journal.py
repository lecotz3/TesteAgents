"""Human-readable trade log (trades.txt), as the original guide asks for."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from desk.models import Analysis, Fill, Position


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def format_entry(*, pos: Position, analysis: Analysis, fill: Fill, edge_pp: float, mode: str, now: datetime) -> str:
    lines = [
        f"=== ENTRADA [{mode.upper()}] {_ts(now)} ===",
        f"Mercado: {pos.question} ({pos.slug}) | categoria: {pos.category}",
        f"Lado comprado: {pos.outcome}",
        f"Valor justo do lado: {pos.entry_fair_prob:.1%} | Preço médio: {fill.avg_price:.3f} | Vantagem: {edge_pp:.1f}pp",
        f"Tamanho: {fill.shares:.2f} cotas | Custo: ${fill.notional:.2f} + taxa ${fill.fee:.4f}",
        f"Fonte oficial: {analysis.official_resolution_source}",
        f"Condição de saída: {analysis.exit_condition}",
        f"Motivo: {analysis.reasoning}",
    ]
    lines += [f"  - {ev.claim} ({ev.url}, {ev.published})" for ev in analysis.evidence]
    return "\n".join(lines) + "\n\n"


def format_exit(*, pos: Position, price: float, proceeds: float, reason: str, mode: str, now: datetime) -> str:
    pnl = proceeds - pos.cost
    return (
        f"=== SAÍDA [{mode.upper()}] {_ts(now)} ===\n"
        f"Mercado: {pos.question} ({pos.slug}) | lado {pos.outcome}\n"
        f"Motivo: {reason} | Preço: {price:.3f} | Recebido: ${proceeds:.2f} | PnL: ${pnl:+.2f}\n\n"
    )


def format_note(title: str, body: str, now: datetime) -> str:
    return f"=== {title} {_ts(now)} ===\n{body}\n\n"


class Journal:
    def __init__(self, path: Path):
        self.path = path

    def append(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(text)

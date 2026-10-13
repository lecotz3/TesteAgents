"""Entry decision for one analysed market: side, edge net of fees, size, depth and exit liquidity."""
from __future__ import annotations

from dataclasses import dataclass

from desk.clob import exit_slippage, simulate_buy
from desk.config import RiskSettings
from desk.fees import fee_per_share
from desk.models import CONFIDENCE_RANK, Analysis, Market, OrderBook
from desk.sizing import size_position

PREFILTER_SLACK_PP = 3.0


@dataclass(frozen=True)
class EntryPlan:
    outcome_index: int
    token_id: str
    q: float
    shares: float
    limit_price: float
    expected_avg_price: float
    edge_pp: float
    exit_slippage: float


@dataclass(frozen=True)
class Skip:
    reason: str


def quick_edge_pp(market: Market, analysis: Analysis) -> float:
    """Rough edge from Gamma prices; only decides whether order books are worth fetching."""
    return max((analysis.prob_of(i) - market.outcome_prices[i]) * 100 for i in (0, 1))


def actionable_problem(analysis: Analysis, min_confidence: str) -> str | None:
    if CONFIDENCE_RANK.get(analysis.confidence, -1) < CONFIDENCE_RANK[min_confidence]:
        return "low_confidence"
    if not (analysis.official_resolution_source or "").strip():
        return "no_official_source"
    if not analysis.exit_condition.strip():
        return "no_exit_condition"
    return None


def _edge(q: float, price: float, market: Market) -> float:
    return q - price - fee_per_share(price, market.fee_rate, market.fee_exponent)


def evaluate_entry(
    *,
    market: Market,
    analysis: Analysis,
    books: tuple[OrderBook, OrderBook],
    equity: float,
    cash: float,
    open_count: int,
    risk: RiskSettings,
    min_confidence: str,
) -> EntryPlan | Skip:
    problem = actionable_problem(analysis, min_confidence)
    if problem:
        return Skip(problem)

    best: tuple[int, float] | None = None
    for idx in (0, 1):
        ask = books[idx].best_ask
        if ask is None:
            continue
        edge = _edge(analysis.prob_of(idx), ask, market)
        if best is None or edge > best[1]:
            best = (idx, edge)
    if best is None:
        return Skip("empty_book")
    idx, edge = best
    if edge * 100 <= risk.min_edge_pp:
        return Skip("edge_below_min")

    book = books[idx]
    q = analysis.prob_of(idx)
    sizing = dict(
        q=q,
        fee_rate=market.fee_rate,
        fee_exponent=market.fee_exponent,
        equity=equity,
        cash=cash,
        open_count=open_count,
        kelly_mult=risk.kelly_fraction,
        max_position_pct=risk.max_position_pct,
        max_open_positions=risk.max_open_positions,
        min_order_size=market.min_order_size,
    )
    first = size_position(price=book.best_ask, **sizing)
    if first.skip_reason:
        return Skip(first.skip_reason)
    sim = simulate_buy(book, first.shares)
    if not sim.complete:
        return Skip("insufficient_depth")
    avg_edge = _edge(q, sim.avg_price, market)
    if avg_edge * 100 <= risk.min_edge_pp:
        return Skip("edge_below_min_after_depth")
    second = size_position(price=sim.avg_price, **sizing)
    if second.skip_reason:
        return Skip(second.skip_reason)
    shares = min(first.shares, second.shares)
    if shares < first.shares:
        sim = simulate_buy(book, shares)
    slippage = exit_slippage(book, shares)
    if slippage is None or slippage > risk.max_exit_slippage:
        return Skip("exit_slippage")
    return EntryPlan(
        outcome_index=idx,
        token_id=market.token_ids[idx],
        q=q,
        shares=shares,
        limit_price=sim.limit_price,
        expected_avg_price=sim.avg_price,
        edge_pp=avg_edge * 100,
        exit_slippage=slippage,
    )

import pytest

from desk.config import RiskSettings
from desk.decide import EntryPlan, Skip, evaluate_entry, quick_edge_pp
from tests.factories import make_analysis, make_book, make_market

R = RiskSettings()


def books(yes_asks=((0.62, 1000.0),), yes_bids=((0.61, 1000.0),), no_asks=((0.39, 1000.0),), no_bids=((0.38, 1000.0),)):
    return (make_book("111", bids=yes_bids, asks=yes_asks), make_book("222", bids=no_bids, asks=no_asks))


def run(analysis=None, market=None, bks=None, equity=100.0, cash=100.0, open_count=0, min_confidence="medium"):
    return evaluate_entry(
        market=market or make_market(),
        analysis=analysis or make_analysis(fair_prob=0.74),
        books=bks or books(),
        equity=equity,
        cash=cash,
        open_count=open_count,
        risk=R,
        min_confidence=min_confidence,
    )


def test_buys_yes_when_fair_value_is_12pp_above_ask():
    plan = run()
    assert isinstance(plan, EntryPlan)
    assert plan.outcome_index == 0 and plan.token_id == "111"
    assert plan.shares == pytest.approx(9.67)
    assert plan.limit_price == 0.62 and plan.expected_avg_price == pytest.approx(0.62)
    assert plan.edge_pp == pytest.approx(12.0)
    assert plan.exit_slippage == pytest.approx(0.0)


def test_buys_no_when_fair_value_is_far_below_price():
    plan = run(
        analysis=make_analysis(fair_prob=0.20),
        bks=books(yes_asks=((0.40, 1000.0),), yes_bids=((0.39, 1000.0),),
                  no_asks=((0.61, 1000.0),), no_bids=((0.60, 1000.0),)),
    )
    assert isinstance(plan, EntryPlan)
    assert plan.outcome_index == 1 and plan.token_id == "222"
    assert plan.q == pytest.approx(0.80)
    assert plan.edge_pp == pytest.approx(19.0)


def test_edge_of_exactly_8pp_is_not_enough():
    assert run(analysis=make_analysis(fair_prob=0.70)) == Skip("edge_below_min")


def test_fee_can_kill_a_marginal_edge():
    no_fee = run(analysis=make_analysis(fair_prob=0.71))
    with_fee = run(analysis=make_analysis(fair_prob=0.71), market=make_market(fee_rate=0.05))
    assert isinstance(no_fee, EntryPlan)
    assert with_fee == Skip("edge_below_min")


@pytest.mark.parametrize("overrides,reason", [
    ({"confidence": "low"}, "low_confidence"),
    ({"official_resolution_source": None}, "no_official_source"),
    ({"exit_condition": "  "}, "no_exit_condition"),
])
def test_analysis_must_be_actionable(overrides, reason):
    assert run(analysis=make_analysis(**overrides)) == Skip(reason)


def test_low_confidence_allowed_when_configured():
    assert isinstance(run(analysis=make_analysis(confidence="low"), min_confidence="low"), EntryPlan)


def test_thin_exit_side_blocks_entry():
    assert run(bks=books(yes_bids=((0.61, 2.0), (0.50, 1000.0)))) == Skip("exit_slippage")


def test_insufficient_depth_blocks_entry():
    assert run(bks=books(yes_asks=((0.62, 3.0),))) == Skip("insufficient_depth")


def test_edge_rechecked_at_average_fill_price():
    assert run(bks=books(yes_asks=((0.62, 1.0), (0.70, 1000.0)))) == Skip("edge_below_min_after_depth")


def test_empty_books():
    assert run(bks=books(yes_asks=(), no_asks=())) == Skip("empty_book")


def test_sizing_skip_reasons_propagate():
    assert run(equity=50.0, cash=50.0) == Skip("below_min_order")
    assert run(open_count=5) == Skip("max_open_positions")


def test_quick_edge_uses_gamma_prices():
    m = make_market(outcome_prices=(0.62, 0.38))
    assert quick_edge_pp(m, make_analysis(fair_prob=0.74)) == pytest.approx(12.0)
    assert quick_edge_pp(m, make_analysis(fair_prob=0.20)) == pytest.approx(42.0)

import pytest

from desk.models import Fill
from tests.factories import make_analysis, make_book, make_position


def test_analysis_prob_of_each_outcome():
    a = make_analysis(fair_prob=0.74)
    assert a.prob_of(0) == 0.74
    assert a.prob_of(1) == pytest.approx(0.26)


def test_book_best_prices_and_empty_book():
    book = make_book(bids=((0.60, 10.0), (0.61, 5.0)), asks=((0.63, 10.0), (0.62, 5.0)))
    assert book.best_bid == 0.61
    assert book.best_ask == 0.62
    empty = make_book(bids=(), asks=())
    assert empty.best_bid is None and empty.best_ask is None


def test_position_cost_includes_fee_and_fill_notional():
    assert make_position(shares=10, avg_price=0.5, fee_paid=0.1).cost == pytest.approx(5.1)
    assert Fill("111", "BUY", 10, 0.5, 0.1, 0.5).notional == pytest.approx(5.0)

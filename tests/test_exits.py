from datetime import timedelta

from desk.config import ExitSettings
from desk.exits import exit_reason, settlement_price
from tests.factories import NOW, make_analysis, make_book, make_market, make_position

S = ExitSettings()
BOOK = make_book(bids=((0.65, 100.0),), asks=((0.66, 100.0),))


def test_holds_when_nothing_changed():
    assert exit_reason(make_position(), BOOK, make_analysis(fair_prob=0.74), NOW, S) is None


def test_exits_when_resolving_within_one_hour():
    pos = make_position(end_date=NOW + timedelta(minutes=59))
    assert exit_reason(pos, BOOK, make_analysis(), NOW, S) == "resolving_soon"


def test_exits_when_fair_value_moves_more_than_5pp():
    assert exit_reason(make_position(), BOOK, make_analysis(fair_prob=0.68), NOW, S) == "fair_value_changed"
    assert exit_reason(make_position(), BOOK, make_analysis(fair_prob=0.70), NOW, S) is None


def test_exits_when_mispricing_closed():
    converged = make_book(bids=((0.735, 100.0),), asks=((0.74, 100.0),))
    assert exit_reason(make_position(), converged, make_analysis(fair_prob=0.74), NOW, S) == "mispricing_closed"


def test_no_side_position_uses_complement_probability():
    pos = make_position(outcome_index=1, outcome="No", token_id="222", entry_fair_prob=0.80)
    assert exit_reason(pos, BOOK, make_analysis(fair_prob=0.20), NOW, S) is None
    assert exit_reason(pos, BOOK, make_analysis(fair_prob=0.30), NOW, S) == "fair_value_changed"


def test_without_new_analysis_uses_entry_fair_value():
    assert exit_reason(make_position(), BOOK, None, NOW, S) is None


def test_settlement_price():
    pos = make_position()
    assert settlement_price(pos, make_market(closed=True, outcome_prices=(1.0, 0.0))) == 1.0
    assert settlement_price(pos, make_market(closed=True, outcome_prices=(0.0, 1.0))) == 0.0
    assert settlement_price(pos, make_market(closed=True, outcome_prices=(0.5, 0.5))) is None
    assert settlement_price(pos, make_market(closed=False, outcome_prices=(1.0, 0.0))) is None

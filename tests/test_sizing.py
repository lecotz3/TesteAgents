import pytest

from desk.sizing import floor_shares, kelly_fraction, size_position

COMMON = dict(
    fee_rate=0.0, fee_exponent=1.0, open_count=0, kelly_mult=0.5,
    max_position_pct=0.06, max_open_positions=5, min_order_size=5.0,
)


def guide_kelly(p, P):
    b = (1 - P) / P
    return (b * p - (1 - p)) / b


def test_kelly_matches_guide_formula():
    assert kelly_fraction(0.74, 0.62) == pytest.approx(guide_kelly(0.74, 0.62))
    assert kelly_fraction(0.74, 0.62) == pytest.approx(0.3158, abs=1e-4)


def test_guide_example_on_50_dollars_is_below_min_order():
    d = size_position(q=0.74, price=0.62, equity=50.0, cash=50.0, **COMMON)
    assert d.kelly_used == pytest.approx(0.1579, abs=1e-4)
    assert d.shares == pytest.approx(4.83)
    assert d.skip_reason == "below_min_order"


def test_guide_example_on_100_dollars_caps_at_6_percent():
    d = size_position(q=0.74, price=0.62, equity=100.0, cash=100.0, **COMMON)
    assert d.skip_reason is None
    assert d.shares == pytest.approx(9.67)
    assert d.stake == pytest.approx(9.67 * 0.62)
    assert d.stake <= 6.0


def test_half_kelly_used_when_below_cap():
    d = size_position(q=0.66, price=0.62, equity=1000.0, cash=1000.0, **COMMON)
    full = (0.66 - 0.62) / 0.38
    assert d.kelly_used == pytest.approx(full / 2)
    assert d.shares == pytest.approx(floor_shares(full / 2 * 1000 / 0.62))


def test_fees_shrink_kelly():
    no_fee = size_position(q=0.74, price=0.62, equity=100.0, cash=100.0, **COMMON)
    with_fee = size_position(q=0.74, price=0.62, equity=100.0, cash=100.0, **{**COMMON, "fee_rate": 0.05})
    assert with_fee.kelly_full < no_fee.kelly_full


def test_cash_limits_stake():
    d = size_position(q=0.74, price=0.62, equity=100.0, cash=2.0, **COMMON)
    assert d.shares == pytest.approx(3.22)
    assert d.skip_reason == "below_min_order"


def test_no_edge_and_max_positions():
    assert size_position(q=0.60, price=0.62, equity=100.0, cash=100.0, **COMMON).skip_reason == "no_edge"
    full = size_position(q=0.74, price=0.62, equity=100.0, cash=100.0, **{**COMMON, "open_count": 5})
    assert full.skip_reason == "max_open_positions"


def test_floor_shares_rounds_down_to_cents():
    assert floor_shares(4.8387) == 4.83
    assert floor_shares(9.999) == 9.99

import pytest

from desk.fees import fee_for_levels, fee_per_share


def test_fee_matches_official_formula_at_50_cents():
    # docs.polymarket.com/trading/fees: fee = C x feeRate x p x (1 - p)
    assert fee_per_share(0.5, 0.05) == pytest.approx(0.0125)


def test_fee_is_zero_without_rate_or_at_extremes():
    assert fee_per_share(0.62, 0.0) == 0.0
    assert fee_per_share(0.0, 0.05) == 0.0
    assert fee_per_share(1.0, 0.05) == 0.0


def test_exponent_changes_curve_shape():
    assert fee_per_share(0.5, 0.25, exponent=2) == pytest.approx(0.25 * 0.0625)


def test_fee_for_levels_sums_each_level():
    levels = ((0.62, 5.0), (0.63, 5.0))
    expected = 5 * 0.05 * 0.62 * 0.38 + 5 * 0.05 * 0.63 * 0.37
    assert fee_for_levels(levels, 0.05) == pytest.approx(expected)

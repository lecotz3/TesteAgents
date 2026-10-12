from decimal import Decimal

import pytest

from desk.executor.base import ExecutionError
from desk.executor.live import LiveExecutor
from desk.executor.sdk import SdkTrader
from desk.geoblock import GeoStatus
from tests.factories import FakeSdkClient as FakeClient
from tests.factories import SdkAccepted as Accepted
from tests.factories import SdkRejected as Rejected
from tests.factories import make_book, make_market


def allowed():
    return GeoStatus(blocked=False, country="PT", region="")


def test_buy_spends_at_most_the_sized_cost_with_a_price_cap():
    client = FakeClient(Accepted("matched", Decimal("6.2"), Decimal("10")))
    SdkTrader(client).place_fok(token_id="111", side="BUY", price=0.62, size=10.004, neg_risk=False, tick_size=0.01)
    order = client.orders[0]
    assert order["asset_id"] == "111" and order["side"] == "BUY" and order["order_type"] == "FOK"
    assert Decimal(order["amount"]) == Decimal("6.20")  # floor(10.004 * 0.62) to cents
    assert Decimal(order["max_price"]) == Decimal("0.62")
    assert "shares" not in order and "min_price" not in order


def test_sell_sells_floored_shares_with_a_price_floor():
    client = FakeClient(Accepted("matched", Decimal("10"), Decimal("6.1")))
    SdkTrader(client).place_fok(token_id="222", side="SELL", price=0.61, size=10.009, neg_risk=True, tick_size=0.001)
    order = client.orders[0]
    assert order["asset_id"] == "222" and order["side"] == "SELL" and order["order_type"] == "FOK"
    assert Decimal(order["shares"]) == Decimal("10.00")
    assert Decimal(order["min_price"]) == Decimal("0.61")
    assert "amount" not in order and "max_price" not in order


def test_accepted_order_feeds_live_executor_fill():
    client = FakeClient(Accepted("matched", Decimal("6.2"), Decimal("10")))
    fill = LiveExecutor(SdkTrader(client), allowed).buy(make_market(), "111", 10, 0.62, make_book())
    assert fill.shares == pytest.approx(10) and fill.avg_price == pytest.approx(0.62)


def test_rejected_order_becomes_execution_error_with_reason():
    client = FakeClient(Rejected("fok_not_filled", "order couldn't be fully filled"))
    with pytest.raises(ExecutionError, match="fully filled"):
        LiveExecutor(SdkTrader(client), allowed).buy(make_market(), "111", 10, 0.62, make_book())


def test_resting_order_is_not_treated_as_filled():
    client = FakeClient(Accepted("live", Decimal("0"), Decimal("0")))
    with pytest.raises(ExecutionError):
        LiveExecutor(SdkTrader(client), allowed).buy(make_market(), "111", 10, 0.62, make_book())


def test_balance_is_converted_from_base_units():
    assert SdkTrader(FakeClient(balance=123_450_000)).usdc_balance() == pytest.approx(123.45)


def test_account_problems_lists_closed_only_and_missing_approvals():
    assert SdkTrader(FakeClient()).account_problems() == []
    problems = SdkTrader(FakeClient(closed_only=True, approved=False)).account_problems()
    assert len(problems) == 2
    assert any("closed-only" in p for p in problems)
    assert any("aprovações" in p for p in problems)


def test_connect_without_sdk_installed_explains_the_extra(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_polymarket(name, *args, **kwargs):
        if name == "polymarket" or name.startswith("polymarket."):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_polymarket)
    with pytest.raises(ExecutionError, match="uv sync --extra live"):
        SdkTrader.connect("0x" + "1" * 64, None)


def test_adapter_calls_match_the_real_sdk_signatures():
    polymarket = pytest.importorskip("polymarket")
    import inspect

    client = polymarket.SecureClient
    for kwargs in _recorded_orders():
        inspect.signature(client.place_market_order).bind(None, **kwargs)
    inspect.signature(client.create).bind(private_key="0xkey", wallet=None)
    inspect.signature(client.get_balance_allowance).bind(None, asset_type="COLLATERAL")
    inspect.signature(client.get_closed_only_mode).bind(None)
    inspect.signature(client.get_trading_approvals_state).bind(None)


def _recorded_orders():
    client = FakeClient(Accepted("matched", Decimal("1"), Decimal("1")))
    trader = SdkTrader(client)
    trader.place_fok(token_id="111", side="BUY", price=0.5, size=10, neg_risk=False, tick_size=0.01)
    trader.place_fok(token_id="111", side="SELL", price=0.5, size=10, neg_risk=False, tick_size=0.01)
    return client.orders

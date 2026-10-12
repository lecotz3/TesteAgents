import httpx
import pytest

from desk.config import ConfigError, Secrets, Settings
from desk.executor import LIVE_CONFIRMATION, make_executor
from desk.executor.base import ExecutionError, ExecutionUncertain
from desk.executor.live import LiveExecutor
from desk.executor.paper import PaperExecutor
from desk.geoblock import GeoStatus
from tests.factories import FakeSdkClient, make_book, make_market


class FakeTrader:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def place_fok(self, **kwargs):
        self.calls.append(kwargs)
        return self.response

    def usdc_balance(self):
        return 100.0


def allowed():
    return GeoStatus(blocked=False, country="PT", region="")


def blocked():
    return GeoStatus(blocked=True, country="BR", region="")


def test_paper_buy_walks_book_and_charges_fee():
    book = make_book(asks=((0.62, 5.0), (0.63, 100.0)))
    fill = PaperExecutor().buy(make_market(fee_rate=0.05), "111", 10, 0.63, book)
    assert fill.side == "BUY" and fill.shares == pytest.approx(10)
    assert fill.avg_price == pytest.approx(0.625)
    assert fill.fee == pytest.approx(5 * 0.05 * 0.62 * 0.38 + 5 * 0.05 * 0.63 * 0.37)


def test_paper_buy_is_fill_or_kill_within_limit():
    book = make_book(asks=((0.62, 5.0), (0.70, 100.0)))
    with pytest.raises(ExecutionError):
        PaperExecutor().buy(make_market(), "111", 10, 0.63, book)


def test_paper_sell_walks_bids():
    book = make_book(bids=((0.61, 4.0), (0.60, 100.0)))
    fill = PaperExecutor().sell(make_market(), "111", 10, 0.60, book)
    assert fill.side == "SELL" and fill.avg_price == pytest.approx(0.604)


def test_live_refuses_when_geoblocked():
    trader = FakeTrader({"success": True, "status": "matched"})
    with pytest.raises(ExecutionError):
        LiveExecutor(trader, blocked).buy(make_market(), "111", 10, 0.62, make_book())
    assert trader.calls == []


def test_live_buy_parses_matched_order():
    trader = FakeTrader({"success": True, "status": "matched", "makingAmount": "6.2", "takingAmount": "10"})
    fill = LiveExecutor(trader, allowed).buy(make_market(fee_rate=0.05), "111", 10, 0.62, make_book())
    assert fill.shares == pytest.approx(10) and fill.avg_price == pytest.approx(0.62)
    assert fill.fee == pytest.approx(10 * 0.05 * 0.62 * 0.38)
    call = trader.calls[0]
    assert call["side"] == "BUY" and call["price"] == 0.62 and call["size"] == 10 and call["token_id"] == "111"
    assert call["spend"] == pytest.approx(6.2)


def test_live_buy_spends_only_what_paper_would_pay():
    # 11 shares at 0.50, the rest deep at 0.90: the limit is the worst level, the budget is the walked cost.
    book = make_book(asks=((0.50, 11.0), (0.90, 100.0)))
    trader = FakeTrader({"success": True, "status": "matched", "makingAmount": "5.66", "takingAmount": "11.18"})
    LiveExecutor(trader, allowed).buy(make_market(), "111", 11.18, 0.90, book)
    call = trader.calls[0]
    assert call["price"] == 0.90
    assert call["spend"] == pytest.approx(11 * 0.50 + 0.18 * 0.90)


def test_live_buy_refuses_when_book_cannot_fill_within_limit():
    trader = FakeTrader({"success": True, "status": "matched"})
    with pytest.raises(ExecutionError):
        LiveExecutor(trader, allowed).buy(make_market(), "111", 10, 0.63, make_book(asks=((0.62, 5.0), (0.70, 100.0))))
    assert trader.calls == []


def test_live_sell_maps_amounts():
    trader = FakeTrader({"success": True, "status": "matched", "makingAmount": "10", "takingAmount": "6.1"})
    fill = LiveExecutor(trader, allowed).sell(make_market(), "111", 10, 0.60, make_book())
    assert fill.shares == pytest.approx(10) and fill.avg_price == pytest.approx(0.61)


def test_live_matched_without_amounts_assumes_full_fill_at_limit():
    trader = FakeTrader({"success": True, "status": "matched"})
    fill = LiveExecutor(trader, allowed).buy(make_market(), "111", 10, 0.62, make_book())
    assert fill.shares == 10 and fill.avg_price == pytest.approx(0.62)


@pytest.mark.parametrize("response", [
    {"success": False, "errorMsg": "not enough balance"},
    {"success": True, "status": "unmatched"},
    "garbage",
])
def test_live_unmatched_orders_raise(response):
    with pytest.raises(ExecutionError):
        LiveExecutor(FakeTrader(response), allowed).buy(make_market(), "111", 10, 0.62, make_book())


def test_live_client_exception_becomes_execution_error():
    class Exploding(FakeTrader):
        def place_fok(self, **kwargs):
            raise RuntimeError("PolyApiException 400")

    with pytest.raises(ExecutionUncertain):
        LiveExecutor(Exploding({}), allowed).buy(make_market(), "111", 10, 0.62, make_book())


def test_explicit_rejection_is_not_uncertain():
    trader = FakeTrader({"success": False, "errorMsg": "not enough balance"})
    with pytest.raises(ExecutionError) as info:
        LiveExecutor(trader, allowed).buy(make_market(), "111", 10, 0.62, make_book())
    assert not isinstance(info.value, ExecutionUncertain)


def test_make_executor_defaults_to_paper():
    with httpx.Client() as http:
        assert isinstance(make_executor(Settings(), Secrets(), http), PaperExecutor)


def test_live_requires_both_switches():
    with httpx.Client() as http:
        with pytest.raises(ConfigError):
            make_executor(Settings(mode="live"), Secrets(), http)
        with pytest.raises(ConfigError):
            make_executor(Settings(mode="live"), Secrets(desk_live=LIVE_CONFIRMATION), http)


def test_live_executor_uses_official_sdk(monkeypatch):
    from desk.executor import sdk

    seen = {}

    def fake_connect(cls, private_key, wallet):
        seen.update(key=private_key, wallet=wallet)
        return cls(FakeSdkClient())

    monkeypatch.setattr(sdk.SdkTrader, "connect", classmethod(fake_connect))
    secrets = Secrets(desk_live=LIVE_CONFIRMATION, poly_private_key="0xkey", poly_wallet="0xwallet")
    with httpx.Client() as http:
        executor = make_executor(Settings(mode="live"), secrets, http)
    assert isinstance(executor, LiveExecutor)
    assert seen == {"key": "0xkey", "wallet": "0xwallet"}

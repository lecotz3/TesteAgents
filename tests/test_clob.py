import httpx
import pytest
import respx

from desk.clob import BookClient, exit_slippage, parse_book, simulate_buy, simulate_sell
from tests.factories import make_book


def test_parse_book_sorts_best_first_and_drops_bad_rows():
    data = {
        "bids": [{"price": "0.01", "size": "100"}, {"price": "0.38", "size": "50"}, {"price": "0.37", "size": "0"}],
        "asks": [{"price": "0.99", "size": "10"}, {"price": "0.39", "size": "20"}, {"price": "x", "size": "1"}],
    }
    book = parse_book(data, "222")
    assert [lvl.price for lvl in book.bids] == [0.38, 0.01]
    assert [lvl.price for lvl in book.asks] == [0.39, 0.99]
    assert book.token_id == "222"


def test_simulate_buy_walks_levels():
    book = make_book(asks=((0.62, 5.0), (0.63, 100.0)))
    sim = simulate_buy(book, 10.0)
    assert sim.complete and sim.shares == pytest.approx(10.0)
    assert sim.avg_price == pytest.approx(0.625)
    assert sim.limit_price == 0.63
    assert sim.levels == ((0.62, 5.0), (0.63, 5.0))


def test_simulate_buy_respects_max_price_and_reports_incomplete():
    book = make_book(asks=((0.62, 5.0), (0.70, 100.0)))
    sim = simulate_buy(book, 10.0, max_price=0.65)
    assert not sim.complete and sim.shares == pytest.approx(5.0)


def test_simulate_on_empty_side():
    sim = simulate_buy(make_book(asks=()), 10.0)
    assert not sim.complete and sim.shares == 0.0 and sim.avg_price == 0.0


def test_simulate_sell_walks_bids():
    book = make_book(bids=((0.61, 4.0), (0.60, 100.0)))
    sim = simulate_sell(book, 10.0)
    assert sim.avg_price == pytest.approx((0.61 * 4 + 0.60 * 6) / 10)
    assert sim.limit_price == 0.60


def test_exit_slippage():
    deep = make_book(bids=((0.61, 1000.0),))
    assert exit_slippage(deep, 10.0) == pytest.approx(0.0)
    thin = make_book(bids=((0.61, 2.0), (0.50, 1000.0)))
    expected = (0.61 - (0.61 * 2 + 0.50 * 8) / 10) / 0.61
    assert exit_slippage(thin, 10.0) == pytest.approx(expected)
    assert exit_slippage(make_book(bids=((0.61, 2.0),)), 10.0) is None
    assert exit_slippage(make_book(bids=()), 10.0) is None


@respx.mock
def test_book_client_fetches_and_parses():
    route = respx.get(host="clob.polymarket.com", path="/book", params={"token_id": "111"}).mock(
        return_value=httpx.Response(
            200, json={"bids": [{"price": "0.61", "size": "10"}], "asks": [{"price": "0.62", "size": "10"}]}
        )
    )
    with httpx.Client() as http:
        book = BookClient(http).get_book("111")
    assert route.called
    assert book.best_bid == 0.61 and book.best_ask == 0.62 and book.token_id == "111"

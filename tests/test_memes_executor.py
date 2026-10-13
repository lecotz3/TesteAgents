import base64

import httpx
import pytest
import respx

from desk.config import ConfigError
from desk.executor.base import ExecutionError, ExecutionUncertain
from desk.memes.executor import LiveMemeExecutor, PaperMemeExecutor, PriceMoved, load_wallet
from desk.memes.sources import Jupiter, JupiterApi, SolanaRpc
from tests.meme_factories import MINT, USDC

JUP = "https://api.jup.ag"
RPC = "https://rpc.test"
OWNER = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"


def order_json(**o):
    return {"inAmount": "3000000", "outAmount": "2000000000", "priceImpactPct": "-0.001", "feeBps": 10,
            "transaction": None, "requestId": "r1", "errorCode": None, **o}


@respx.mock
def test_paper_buy_and_sell_use_real_quotes():
    route = respx.get(JUP + "/swap/v2/order").mock(side_effect=[
        httpx.Response(200, json=order_json()),
        httpx.Response(200, json=order_json(inAmount="2000000000", outAmount="2900000")),
    ])
    with httpx.Client() as http:
        ex = PaperMemeExecutor(Jupiter(JupiterApi(http, min_interval=0)), network_fee_usd=0.002)
        buy = ex.buy(MINT, 3.0)
        sell = ex.sell(MINT, 2_000_000_000)
    assert (buy.side, buy.amount_raw, buy.usd, buy.fee_usd) == ("BUY", 2_000_000_000, 3.0, 0.002)
    assert (sell.side, sell.amount_raw, sell.usd) == ("SELL", 2_000_000_000, 2.9)
    first = route.calls[0].request.url.params
    assert first["inputMint"] == USDC and first["outputMint"] == MINT and first["amount"] == "3000000"
    assert "taker" not in first  # paper never builds a transaction
    assert ex.funds_problem(3.0) is None and ex.sellable(MINT, 5) == 5


@respx.mock
def test_paper_without_route_is_an_execution_error():
    respx.get(JUP + "/swap/v2/order").mock(return_value=httpx.Response(200, json={"errorCode": 1, "outAmount": None}))
    with httpx.Client() as http, pytest.raises(ExecutionError):
        PaperMemeExecutor(Jupiter(JupiterApi(http, min_interval=0)), network_fee_usd=0.0).sell(MINT, 10)


class FakeWallet:
    owner = OWNER

    def sign(self, tx_b64: str) -> str:
        return "signed:" + tx_b64


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def live(http, clock: Clock | None = None):
    clock = clock or Clock()
    return LiveMemeExecutor(Jupiter(JupiterApi(http, min_interval=0)), FakeWallet(), SolanaRpc(http, RPC),
                            network_fee_usd=0.002, min_sol=0.01, clock=clock, sleep=clock.sleep)


@respx.mock
def test_live_buy_signs_and_executes_with_the_wallet_as_taker():
    order = respx.get(JUP + "/swap/v2/order").mock(return_value=httpx.Response(200, json=order_json(transaction="TX")))
    execute = respx.post(JUP + "/swap/v2/execute").mock(return_value=httpx.Response(200, json={
        "status": "Success", "signature": "SIG", "inputAmountResult": "3000000", "outputAmountResult": "1990000000"}))
    with httpx.Client() as http:
        fill = live(http).buy(MINT, 3.0)
    assert order.calls[0].request.url.params["taker"] == OWNER
    assert execute.calls[0].request.read() == b'{"signedTransaction":"signed:TX","requestId":"r1"}'
    assert (fill.amount_raw, fill.usd, fill.signature) == (1_990_000_000, 3.0, "SIG")


@respx.mock
def test_live_sell_reports_usdc_received():
    respx.get(JUP + "/swap/v2/order").mock(return_value=httpx.Response(200, json=order_json(transaction="TX")))
    respx.post(JUP + "/swap/v2/execute").mock(return_value=httpx.Response(200, json={
        "status": "Success", "signature": "SIG", "inputAmountResult": "2000000000", "outputAmountResult": "2950000"}))
    with httpx.Client() as http:
        fill = live(http).sell(MINT, 2_000_000_000)
    assert (fill.side, fill.amount_raw, fill.usd) == ("SELL", 2_000_000_000, 2.95)


@respx.mock
def test_live_order_without_transaction_sends_nothing():
    respx.get(JUP + "/swap/v2/order").mock(return_value=httpx.Response(200, json=order_json(
        transaction=None, errorMessage="Insufficient funds")))
    execute = respx.post(JUP + "/swap/v2/execute")
    with httpx.Client() as http, pytest.raises(ExecutionError) as err:
        live(http).buy(MINT, 3.0)
    assert not isinstance(err.value, ExecutionUncertain) and "Insufficient funds" in str(err.value)
    assert not execute.called


@respx.mock
def test_live_failed_swap_is_an_error_not_an_uncertainty():
    respx.get(JUP + "/swap/v2/order").mock(return_value=httpx.Response(200, json=order_json(transaction="TX")))
    execute = respx.post(JUP + "/swap/v2/execute").mock(
        return_value=httpx.Response(200, json={"status": "Failed", "error": "slippage", "code": -1}))
    with httpx.Client() as http, pytest.raises(ExecutionError) as failed:
        live(http).buy(MINT, 3.0)
    assert not isinstance(failed.value, ExecutionUncertain) and "slippage" in str(failed.value)
    assert execute.call_count == 1  # a definite answer is never polled again


@respx.mock
def test_live_lost_response_is_polled_with_the_same_signed_transaction():
    respx.get(JUP + "/swap/v2/order").mock(return_value=httpx.Response(200, json=order_json(transaction="TX")))
    execute = respx.post(JUP + "/swap/v2/execute").mock(side_effect=[
        httpx.ReadTimeout("lost"), httpx.Response(503), httpx.Response(200, json={"status": None}),
        httpx.Response(200, json={"status": "Success", "signature": "SIG", "inputAmountResult": "3000000",
                                  "outputAmountResult": "1990000000"}),
    ])
    clock = Clock()
    with httpx.Client() as http:
        fill = live(http, clock).buy(MINT, 3.0)
    assert (fill.amount_raw, fill.signature) == (1_990_000_000, "SIG")
    assert execute.call_count == 4 and len(clock.slept) == 3
    bodies = {c.request.read() for c in execute.calls}
    assert bodies == {b'{"signedTransaction":"signed:TX","requestId":"r1"}'}  # Jupiter never executes it twice


@respx.mock
def test_live_swap_is_uncertain_when_jupiter_never_answers_within_the_window():
    respx.get(JUP + "/swap/v2/order").mock(return_value=httpx.Response(200, json=order_json(transaction="TX")))
    execute = respx.post(JUP + "/swap/v2/execute").mock(side_effect=httpx.ReadTimeout("lost"))
    clock = Clock()
    with httpx.Client() as http, pytest.raises(ExecutionUncertain):
        live(http, clock).buy(MINT, 3.0)
    assert 60 <= clock.t <= 110  # inside Jupiter's 2-minute resubmission window
    assert execute.call_count >= 5


def rpc_reply(request):
    body = request.read().decode()
    if "getBalance" in body:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"value": 20_000_000}})
    mint = USDC if USDC in body else MINT
    amounts = ["2500000", "500000"] if mint == USDC else ["700"]
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"value": [
        {"account": {"data": {"parsed": {"info": {"tokenAmount": {"amount": a}}}}}} for a in amounts]}})


@respx.mock
def test_live_checks_the_wallet_before_trading():
    respx.post(RPC).mock(side_effect=rpc_reply)
    with httpx.Client() as http:
        ex = live(http)
        assert ex.funds_problem(2.0) is None  # 3.0 USDC and 0.02 SOL in the wallet
        assert ex.funds_problem(5.0) == "wallet_usdc_short"
        assert ex.sellable(MINT, 1000) == 700  # never sells more than the wallet holds


@respx.mock
def test_live_needs_sol_for_fees():
    respx.post(RPC).mock(side_effect=lambda req: httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {
        "value": 1_000_000 if "getBalance" in req.read().decode() else []}}))
    with httpx.Client() as http:
        assert live(http).funds_problem(1.0) == "wallet_sol_short"


def test_rpc_error_payload_raises():
    with respx.mock:
        respx.post(RPC).mock(return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                                                    "error": {"code": -32005, "message": "busy"}}))
        with httpx.Client() as http, pytest.raises(ValueError):
            SolanaRpc(http, RPC).sol_balance(OWNER)


def test_wallet_signs_only_its_own_slot():
    solders = pytest.importorskip("solders")
    from solders.hash import Hash
    from solders.keypair import Keypair
    from solders.message import MessageV0
    from solders.signature import Signature
    from solders.system_program import TransferParams, transfer
    from solders.transaction import VersionedTransaction

    me, payer = Keypair(), Keypair()  # gasless: Jupiter pays the fee and signs its own slot
    ix = transfer(TransferParams(from_pubkey=me.pubkey(), to_pubkey=Keypair().pubkey(), lamports=1))
    msg = MessageV0.try_compile(payer.pubkey(), [ix], [], Hash.default())
    unsigned = VersionedTransaction.populate(msg, [Signature.default(), Signature.default()])
    wallet = load_wallet(str(me))
    assert wallet.owner == str(me.pubkey())
    signed = VersionedTransaction.from_bytes(base64.b64decode(wallet.sign(base64.b64encode(bytes(unsigned)).decode())))
    assert signed.signatures[0] == Signature.default()  # the payer's slot is untouched
    assert signed.verify_with_results() == [False, True]
    assert solders is not None


def test_wallet_refuses_transactions_it_is_not_asked_to_sign():
    pytest.importorskip("solders")
    from solders.hash import Hash
    from solders.keypair import Keypair
    from solders.message import MessageV0
    from solders.signature import Signature
    from solders.system_program import TransferParams, transfer
    from solders.transaction import VersionedTransaction

    other = Keypair()
    ix = transfer(TransferParams(from_pubkey=other.pubkey(), to_pubkey=Keypair().pubkey(), lamports=1))
    tx = VersionedTransaction.populate(MessageV0.try_compile(other.pubkey(), [ix], [], Hash.default()),
                                       [Signature.default()])
    with pytest.raises(ExecutionError):
        load_wallet(str(Keypair())).sign(base64.b64encode(bytes(tx)).decode())


def test_bad_private_key_is_a_config_error():
    pytest.importorskip("solders")
    with pytest.raises(ConfigError):
        load_wallet("not-a-key")


@respx.mock
def test_live_buy_refuses_an_order_worse_than_the_approved_quote():
    # Real case: a wallet without SOL for fees gets a "gasless" order from Jupiter with a 10% fee.
    respx.get(JUP + "/swap/v2/order").mock(return_value=httpx.Response(200, json=order_json(
        transaction="TX", outAmount="1800000000", gasless=True, feeBps=1000)))
    execute = respx.post(JUP + "/swap/v2/execute")
    with httpx.Client() as http, pytest.raises(PriceMoved) as err:
        live(http).buy(MINT, 3.0, min_out=1_940_000_000)
    assert not execute.called
    assert "gasless" in str(err.value) and "10.0%" in str(err.value)


@respx.mock
def test_live_buy_at_or_above_the_floor_goes_through():
    respx.get(JUP + "/swap/v2/order").mock(return_value=httpx.Response(200, json=order_json(transaction="TX")))
    respx.post(JUP + "/swap/v2/execute").mock(return_value=httpx.Response(200, json={
        "status": "Success", "signature": "SIG", "inputAmountResult": "3000000", "outputAmountResult": "2000000000"}))
    with httpx.Client() as http:
        assert live(http).buy(MINT, 3.0, min_out=2_000_000_000).amount_raw == 2_000_000_000


@respx.mock
def test_paper_buy_refuses_a_quote_worse_than_the_approved_one():
    respx.get(JUP + "/swap/v2/order").mock(return_value=httpx.Response(200, json=order_json(outAmount="1800000000")))
    with httpx.Client() as http, pytest.raises(PriceMoved):
        PaperMemeExecutor(Jupiter(JupiterApi(http, min_interval=0)), network_fee_usd=0.002).buy(
            MINT, 3.0, min_out=1_940_000_000)

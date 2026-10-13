"""Turn a buy/sell intent into a MemeFill: simulated on real Jupiter quotes (paper) or a signed swap (live)."""
from __future__ import annotations

import base64
import time
from typing import Callable, Protocol

import httpx

from desk.config import ConfigError
from desk.executor.base import ExecutionError, ExecutionUncertain
from desk.memes.models import SOL_MINT, USDC_DECIMALS, USDC_MINT, MemeFill, Quote
from desk.memes.sources import FETCH_ERRORS, Jupiter, SolanaRpc


# Jupiter accepts the same signed transaction and requestId for 2 minutes to report its status, never executing it twice.
EXECUTE_POLL_SECONDS = 90.0
EXECUTE_POLL_EVERY = 5.0


class PriceMoved(ExecutionError):
    """The executable order is worse than the quote the buy was approved on. Nothing was signed."""


def check_floor(out_amount: int, min_out: int, fee_bps: float, gasless: bool = False) -> None:
    if min_out and out_amount < min_out:
        kind = "gasless, " if gasless else ""
        raise PriceMoved(f"ordem {1 - out_amount / min_out:.1%} abaixo do mínimo aprovado ({kind}taxa de {fee_bps / 100:.1f}%)")


def fetch_detail(exc: Exception) -> str:
    """The error type, plus the HTTP status and the start of the body when the server explained itself."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}: {exc.response.text[:200].strip()}"
    return type(exc).__name__


def usdc_raw(usd: float) -> int:
    return int(round(usd * 10 ** USDC_DECIMALS))


def raw_usdc(raw: int) -> float:
    return raw / 10 ** USDC_DECIMALS


class MemeExecutor(Protocol):
    mode: str

    def buy(self, mint: str, usd: float, min_out: int = 0) -> MemeFill: ...

    def sell(self, mint: str, amount_raw: int) -> MemeFill: ...

    def funds_problem(self, usd: float) -> str | None: ...

    def sellable(self, mint: str, amount_raw: int) -> int: ...

    def wallet_balance(self, mint: str) -> int: ...

    def sol_balance(self) -> float: ...

    def reclaim_rent(self) -> int: ...


class PaperMemeExecutor:
    """Fills at the exact amounts Jupiter would execute right now, plus an estimate of the network fee."""

    mode = "paper"

    def __init__(self, jupiter: Jupiter, network_fee_usd: float):
        self._jup = jupiter
        self._fee = network_fee_usd

    def _quote(self, input_mint: str, output_mint: str, amount: int) -> Quote:
        try:
            quote = self._jup.quote(input_mint, output_mint, amount)
        except FETCH_ERRORS as exc:
            raise ExecutionError(f"cotação indisponível: {fetch_detail(exc)}") from exc
        if quote is None:
            raise ExecutionError("Jupiter não encontrou rota")
        return quote

    def buy(self, mint: str, usd: float, min_out: int = 0) -> MemeFill:
        q = self._quote(USDC_MINT, mint, usdc_raw(usd))
        check_floor(q.out_amount, min_out, q.fee_bps)
        return MemeFill("BUY", q.out_amount, raw_usdc(q.in_amount), self._fee)

    def sell(self, mint: str, amount_raw: int) -> MemeFill:
        q = self._quote(mint, USDC_MINT, amount_raw)
        return MemeFill("SELL", q.in_amount, raw_usdc(q.out_amount), self._fee)

    def funds_problem(self, usd: float) -> str | None:
        return None

    def sellable(self, mint: str, amount_raw: int) -> int:
        return amount_raw

    def wallet_balance(self, mint: str) -> int:
        return 0  # paper has no wallet, and its fills are never uncertain

    def sol_balance(self) -> float:
        return float("inf")

    def reclaim_rent(self) -> int:
        return 0


class Wallet(Protocol):
    owner: str

    def sign(self, tx_b64: str) -> str: ...


class SoldersWallet:
    """Signs Jupiter transactions locally. The key never leaves this process and is never logged."""

    def __init__(self, keypair):
        self._kp = keypair
        self.owner = str(keypair.pubkey())

    def __repr__(self) -> str:
        return f"SoldersWallet({self.owner})"

    def sign(self, tx_b64: str) -> str:
        from solders.message import to_bytes_versioned
        from solders.transaction import VersionedTransaction

        tx = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
        signers = list(tx.message.account_keys[: tx.message.header.num_required_signatures])
        me = self._kp.pubkey()
        if me not in signers:
            raise ExecutionError("a transação não pede a assinatura desta carteira; recusada")
        signatures = list(tx.signatures)
        signatures[signers.index(me)] = self._kp.sign_message(to_bytes_versioned(tx.message))
        return base64.b64encode(bytes(VersionedTransaction.populate(tx.message, signatures))).decode()


def load_wallet(private_key_b58: str) -> SoldersWallet:
    try:
        from solders.keypair import Keypair
    except ImportError as exc:
        raise ConfigError("o modo real das memecoins exige: uv sync --extra memes-live") from exc
    try:
        keypair = Keypair.from_base58_string(private_key_b58.strip())
    except BaseException as exc:  # solders raises a pyo3 PanicException (a BaseException) on malformed keys
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise ConfigError("MEMES_PRIVATE_KEY inválida (esperado: chave privada base58 exportada da carteira)") from None
    return SoldersWallet(keypair)


class LiveMemeExecutor:
    """Real swaps through Jupiter Ultra: order with the wallet as taker, sign locally, execute."""

    mode = "live"

    def __init__(self, jupiter: Jupiter, wallet: Wallet, rpc: SolanaRpc, *, network_fee_usd: float, min_sol: float,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self._jup = jupiter
        self._wallet = wallet
        self._rpc = rpc
        self._fee = network_fee_usd
        self._min_sol = min_sol
        self._clock = clock
        self._sleep = sleep

    @property
    def owner(self) -> str:
        return self._wallet.owner

    def _swap(self, input_mint: str, output_mint: str, amount: int, min_out: int = 0) -> tuple[int, int, str | None]:
        try:
            order = self._jup.order(input_mint, output_mint, amount, taker=self.owner)
        except FETCH_ERRORS as exc:
            raise ExecutionError(f"ordem indisponível: {fetch_detail(exc)}") from exc
        if not order.get("transaction") or not order.get("requestId"):
            reason = order.get("errorMessage") or order.get("error") or "?"
            raise ExecutionError(f"Jupiter não montou a transação: {reason}")
        check_floor(int(order.get("outAmount") or 0), min_out, float(order.get("feeBps") or 0), bool(order.get("gasless")))
        signed = self._wallet.sign(order["transaction"])
        result = self._execute(signed, order["requestId"])
        if result.get("status") == "Failed":
            raise ExecutionError(f"swap falhou: {result.get('error') or '?'} (código {result.get('code')})")
        spent = int(result.get("inputAmountResult") or order["inAmount"])
        received = int(result.get("outputAmountResult") or order["outAmount"])
        return spent, received, result.get("signature")

    def _execute(self, signed: str, request_id: str) -> dict:
        """Submits, then resubmits the same signed transaction until Jupiter says Success or Failed."""
        deadline = self._clock() + EXECUTE_POLL_SECONDS
        while True:
            try:
                result = self._jup.execute(signed, request_id)
            except FETCH_ERRORS as exc:  # it may have landed: ask again
                last = f"sem resposta do execute ({type(exc).__name__})"
            else:
                if result.get("status") in ("Success", "Failed"):
                    return result
                last = f"status desconhecido do execute: {result.get('status')!r}"
            if self._clock() >= deadline:
                raise ExecutionUncertain(f"{last} por {EXECUTE_POLL_SECONDS:.0f}s; confira a carteira")
            self._sleep(EXECUTE_POLL_EVERY)

    def buy(self, mint: str, usd: float, min_out: int = 0) -> MemeFill:
        spent, received, sig = self._swap(USDC_MINT, mint, usdc_raw(usd), min_out)
        return MemeFill("BUY", received, raw_usdc(spent), self._fee, sig)

    def sell(self, mint: str, amount_raw: int) -> MemeFill:
        spent, received, sig = self._swap(mint, USDC_MINT, amount_raw)
        return MemeFill("SELL", spent, raw_usdc(received), self._fee, sig)

    def funds_problem(self, usd: float) -> str | None:
        if self._rpc.sol_balance(self.owner) < self._min_sol:
            return "wallet_sol_short"
        if raw_usdc(self._rpc.token_balance(self.owner, USDC_MINT)) < usd:
            return "wallet_usdc_short"
        return None

    def sellable(self, mint: str, amount_raw: int) -> int:
        return min(amount_raw, self.wallet_balance(mint))

    def wallet_balance(self, mint: str) -> int:
        return self._rpc.token_balance(self.owner, mint)

    def sol_balance(self) -> float:
        return self._rpc.sol_balance(self.owner)

    def reclaim_rent(self, limit: int = 10) -> int:
        """Every token bought opens an account that holds ~0.0015 SOL of rent. Once a sell empties it, closing it
        sends that SOL back to the wallet. Built here, not by Jupiter: only CloseAccount, only empty accounts, only
        to the wallet itself. USDC and wrapped SOL accounts are kept."""
        from solders.hash import Hash
        from solders.instruction import AccountMeta, Instruction
        from solders.message import MessageV0
        from solders.pubkey import Pubkey
        from solders.signature import Signature
        from solders.transaction import VersionedTransaction

        empty = [a for a in self._rpc.empty_token_accounts(self.owner) if a[2] not in (USDC_MINT, SOL_MINT)][:limit]
        if not empty:
            return 0
        owner = Pubkey.from_string(self.owner)
        close_account = bytes([9])  # SPL Token / Token-2022 instruction CloseAccount
        ixs = [Instruction(Pubkey.from_string(program), close_account, [
            AccountMeta(Pubkey.from_string(account), False, True), AccountMeta(owner, False, True), AccountMeta(owner, True, False),
        ]) for account, program, _ in empty]
        msg = MessageV0.try_compile(owner, ixs, [], Hash.from_string(self._rpc.latest_blockhash()))
        unsigned = base64.b64encode(bytes(VersionedTransaction.populate(msg, [Signature.default()]))).decode()
        self._rpc.send_transaction(self._wallet.sign(unsigned))
        return len(ixs)

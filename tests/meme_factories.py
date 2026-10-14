"""Payloads shaped like the real DexScreener / RugCheck / Jupiter responses (observed 2026-10-13)."""
from __future__ import annotations

from datetime import timedelta

from desk.memes.models import MemePosition, Pair, Quote, Safety
from desk.memes.x import XPost, XProfile
from tests.factories import NOW

MINT = "3sKaoPryg4KrXubLWtoqF7MznLT7QNqKRxMMkkxTpump"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def raw_pair(mint: str = MINT, **overrides) -> dict:
    pair = {
        "chainId": "solana", "dexId": "pumpswap", "url": f"https://dexscreener.com/solana/{mint[:8]}",
        "pairAddress": "HvYfFr8nm6bn9V93TDDMgqWwS2D2LsMn1Y3rDCToqeAB",
        "baseToken": {"address": mint, "name": "Save The Seals", "symbol": "SEALS"},
        "quoteToken": {"address": "So11111111111111111111111111111111111111112", "name": "Wrapped SOL", "symbol": "SOL"},
        "priceNative": "0.0000071", "priceUsd": "0.001389",
        "txns": {"m5": {"buys": 120, "sells": 110}, "h1": {"buys": 3380, "sells": 3686},
                 "h6": {"buys": 6290, "sells": 6052}, "h24": {"buys": 6290, "sells": 6052}},
        "volume": {"h24": 837266.15, "h6": 837266.15, "h1": 498398.65, "m5": 13288.47},
        "priceChange": {"m5": 2.1, "h1": 35.0, "h6": 120.0, "h24": 120.0},
        "liquidity": {"usd": 107597.06, "base": 39334119, "quote": 495.0},
        "fdv": 1288164, "marketCap": 1288164,
        "pairCreatedAt": int((NOW - timedelta(hours=3)).timestamp() * 1000),
        "info": {"imageUrl": "x", "websites": [{"label": "Website", "url": "https://seals.fun"}],
                 "socials": [{"type": "twitter", "url": "https://x.com/seals"}]},
    }
    pair.update(overrides)
    return pair


def raw_rugcheck(**overrides) -> dict:
    report = {
        "mint": MINT, "mintAuthority": None, "freezeAuthority": None, "rugged": False,
        "score": 1, "score_normalised": 1, "risks": [],
        "token": {"supply": 1_000_000_000_000_000, "decimals": 6, "mintAuthority": None, "freezeAuthority": None},
        "topHolders": [
            {"owner": "HvYfFr8nm6bn9V93TDDMgqWwS2D2LsMn1Y3rDCToqeAB", "pct": 30.0, "insider": False},  # the AMM pool
            *({"owner": f"holder{i}", "pct": 2.0, "insider": False} for i in range(12)),
        ],
        "knownAccounts": {"HvYfFr8nm6bn9V93TDDMgqWwS2D2LsMn1Y3rDCToqeAB": {"name": "Pump Fun AMM", "type": "AMM"}},
        "markets": [
            {"marketType": "pump_fun_amm", "pubkey": "HvYfFr8nm6bn9V93TDDMgqWwS2D2LsMn1Y3rDCToqeAB",
             "lp": {"lpLockedPct": 100.0, "lpLockedUSD": 107484.0, "baseUSD": 53742.0, "quoteUSD": 53742.0}},
            {"marketType": "meteoraDlmm", "pubkey": "met1",
             "lp": {"lpLockedPct": 0.0, "lpLockedUSD": 0.0, "baseUSD": 1000.0, "quoteUSD": 1000.0}},
        ],
        "insiderNetworks": [{"id": "proud-jade-falcon", "size": 5, "currentHolding": 30_000_000_000_000}],
        "creatorBalance": 0, "transferFee": {"pct": 0, "maxAmount": 0},
    }
    report.update(overrides)
    return report


def make_pair(**overrides) -> Pair:
    base = dict(
        mint=MINT, symbol="SEALS", name="Save The Seals", pair_address="HvYf", dex="pumpswap", price_usd=0.001389,
        liquidity_usd=107597.0, market_cap_usd=1288164.0, volume_h1_usd=498398.0, buys_h1=3380, sells_h1=3686,
        change_h1_pct=35.0, change_h6_pct=120.0, created_at=NOW - timedelta(hours=3), has_socials=True,
        url="https://dexscreener.com/solana/x",
    )
    base.update(overrides)
    return Pair(**base)


def make_safety(**overrides) -> Safety:
    base = dict(
        mint_authority=None, freeze_authority=None, rugged=False, danger_risks=(), lp_locked_pct=98.0,
        top10_pct=24.0, insider_pct=3.0, creator_pct=0.0, transfer_fee_pct=0.0, decimals=6, score=1,
    )
    base.update(overrides)
    return Safety(**base)


def make_meme_position(**overrides) -> MemePosition:
    base = dict(
        mint=MINT, symbol="SEALS", name="Save The Seals", decimals=6, amount_raw=2_000_000_000, cost_usd=3.0,
        entry_price_usd=0.0015, peak_price_usd=0.0015, entry_liquidity_usd=100_000.0,
        opened_at=NOW - timedelta(hours=1),
    )
    base.update(overrides)
    return MemePosition(**base)


class FakeDex:
    def __init__(self, pairs: dict[str, Pair] | None = None, discovered: list[str] | None = None, fail=None):
        self.by_mint = dict(pairs or {})
        self.discovered = list(self.by_mint) if discovered is None else discovered
        self.fail = fail
        self.pair_calls: list[list[str]] = []

    def discover(self) -> list[str]:
        if self.fail:
            raise self.fail
        return list(self.discovered)

    def pairs(self, mints: list[str]) -> dict[str, Pair]:
        if self.fail:
            raise self.fail
        self.pair_calls.append(list(mints))
        return {m: self.by_mint[m] for m in mints if m in self.by_mint}


class FakeRug:
    def __init__(self, reports: dict[str, Safety] | None = None, default: Safety | None = None):
        self.reports = reports or {}
        self.default = default or make_safety()
        self.calls: list[str] = []

    def report(self, mint: str, main_pool: str | None = None) -> Safety:
        self.calls.append(mint)
        return self.reports.get(mint, self.default)


class FakeJupiter:
    """Quotes at a fixed USD price per token, losing `loss` of the value on each leg."""

    def __init__(self, prices: dict[str, float] | None = None, decimals: int = 6, leg_loss: float = 0.01,
                 impact_pct: float = 0.3, no_route: set[str] = frozenset()):
        self.prices = prices or {}
        self.decimals, self.leg_loss, self.impact, self.no_route = decimals, leg_loss, impact_pct, set(no_route)
        self.calls: list[tuple[str, str, int]] = []

    def quote(self, input_mint: str, output_mint: str, amount: int) -> Quote | None:
        self.calls.append((input_mint, output_mint, amount))
        token = output_mint if input_mint == USDC else input_mint
        if token in self.no_route:
            return None
        price = self.prices.get(token, 0.0015)
        if input_mint == USDC:
            out = amount / 1e6 / price * 10 ** self.decimals * (1 - self.leg_loss)
        else:
            out = amount / 10 ** self.decimals * price * 1e6 * (1 - self.leg_loss)
        return Quote(in_amount=amount, out_amount=int(out), price_impact_pct=self.impact, fee_bps=10)


class FakeX:
    def __init__(self, profile=None, post=None, raises=None):
        self._profile, self._post, self.raises, self.calls = profile, post, raises, []

    def profile(self, handle):
        self.calls.append(handle)
        if self.raises:
            raise self.raises
        return self._profile

    def post(self, handle, post_id):
        self.calls.append((handle, post_id))
        if self.raises:
            raise self.raises
        return self._post


def make_post(**o):
    base = dict(id="1", author="elonmusk", author_followers=241_000_000, author_verified=True, text="meme",
                created_at=NOW - timedelta(hours=2), likes=928, retweets=61, replies=188, quotes=13, views=36693)
    return XPost(**{**base, **o})


def make_profile(**o):
    base = dict(handle="CealOnSol", followers=1, tweets=2, joined=NOW - timedelta(minutes=30), verified=False)
    return XProfile(**{**base, **o})


class FakeTokens:
    def __init__(self, discovered=(), info=None, fail=None, items=()):
        self.discovered, self.by_mint, self.fail, self.items = list(discovered), dict(info or {}), fail, list(items)
        self.info_calls: list[list[str]] = []

    def discover(self, categories, interval, limit=100):
        if self.fail:
            raise self.fail
        return list(self.discovered)

    def lists(self, categories, interval, limit=100):
        if self.fail:
            raise self.fail
        return list(self.items)

    def info(self, mints):
        self.info_calls.append(list(mints))
        if self.fail:
            raise self.fail
        return {m: self.by_mint[m] for m in mints if m in self.by_mint}

"""Free public APIs: DexScreener (discovery and prices), RugCheck (contract safety), Jupiter (lists, quotes)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from desk.memes.models import Pair, Quote, Safety

log = logging.getLogger("desk")

DEX_URL = "https://api.dexscreener.com"
RUG_URL = "https://api.rugcheck.xyz"
JUP_URL = "https://lite-api.jup.ag"
DISCOVERY_FEEDS = (
    "/token-profiles/latest/v1",  # tokens that just bought a profile
    "/token-boosts/latest/v1",  # tokens paying for promotion right now
    "/token-boosts/top/v1",
    "/community-takeovers/latest/v1",  # abandoned tokens revived by their holders
)
BATCH = 30  # DexScreener's limit of addresses per /tokens call
FETCH_ERRORS = (httpx.HTTPError, ValueError)


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _x_url(socials) -> str | None:
    for social in socials if isinstance(socials, list) else []:
        url = str((social or {}).get("url") or "")
        if (social or {}).get("type") in ("twitter", "x") or "x.com/" in url or "twitter.com/" in url:
            return url or None
    return None


def parse_pair(raw: dict) -> Pair | None:
    try:
        if raw.get("chainId") != "solana":
            return None
        base = raw["baseToken"]
        price = float(raw["priceUsd"])
        liquidity = float((raw.get("liquidity") or {})["usd"])
        h1 = (raw.get("txns") or {}).get("h1") or {}
        change = raw.get("priceChange") or {}
        created = raw.get("pairCreatedAt")
        info = raw.get("info") or {}
        return Pair(
            mint=str(base["address"]), symbol=str(base.get("symbol") or "?"), name=str(base.get("name") or "?"),
            pair_address=str(raw.get("pairAddress") or ""), dex=str(raw.get("dexId") or ""), price_usd=price,
            liquidity_usd=liquidity, market_cap_usd=_num(raw.get("marketCap") or raw.get("fdv")),
            volume_h1_usd=_num((raw.get("volume") or {}).get("h1")), buys_h1=int(_num(h1.get("buys"))),
            sells_h1=int(_num(h1.get("sells"))), change_h1_pct=_num(change.get("h1")),
            change_h6_pct=_num(change.get("h6")),
            created_at=datetime.fromtimestamp(created / 1000, timezone.utc) if created else None,
            has_socials=bool(info.get("socials") or info.get("websites")), url=str(raw.get("url") or ""),
            x_url=_x_url(info.get("socials")),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def parse_safety(raw: dict) -> Safety:
    token = raw.get("token") or {}
    supply = _num(token.get("supply")) or 0.0
    known = raw.get("knownAccounts") or {}
    pools = {m.get("pubkey") for m in raw.get("markets") or []} | {
        addr for addr, info in known.items() if (info or {}).get("type") in ("AMM", "LOCKER")
    }
    whales = [h for h in raw.get("topHolders") or [] if h.get("owner") not in pools]
    locked = total = 0.0
    for market in raw.get("markets") or []:
        lp = market.get("lp") or {}
        size = _num(lp.get("baseUSD")) + _num(lp.get("quoteUSD"))
        total += size
        locked += min(_num(lp.get("lpLockedUSD")), size)
    insiders = sum(_num(n.get("currentHolding")) for n in raw.get("insiderNetworks") or [])
    return Safety(
        mint_authority=raw.get("mintAuthority") or token.get("mintAuthority"),
        freeze_authority=raw.get("freezeAuthority") or token.get("freezeAuthority"),
        rugged=bool(raw.get("rugged")),
        danger_risks=tuple(str(r.get("name")) for r in raw.get("risks") or [] if r.get("level") == "danger"),
        lp_locked_pct=100 * locked / total if total else 0.0,
        top10_pct=sum(_num(h.get("pct")) for h in whales[:10]),
        insider_pct=100 * insiders / supply if supply else 0.0,
        creator_pct=100 * _num(raw.get("creatorBalance")) / supply if supply else 0.0,
        transfer_fee_pct=_num((raw.get("transferFee") or {}).get("pct")),
        decimals=int(_num(token.get("decimals"), 6)),
        score=int(_num(raw.get("score_normalised") or raw.get("score"))),
    )


class DexScreener:
    def __init__(self, http: httpx.Client, base_url: str = DEX_URL):
        self._http = http
        self._base = base_url

    def discover(self) -> list[str]:
        """Solana mints from every discovery feed, in feed order, without repeats. A failing feed is skipped."""
        seen: dict[str, None] = {}
        for feed in DISCOVERY_FEEDS:
            try:
                resp = self._http.get(self._base + feed)
                resp.raise_for_status()
                items = resp.json()
            except FETCH_ERRORS as exc:
                log.warning("feed %s indisponível: %s", feed, type(exc).__name__)
                continue
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and item.get("chainId") == "solana" and item.get("tokenAddress"):
                    seen.setdefault(str(item["tokenAddress"]), None)
        return list(seen)

    def pairs(self, mints: list[str]) -> dict[str, Pair]:
        """The most liquid Solana pair of each mint."""
        best: dict[str, Pair] = {}
        for i in range(0, len(mints), BATCH):
            chunk = ",".join(mints[i:i + BATCH])
            resp = self._http.get(f"{self._base}/tokens/v1/solana/{chunk}")
            resp.raise_for_status()
            for raw in resp.json() or []:
                pair = parse_pair(raw) if isinstance(raw, dict) else None
                if pair and (pair.mint not in best or pair.liquidity_usd > best[pair.mint].liquidity_usd):
                    best[pair.mint] = pair
        return best


@dataclass(frozen=True)
class TokenInfo:
    organic_score: float | None
    twitter: str | None


class JupiterTokens:
    """Jupiter's token lists (most traded, most organic, trending) and per-token organic score. Free, keyless."""

    SEARCH_BATCH = 100

    def __init__(self, http: httpx.Client, base_url: str = JUP_URL):
        self._http = http
        self._base = base_url

    def discover(self, categories: tuple[str, ...] | list[str], interval: str, limit: int = 100) -> list[str]:
        seen: dict[str, None] = {}
        for category in categories:
            try:
                resp = self._http.get(f"{self._base}/tokens/v2/{category}/{interval}", params={"limit": limit})
                resp.raise_for_status()
                items = resp.json()
            except FETCH_ERRORS as exc:
                log.warning("lista %s da Jupiter indisponível: %s", category, type(exc).__name__)
                continue
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and item.get("id"):
                    seen.setdefault(str(item["id"]), None)
        return list(seen)

    def info(self, mints: list[str]) -> dict[str, TokenInfo]:
        found: dict[str, TokenInfo] = {}
        for i in range(0, len(mints), self.SEARCH_BATCH):
            resp = self._http.get(f"{self._base}/tokens/v2/search",
                                  params={"query": ",".join(mints[i:i + self.SEARCH_BATCH])})
            resp.raise_for_status()
            for item in resp.json() or []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                score = item.get("organicScore")
                found[str(item["id"])] = TokenInfo(
                    organic_score=float(score) if isinstance(score, (int, float)) else None,
                    twitter=str(item["twitter"]) if item.get("twitter") else None,
                )
        return found


class RugCheck:
    def __init__(self, http: httpx.Client, base_url: str = RUG_URL):
        self._http = http
        self._base = base_url

    def report(self, mint: str) -> Safety:
        resp = self._http.get(f"{self._base}/v1/tokens/{mint}/report")
        resp.raise_for_status()
        return parse_safety(resp.json())


class Jupiter:
    """Ultra API quotes. Without a taker the order carries no transaction: a free, exact executable price."""

    def __init__(self, http: httpx.Client, base_url: str = JUP_URL):
        self._http = http
        self._base = base_url

    def order(self, input_mint: str, output_mint: str, amount: int, taker: str | None = None) -> dict:
        params = {"inputMint": input_mint, "outputMint": output_mint, "amount": str(amount)}
        if taker:
            params["taker"] = taker
        resp = self._http.get(f"{self._base}/ultra/v1/order", params=params)
        resp.raise_for_status()
        return resp.json()

    def quote(self, input_mint: str, output_mint: str, amount: int) -> Quote | None:
        data = self.order(input_mint, output_mint, amount)
        if data.get("errorCode") or not data.get("outAmount"):
            return None
        return Quote(
            in_amount=int(data["inAmount"]), out_amount=int(data["outAmount"]),
            price_impact_pct=abs(_num(data.get("priceImpactPct"))) * 100, fee_bps=int(_num(data.get("feeBps"))),
        )

    def execute(self, signed_transaction: str, request_id: str) -> dict:
        resp = self._http.post(f"{self._base}/ultra/v1/execute",
                               json={"signedTransaction": signed_transaction, "requestId": request_id})
        resp.raise_for_status()
        return resp.json()


class SolanaRpc:
    """Wallet balances straight from a Solana RPC node (the public one by default)."""

    def __init__(self, http: httpx.Client, url: str):
        self._http = http
        self._url = url

    def _call(self, method: str, params: list) -> dict:
        resp = self._http.post(self._url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        resp.raise_for_status()
        data = resp.json()
        if "error" in data or "result" not in data:
            raise ValueError(f"RPC {method}: {(data.get('error') or {}).get('message', 'sem resultado')}")
        return data["result"]

    def sol_balance(self, owner: str) -> float:
        return int(self._call("getBalance", [owner])["value"]) / 1e9

    def token_balance(self, owner: str, mint: str) -> int:
        """Raw amount of `mint` across every token account of `owner` (classic SPL and Token-2022)."""
        accounts = self._call("getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed"}])["value"]
        return sum(int(a["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"]) for a in accounts)

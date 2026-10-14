import json

import httpx
import pytest
import respx

from desk.memes.sources import DexScreener, Jupiter, JupiterApi, JupiterTokens, RugCheck, parse_pair, parse_safety
from tests.factories import NOW
from tests.meme_factories import MINT, USDC, raw_pair, raw_rugcheck

DEX = "https://api.dexscreener.com"
RUG = "https://api.rugcheck.xyz"
JUP = "https://api.jup.ag"


def api(http, **kw):
    return JupiterApi(http, min_interval=0, **kw)


def test_parse_pair_reads_real_fields():
    p = parse_pair(raw_pair())
    assert p.mint == MINT and p.symbol == "SEALS" and p.dex == "pumpswap"
    assert p.price_usd == pytest.approx(0.001389) and p.liquidity_usd == pytest.approx(107597.06)
    assert p.volume_h1_usd == pytest.approx(498398.65) and p.buys_h1 == 3380 and p.sells_h1 == 3686
    assert p.change_h1_pct == 35.0 and p.age_minutes(NOW) == pytest.approx(180)
    assert p.has_socials is True


@pytest.mark.parametrize("broken", [{"priceUsd": None}, {"baseToken": None}, {"liquidity": "oops"}, {"chainId": "base"}])
def test_parse_pair_skips_unusable_records(broken):
    assert parse_pair(raw_pair(**broken)) is None


def test_parse_safety_excludes_pool_from_top_holders():
    s = parse_safety(raw_rugcheck())
    assert s.top10_pct == pytest.approx(20.0)  # the AMM pool (30%) is liquidity, not a whale
    assert s.insider_pct == pytest.approx(3.0)
    assert s.decimals == 6 and s.mint_authority is None and s.danger_risks == ()


def test_lp_lock_ignores_concentrated_liquidity_pools():
    # DLMM/CLMM positions belong to individual LPs: there is no LP token to lock (seen on FLOYD and VEIL)
    assert parse_safety(raw_rugcheck()).lp_locked_pct == pytest.approx(100.0)


def test_lp_lock_of_the_main_pool_wins_even_with_stale_sizes():
    raw = raw_rugcheck(markets=[
        {"marketType": "pump_fun_amm", "pubkey": "MAIN", "lp": {"lpLockedPct": 100.0, "lpLockedUSD": 0.0,
                                                               "baseUSD": 0.0, "quoteUSD": 0.0}},
        {"marketType": "meteora_damm_v2", "pubkey": "DAMM", "lp": {"lpLockedPct": 0.0, "lpLockedUSD": 0.0,
                                                                  "baseUSD": 40.0, "quoteUSD": 43.0}},
    ])  # seen on HIRE: RugCheck had no size for the main pool
    assert parse_safety(raw, main_pool="MAIN").lp_locked_pct == pytest.approx(100.0)
    assert parse_safety(raw).lp_locked_pct == pytest.approx(0.0)  # without the main pool: weighted, conservative


def test_lp_lock_weights_the_pools_with_lp_tokens():
    raw = raw_rugcheck(markets=[
        {"marketType": "pump_fun_amm", "pubkey": "A", "lp": {"lpLockedPct": 100.0, "lpLockedUSD": 60000.0,
                                                            "baseUSD": 30000.0, "quoteUSD": 30000.0}},
        {"marketType": "raydium_cpmm", "pubkey": "B", "lp": {"lpLockedPct": 0.0, "lpLockedUSD": 0.0,
                                                            "baseUSD": 20000.0, "quoteUSD": 20000.0}},
        {"marketType": "raydium_clmm", "pubkey": "C", "lp": {"lpLockedPct": 0.0, "lpLockedUSD": 0.0,
                                                            "baseUSD": 500000.0, "quoteUSD": 500000.0}},
    ])
    assert parse_safety(raw).lp_locked_pct == pytest.approx(60.0)  # the dev can still pull the cpmm pool


def test_only_concentrated_pools_fall_back_to_all_of_them():
    raw = raw_rugcheck(markets=[{"marketType": "meteoraDlmm", "pubkey": "D", "lp": {
        "lpLockedPct": 0.0, "lpLockedUSD": 0.0, "baseUSD": 5000.0, "quoteUSD": 5000.0}}])
    assert parse_safety(raw).lp_locked_pct == pytest.approx(0.0)


def test_parse_safety_collects_danger_risks():
    s = parse_safety(raw_rugcheck(risks=[{"name": "Freeze Authority still enabled", "level": "danger"},
                                         {"name": "Low amount of LP Providers", "level": "warn"}]))
    assert s.danger_risks == ("Freeze Authority still enabled",)


@respx.mock
def test_discover_merges_feeds_and_keeps_solana_only():
    for path, items in {
        "/token-profiles/latest/v1": [{"chainId": "solana", "tokenAddress": "A"}, {"chainId": "base", "tokenAddress": "B"}],
        "/token-boosts/latest/v1": [{"chainId": "solana", "tokenAddress": "C"}],
        "/token-boosts/top/v1": [{"chainId": "solana", "tokenAddress": "A"}],
        "/community-takeovers/latest/v1": [{"chainId": "solana", "tokenAddress": "D"}],
    }.items():
        respx.get(DEX + path).mock(return_value=httpx.Response(200, json=items))
    with httpx.Client() as http:
        assert DexScreener(http).discover() == ["A", "C", "D"]


@respx.mock
def test_discover_survives_one_feed_failing():
    respx.get(DEX + "/token-profiles/latest/v1").mock(return_value=httpx.Response(500))
    respx.get(DEX + "/token-boosts/latest/v1").mock(return_value=httpx.Response(200, json=[{"chainId": "solana", "tokenAddress": "C"}]))
    respx.get(DEX + "/token-boosts/top/v1").mock(return_value=httpx.Response(200, text="<html>"))
    respx.get(DEX + "/community-takeovers/latest/v1").mock(return_value=httpx.Response(200, json=[]))
    with httpx.Client() as http:
        assert DexScreener(http).discover() == ["C"]


@respx.mock
def test_pairs_batches_30_and_keeps_most_liquid_pair_per_mint():
    mints = [f"M{i}" for i in range(31)]
    route = respx.get(url__regex=DEX + r"/tokens/v1/solana/.*").mock(side_effect=lambda req: httpx.Response(200, json=[
        raw_pair(m, liquidity={"usd": 1000.0}) for m in req.url.path.rsplit("/", 1)[1].split(",")
    ] + [raw_pair("M0", liquidity={"usd": 5000.0}, priceUsd="0.5")]))
    with httpx.Client() as http:
        pairs = DexScreener(http).pairs(mints)
    assert route.call_count == 2
    assert len(pairs) == 31 and pairs["M0"].price_usd == 0.5


@respx.mock
def test_rugcheck_report_is_parsed():
    respx.get(f"{RUG}/v1/tokens/{MINT}/report").mock(return_value=httpx.Response(200, json=raw_rugcheck()))
    with httpx.Client() as http:
        assert RugCheck(http).report(MINT).decimals == 6
        assert RugCheck(http).report(MINT, main_pool="HvYfFr8nm6bn9V93TDDMgqWwS2D2LsMn1Y3rDCToqeAB").lp_locked_pct == 100.0


@respx.mock
def test_jupiter_quote_reads_amounts_and_impact():
    respx.get(JUP + "/swap/v2/order").mock(return_value=httpx.Response(200, json={
        "inAmount": "3000000", "outAmount": "2000000000", "priceImpactPct": "-0.015", "feeBps": 10,
        "transaction": None, "requestId": "r1", "errorCode": None}))
    with httpx.Client() as http:
        q = Jupiter(api(http)).quote(USDC, MINT, 3_000_000)
    assert q.out_amount == 2_000_000_000 and q.price_impact_pct == pytest.approx(1.5) and q.fee_bps == 10


@respx.mock
def test_jupiter_quote_without_route_is_none():
    respx.get(JUP + "/swap/v2/order").mock(return_value=httpx.Response(200, json={
        "errorCode": 1, "errorMessage": "No routes found", "outAmount": None}))
    with httpx.Client() as http:
        assert Jupiter(api(http)).quote(USDC, MINT, 3_000_000) is None


def jup_token(mint, **o):
    return {"id": mint, "symbol": mint[:4], "organicScore": 75.0, "organicScoreLabel": "high",
            "twitter": f"https://x.com/{mint[:4]}/status/1", **o}


@respx.mock
def test_jupiter_tokens_discover_merges_lists_and_survives_a_failure():
    respx.get(JUP + "/tokens/v2/toptraded/1h").mock(return_value=httpx.Response(200, json=[jup_token("A"), jup_token("B")]))
    respx.get(JUP + "/tokens/v2/toporganicscore/1h").mock(return_value=httpx.Response(500))
    respx.get(JUP + "/tokens/v2/toptrending/1h").mock(return_value=httpx.Response(200, json=[jup_token("B"), jup_token("C")]))
    with httpx.Client() as http:
        tokens = JupiterTokens(api(http))
        assert tokens.discover(("toptraded", "toporganicscore", "toptrending"), "1h") == ["A", "B", "C"]


@respx.mock
def test_jupiter_lists_are_fetched_once_per_run_for_discovery_and_movers():
    route = respx.get(JUP + "/tokens/v2/toptraded/1h").mock(return_value=httpx.Response(200, json=[jup_token("A")]))
    with httpx.Client() as http:
        tokens = JupiterTokens(api(http))
        assert tokens.discover(("toptraded",), "1h") == ["A"]
        assert [i["id"] for i in tokens.lists(("toptraded",), "1h")] == ["A"]
    assert route.call_count == 1


@respx.mock
def test_jupiter_tokens_info_reads_organic_score_and_x_link_in_batches():
    mints = [f"M{i}" for i in range(101)]
    route = respx.get(JUP + "/tokens/v2/search").mock(side_effect=lambda req: httpx.Response(200, json=[
        jup_token(m, organicScore=None if m == "M5" else 75.0, twitter=None if m == "M6" else "https://x.com/a/status/9")
        for m in req.url.params["query"].split(",")]))
    with httpx.Client() as http:
        info = JupiterTokens(api(http)).info(mints)
    assert route.call_count == 2 and len(info) == 101
    assert info["M0"].organic_score == 75.0 and info["M0"].twitter == "https://x.com/a/status/9"
    assert info["M5"].organic_score is None and info["M6"].twitter is None


class FakeClock:
    def __init__(self):
        self.t = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


@respx.mock
def test_jupiter_api_sends_the_key_only_when_configured():
    route = respx.get(JUP + "/tokens/v2/search").mock(return_value=httpx.Response(200, json=[]))
    with httpx.Client() as http:
        api(http, api_key="k-123").get("/tokens/v2/search", params={"query": "A"})
        api(http).get("/tokens/v2/search", params={"query": "A"})
    assert route.calls[0].request.headers["x-api-key"] == "k-123"
    assert "x-api-key" not in route.calls[1].request.headers


@respx.mock
def test_jupiter_api_spaces_calls_to_respect_the_rate_limit():
    respx.get(JUP + "/tokens/v2/search").mock(return_value=httpx.Response(200, json=[]))
    clock = FakeClock()
    with httpx.Client() as http:
        gate = JupiterApi(http, min_interval=2.0, clock=clock, sleep=clock.sleep)
        gate.get("/tokens/v2/search")
        clock.t += 0.5
        gate.get("/tokens/v2/search")
    assert clock.slept == [pytest.approx(1.5)]  # the first call never waits


def test_jupiter_api_default_spacing_follows_the_tier():
    with httpx.Client() as http:
        assert JupiterApi(http).min_interval >= 2.0  # keyless: 0.5 request per second
        assert 1.0 <= JupiterApi(http, api_key="k").min_interval < 2.0  # free key: 1 per second


@respx.mock
def test_jupiter_api_waits_and_retries_a_429():
    route = respx.get(JUP + "/swap/v2/order").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200, json={"outAmount": "1"})])
    clock = FakeClock()
    with httpx.Client() as http:
        resp = JupiterApi(http, min_interval=0, clock=clock, sleep=clock.sleep).get("/swap/v2/order")
    assert resp.json() == {"outAmount": "1"} and route.call_count == 2 and clock.slept == [3.0]


@respx.mock
def test_jupiter_api_gives_up_after_repeated_429s():
    route = respx.get(JUP + "/swap/v2/order").mock(return_value=httpx.Response(429))
    clock = FakeClock()
    with httpx.Client() as http, pytest.raises(httpx.HTTPStatusError):
        JupiterApi(http, min_interval=0, clock=clock, sleep=clock.sleep, retries=2).get("/swap/v2/order")
    assert route.call_count == 3


@respx.mock
def test_jupiter_execute_posts_to_swap_v2():
    route = respx.post(JUP + "/swap/v2/execute").mock(return_value=httpx.Response(200, json={"status": "Success"}))
    with httpx.Client() as http:
        assert Jupiter(api(http)).execute("SIGNED", "r1") == {"status": "Success"}
    assert json.loads(route.calls[0].request.content) == {"signedTransaction": "SIGNED", "requestId": "r1"}

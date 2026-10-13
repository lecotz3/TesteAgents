import httpx
import pytest
import respx

from desk.memes.sources import DexScreener, Jupiter, RugCheck, parse_pair, parse_safety
from tests.factories import NOW
from tests.meme_factories import MINT, USDC, raw_pair, raw_rugcheck

DEX = "https://api.dexscreener.com"
RUG = "https://api.rugcheck.xyz"
JUP = "https://lite-api.jup.ag"


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


def test_parse_safety_excludes_pool_from_top_holders_and_weights_lp_lock():
    s = parse_safety(raw_rugcheck())
    assert s.top10_pct == pytest.approx(20.0)  # the AMM pool (30%) is liquidity, not a whale
    assert s.lp_locked_pct == pytest.approx(100 * 107484 / (107484 + 2000))
    assert s.insider_pct == pytest.approx(3.0)
    assert s.decimals == 6 and s.mint_authority is None and s.danger_risks == ()


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


@respx.mock
def test_jupiter_quote_reads_amounts_and_impact():
    respx.get(JUP + "/ultra/v1/order").mock(return_value=httpx.Response(200, json={
        "inAmount": "3000000", "outAmount": "2000000000", "priceImpactPct": "-0.015", "feeBps": 10,
        "transaction": None, "requestId": "r1", "errorCode": None}))
    with httpx.Client() as http:
        q = Jupiter(http).quote(USDC, MINT, 3_000_000)
    assert q.out_amount == 2_000_000_000 and q.price_impact_pct == pytest.approx(1.5) and q.fee_bps == 10


@respx.mock
def test_jupiter_quote_without_route_is_none():
    respx.get(JUP + "/ultra/v1/order").mock(return_value=httpx.Response(200, json={
        "errorCode": 1, "errorMessage": "No routes found", "outAmount": None}))
    with httpx.Client() as http:
        assert Jupiter(http).quote(USDC, MINT, 3_000_000) is None

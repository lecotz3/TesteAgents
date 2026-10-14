"""The biggest moves in the memecoin market right now: what fomo.family shows as "Em alta". fomo has no public API,
so this reads the same tokens from Jupiter's public lists (most traded, trending, most organic), which the cycle
already downloads, and links each one to its fomo page. Tracking only: nothing here buys."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from desk.config import MemesSettings
from desk.memes.models import Pair
from desk.memes.rules import market_rejection


@dataclass(frozen=True)
class Mover:
    mint: str
    symbol: str
    name: str
    age_hours: float
    price: float
    mcap: float
    liquidity: float
    holders: int
    organic_score: float | None
    change_5m: float
    change_1h: float
    change_24h: float
    volume_1h: float
    traders_1h: int
    verdict: str | None  # why the robot's market filter would skip it, or None when it passes

    @property
    def link(self) -> str:
        return f"https://fomo.family/tokens/solana/{self.mint}"

    def as_dict(self) -> dict:
        return {**asdict(self), "link": self.link}


def _created(item: dict) -> datetime | None:
    raw = (item.get("firstPool") or {}).get("createdAt") or item.get("createdAt")
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_mover(item: dict, now: datetime, s: MemesSettings) -> Mover | None:
    """One Jupiter list item, or None when it lacks what the panel needs."""
    try:
        h1, m5, d1 = item["stats1h"], item.get("stats5m") or {}, item.get("stats24h") or {}
        created = _created(item)
        buys, sells = int(h1.get("numBuys") or 0), int(h1.get("numSells") or 0)
        pair = Pair(
            mint=str(item["id"] or ""), symbol=str(item.get("symbol") or "?"), name=str(item.get("name") or "?"),
            pair_address="", dex="", price_usd=float(item.get("usdPrice") or 0), liquidity_usd=float(item.get("liquidity") or 0),
            market_cap_usd=float(item.get("mcap") or 0), volume_h1_usd=float(h1.get("buyVolume") or 0) + float(h1.get("sellVolume") or 0),
            buys_h1=buys, sells_h1=sells, change_h1_pct=float(h1.get("priceChange") or 0),
            change_h6_pct=float((item.get("stats6h") or {}).get("priceChange") or 0), created_at=created,
            has_socials=bool(item.get("twitter")), url="",
            organic_score=float(item["organicScore"]) if isinstance(item.get("organicScore"), (int, float)) else None,
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if not pair.mint:
        return None
    return Mover(
        mint=pair.mint, symbol=pair.symbol, name=pair.name, age_hours=pair.age_minutes(now) / 60 if created else 0.0,
        price=pair.price_usd, mcap=pair.market_cap_usd, liquidity=pair.liquidity_usd,
        holders=int(item.get("holderCount") or 0), organic_score=pair.organic_score,
        change_5m=float(m5.get("priceChange") or 0), change_1h=pair.change_h1_pct,
        change_24h=float(d1.get("priceChange") or 0), volume_1h=pair.volume_h1_usd,
        traders_1h=int(h1.get("numTraders") or 0), verdict=market_rejection(pair, now, s),
    )


def biggest_moves(items: list[dict], now: datetime, s: MemesSettings) -> list[Mover]:
    """Young tokens only (memecoins, not majors), one per mint, the largest 1h moves up or down first."""
    seen: dict[str, Mover] = {}
    young = timedelta(days=s.movers_max_age_days)
    for item in items:
        m = parse_mover(item, now, s) if isinstance(item, dict) else None
        if m and m.mint not in seen and 0 < m.age_hours <= young.total_seconds() / 3600:
            seen[m.mint] = m
    return sorted(seen.values(), key=lambda m: abs(m.change_1h), reverse=True)[: s.movers_keep]


def day_peaks(rows: list[dict], limit: int = 15) -> list[dict]:
    """Per token: its biggest 1h move in `rows` (oldest first or not), the market cap then, and the latest one seen."""
    by_mint: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: r["ts"]):
        peak = by_mint.get(r["mint"])
        if peak is None or r["change_1h"] > peak["peak_change_1h"]:
            by_mint[r["mint"]] = peak = {
                "mint": r["mint"], "symbol": r["symbol"], "peak_change_1h": r["change_1h"], "peak_at": r["ts"],
                "peak_mcap": r["mcap"], "peak_volume_1h": r["volume_1h"],
                "link": f"https://fomo.family/tokens/solana/{r['mint']}",
            }
        peak["mcap_now"], peak["last_seen"] = r["mcap"], r["ts"]
    return sorted(by_mint.values(), key=lambda p: p["peak_change_1h"], reverse=True)[:limit]

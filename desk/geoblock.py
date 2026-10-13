"""Polymarket's official geoblock check. Any doubt counts as blocked (fail-closed)."""
from __future__ import annotations

from dataclasses import dataclass

import httpx

GEOBLOCK_URL = "https://polymarket.com/api/geoblock"


@dataclass(frozen=True)
class GeoStatus:
    blocked: bool
    country: str
    region: str
    error: str | None = None


def check_geoblock(http: httpx.Client, url: str = GEOBLOCK_URL) -> GeoStatus:
    try:
        resp = http.get(url)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        return GeoStatus(True, "", "", f"não foi possível verificar o geobloqueio ({type(exc).__name__})")
    if not isinstance(data, dict) or not isinstance(data.get("blocked"), bool):
        return GeoStatus(True, "", "", "resposta inesperada da API de geobloqueio")
    return GeoStatus(data["blocked"], str(data.get("country") or ""), str(data.get("region") or ""))

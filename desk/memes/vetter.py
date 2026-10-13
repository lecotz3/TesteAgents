"""Claude as a veto: reputation and narrative checks the code cannot do. It never sizes or forces a trade."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Literal, Protocol

from pydantic import BaseModel, ValidationError

from desk.analyst.base import AnalystError
from desk.memes.models import Pair, Safety

PROMPT_PATH = Path(__file__).parent / "vet_prompt.md"
MAX_RED_FLAGS = 5
VET_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "confidence", "red_flags", "narrative", "summary"],
    "properties": {
        "verdict": {"type": "string", "enum": ["buy", "avoid"]},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "narrative": {"type": "string"},
        "summary": {"type": "string"},
    },
}


class Asker(Protocol):
    def ask(self, prompt: str, schema: dict) -> object: ...


class _VetIn(BaseModel):
    verdict: Literal["buy", "avoid"]
    confidence: Literal["low", "medium", "high"]
    red_flags: list[str]
    narrative: str
    summary: str


@dataclass(frozen=True)
class Vet:
    verdict: str
    confidence: str
    red_flags: tuple[str, ...]
    narrative: str
    summary: str

    @property
    def approves(self) -> bool:
        return self.verdict == "buy" and self.confidence != "low"

    def as_dict(self) -> dict:
        return {"confidence": self.confidence, "red_flags": list(self.red_flags), "narrative": self.narrative,
                "summary": self.summary}


def build_vet_prompt(pair: Pair, safety: Safety, now: datetime) -> str:
    return Template(PROMPT_PATH.read_text(encoding="utf-8")).safe_substitute(
        now=now.isoformat(timespec="minutes"), name=pair.name, symbol=pair.symbol, mint=pair.mint, url=pair.url,
        age_hours=f"{pair.age_minutes(now) / 60:.1f}", price=f"{pair.price_usd:.8g}",
        market_cap=f"{pair.market_cap_usd:,.0f}", liquidity=f"{pair.liquidity_usd:,.0f}",
        volume_h1=f"{pair.volume_h1_usd:,.0f}", buys=pair.buys_h1, sells=pair.sells_h1,
        change_h1=f"{pair.change_h1_pct:+.1f}", change_h6=f"{pair.change_h6_pct:+.1f}",
        top10=f"{safety.top10_pct:.1f}", insiders=f"{safety.insider_pct:.1f}", creator=f"{safety.creator_pct:.1f}",
        lp_locked=f"{safety.lp_locked_pct:.0f}",
    )


class MemeVetter:
    def __init__(self, asker: Asker):
        self._asker = asker

    def vet(self, pair: Pair, safety: Safety, now: datetime) -> Vet:
        reply = self._asker.ask(build_vet_prompt(pair, safety, now), VET_SCHEMA)
        try:
            parsed = _VetIn.model_validate(reply)
        except ValidationError as exc:
            raise AnalystError(f"veredito inválido ({exc.error_count()} erros)") from exc
        return Vet(parsed.verdict, parsed.confidence, tuple(f.strip() for f in parsed.red_flags[:MAX_RED_FLAGS]),
                   parsed.narrative.strip(), parsed.summary.strip())

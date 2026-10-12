"""Analyst contract shared by the Claude Code and Codex backends."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Literal, Mapping, Protocol

from pydantic import BaseModel, ValidationError, field_validator

from desk.models import Analysis, Evidence, Market

HERE = Path(__file__).parent
SCHEMA_PATH = HERE / "schema.json"
PROMPT_PATH = HERE / "prompt.md"
POSTMORTEM_PATH = HERE / "postmortem.md"
SECRET_PREFIXES = ("POLY_", "DESK_", "TELEGRAM_")
# With an API key in the environment the CLIs bill the API instead of the user's subscription.
API_KEY_VARS = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"}
MAX_EVIDENCE = 5


class AnalystError(Exception):
    """The analyst could not produce a valid estimate."""


class Analyst(Protocol):
    name: str

    def estimate(self, market: Market, now: datetime) -> Analysis: ...

    def postmortem(self, summary: str) -> str: ...


class _EvidenceIn(BaseModel):
    claim: str
    url: str
    published: str


class _AnalysisIn(BaseModel):
    fair_prob: float
    official_resolution_source: str | None
    confidence: Literal["low", "medium", "high"]
    evidence: list[_EvidenceIn]
    exit_condition: str
    reasoning: str

    @field_validator("fair_prob")
    @classmethod
    def _unit_interval(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("fair_prob must be within [0, 1]")
        return value


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def build_estimate_prompt(market: Market, now: datetime) -> str:
    template = Template(PROMPT_PATH.read_text(encoding="utf-8"))
    return template.safe_substitute(
        now=now.isoformat(timespec="minutes"),
        question=market.question,
        outcome0=market.outcomes[0],
        outcome1=market.outcomes[1],
        end_date=market.end_date.isoformat(timespec="minutes"),
        declared_source=market.resolution_source or "(not declared)",
        rules=market.description,
    )


def build_postmortem_prompt(summary: str) -> str:
    return Template(POSTMORTEM_PATH.read_text(encoding="utf-8")).safe_substitute(summary=summary)


def parse_analysis(data: object, *, market_id: str, backend: str, now: datetime) -> Analysis:
    try:
        parsed = _AnalysisIn.model_validate(data)
    except ValidationError as exc:
        raise AnalystError(f"JSON de análise inválido ({exc.error_count()} erros)") from exc
    source = (parsed.official_resolution_source or "").strip() or None
    return Analysis(
        market_id=market_id,
        fair_prob=parsed.fair_prob,
        official_resolution_source=source,
        confidence=parsed.confidence,
        evidence=tuple(Evidence(e.claim, e.url, e.published) for e in parsed.evidence[:MAX_EVIDENCE]),
        exit_condition=parsed.exit_condition.strip(),
        reasoning=parsed.reasoning.strip(),
        created_at=now,
        backend=backend,
    )


def clean_env(env: Mapping[str, str]) -> dict[str, str]:
    """Environment for the analyst subprocess: everything except the desk's secrets and API keys."""
    return {k: v for k, v in env.items() if not k.upper().startswith(SECRET_PREFIXES) and k.upper() not in API_KEY_VARS}

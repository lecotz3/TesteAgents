"""Settings (config.toml) and secrets (.env / environment)."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal, Mapping

from pydantic import BaseModel, Field, SecretStr, ValidationError, model_validator


class ConfigError(Exception):
    """Invalid or unsafe configuration."""


class ScanSettings(BaseModel):
    max_markets_scanned: int = Field(1000, gt=0)
    min_liquidity_usd: float = Field(5000.0, ge=0)
    min_hours_to_resolution: float = Field(2.0, ge=0)
    price_band: tuple[float, float] = (0.03, 0.97)
    skip_categories: list[str] = Field(default_factory=list)
    allow_categories: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_band(self) -> "ScanSettings":
        low, high = self.price_band
        if not 0.0 < low < high < 1.0:
            raise ValueError("price_band must satisfy 0 < low < high < 1")
        return self


class AnalysisSettings(BaseModel):
    analyst: Literal["claude", "codex"] = "claude"
    claude_model: str = "sonnet"
    codex_model: str = ""
    analyses_per_cycle: int = Field(6, ge=0)
    max_analyses_per_day: int = Field(150, ge=0)  # hard ceiling on analyst calls in any 24h window
    analysis_ttl_hours: float = Field(3.0, gt=0)
    entry_analysis_max_age_minutes: int = Field(20, ge=0)  # older analyses are redone before buying
    held_reanalysis_minutes: int = Field(60, gt=0)
    analyst_failure_cooldown_minutes: int = Field(30, ge=0)
    analyst_timeout_seconds: int = Field(300, gt=0)
    min_confidence: Literal["low", "medium", "high"] = "medium"


class RiskSettings(BaseModel):
    min_edge_pp: float = Field(8.0, gt=0)
    max_exit_slippage: float = Field(0.03, ge=0)
    kelly_fraction: float = Field(0.5, gt=0, le=1)
    max_position_pct: float = Field(0.06, gt=0, le=1)
    max_open_positions: int = Field(5, ge=1)
    drawdown_halt: float = Field(0.40, gt=0, lt=1)


class ExitSettings(BaseModel):
    exit_edge_closed_pp: float = Field(1.0, ge=0)
    exit_fair_change_pp: float = Field(5.0, gt=0)
    exit_hours_before_resolution: float = Field(1.0, ge=0)
    reentry_cooldown_hours: float = Field(6.0, ge=0)  # no buying back a market right after selling it


class MonthlySettings(BaseModel):
    subscription_cost_usd: float = Field(20.0, ge=0)
    monthly_reserve_usd: float = Field(50.0, ge=0)


class Settings(BaseModel):
    mode: Literal["paper", "live"] = "paper"
    bankroll_start_usd: float = Field(100.0, gt=0)
    state_dir: Path = Path("state")
    scan: ScanSettings = Field(default_factory=ScanSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    exits: ExitSettings = Field(default_factory=ExitSettings)
    monthly: MonthlySettings = Field(default_factory=MonthlySettings)


def load_settings(path: Path) -> Settings:
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"arquivo de configuração não encontrado: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config.toml inválido: {exc}") from exc
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"config.toml inválido: {exc}") from exc


class Secrets(BaseModel):
    poly_private_key: SecretStr | None = None
    poly_wallet: str | None = None  # Polymarket wallet that holds the funds; None = the key's Deposit Wallet
    desk_live: str | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def merged_env(env_file: Path) -> dict[str, str]:
    """Values from the .env file, overridden by the real environment."""
    return {**read_env_file(env_file), **os.environ}


def load_secrets(env: Mapping[str, str]) -> Secrets:
    def get(name: str) -> str | None:
        value = (env.get(name) or "").strip()
        return value or None

    return Secrets(
        poly_private_key=get("POLY_PRIVATE_KEY"),
        poly_wallet=get("POLY_WALLET"),
        desk_live=get("DESK_LIVE"),
        telegram_bot_token=get("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=get("TELEGRAM_CHAT_ID"),
    )

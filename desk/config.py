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


class EconomySettings(BaseModel):
    """The desk pays to exist: rent every day and a fee per analyst call, out of its own cash."""

    daily_rent_usd: float | None = Field(None, ge=0)  # None = monthly.subscription_cost_usd / 30
    cost_per_analysis_usd: float = Field(0.01, ge=0)
    max_daily_burn_pct: float = Field(0.015, gt=0, le=1)  # rent + analyses in 24h, as a share of equity


class MemesSettings(BaseModel):
    """Memecoin agent (Solana). Same spirit as the desk: hard filters in code, small sizes, it pays to exist."""

    enabled: bool = True
    mode: Literal["paper", "live"] = "paper"
    auto_trade: bool = True  # False = alerts only: it sends approved tokens and you decide (option B)
    notify_paper_trades: bool = True
    bankroll_start_usd: float = Field(100.0, gt=0)
    # market filters (DexScreener)
    min_liquidity_usd: float = Field(50_000.0, ge=0)
    min_age_minutes: float = Field(60.0, ge=0)
    max_age_hours: float = Field(72.0, gt=0)
    min_volume_h1_usd: float = Field(25_000.0, ge=0)
    min_txns_h1: int = Field(200, ge=0)
    min_buy_ratio_h1: float = Field(0.45, ge=0, le=1)
    max_change_h1_pct: float = 150.0
    min_change_h1_pct: float = -15.0
    min_market_cap_usd: float = Field(150_000.0, ge=0)
    max_market_cap_usd: float = Field(25_000_000.0, gt=0)
    min_organic_score: float = Field(50.0, ge=0, le=100)  # Jupiter's real-trading score (unknown = not checked)
    # discovery beyond DexScreener: Jupiter's most traded / most organic / trending lists
    jup_discovery: bool = True
    jup_categories: list[str] = Field(default_factory=lambda: ["toptraded", "toporganicscore", "toptrending"])
    jup_interval: Literal["5m", "1h", "6h", "24h"] = "1h"
    # safety filters (RugCheck)
    min_lp_locked_pct: float = Field(80.0, ge=0, le=100)
    max_top10_holders_pct: float = Field(30.0, ge=0, le=100)
    max_insider_pct: float = Field(10.0, ge=0, le=100)
    max_creator_pct: float = Field(5.0, ge=0, le=100)
    # execution (Jupiter)
    position_pct: float = Field(0.03, gt=0, le=1)
    min_trade_usd: float = Field(1.0, gt=0)
    max_positions: int = Field(4, ge=1)
    max_round_trip_loss: float = Field(0.05, gt=0, lt=1)
    max_price_impact_pct: float = Field(2.0, gt=0)
    max_candidates_checked: int = Field(8, ge=1)  # RugCheck/Jupiter calls per cycle stay polite
    max_price_mismatch_pct: float = Field(25.0, gt=0)  # quote vs DexScreener price: catches stale data or decimals
    network_fee_usd: float = Field(0.002, ge=0)  # per transaction, paper estimate
    min_sol_balance: float = Field(0.01, ge=0)  # live: SOL kept in the wallet for network fees
    rpc_url: str = "https://api.mainnet-beta.solana.com"
    rejection_block_hours: float = Field(6.0, ge=0)  # do not re-check a rejected token before this
    # exits
    stop_loss_pct: float = Field(30.0, gt=0, lt=100)
    take_profit_pct: float = Field(100.0, gt=0)
    take_profit_fraction: float = Field(0.5, gt=0, le=1)
    trailing_stop_pct: float = Field(30.0, gt=0, lt=100)
    trailing_activation_pct: float = Field(20.0, ge=0)
    max_hold_hours: float = Field(24.0, gt=0)
    liquidity_drain_pct: float = Field(50.0, gt=0, lt=100)
    reentry_cooldown_hours: float = Field(24.0, ge=0)
    # X (free: the post or profile the token links to, read through the public FxTwitter API)
    x_enabled: bool = True
    x_require_link: bool = True  # no X link at all: skip (no community, no narrative)
    x_min_narrative_views: int = Field(5000, ge=0)  # the linked post needs these views...
    x_min_narrative_likes: int = Field(50, ge=0)  # ...or these likes (old posts have no view count)
    x_min_profile_followers: int = Field(100, ge=0)  # the token's own X account
    x_api_url: str = "https://api.fxtwitter.com"
    # the agent pays to exist
    analyst: Literal["claude", "none"] = "claude"
    cost_per_vet_usd: float = Field(0.01, ge=0)
    max_vets_per_day: int = Field(30, ge=0)
    max_vets_per_cycle: int = Field(2, ge=0)  # each check takes minutes; the next cycle's exits must not wait
    vet_block_hours: float = Field(24.0, ge=0)
    daily_rent_usd: float = Field(0.0, ge=0)
    drawdown_halt: float = Field(0.40, gt=0, lt=1)
    daily_loss_pause_pct: float = Field(15.0, ge=0, lt=100)  # lost this much in 24h: no new buys (0 = off)
    # what paper has to show before real money (docs/PENDENCIAS.md, D8)
    readiness_min_days: int = Field(14, ge=0)
    readiness_min_trades: int = Field(30, ge=0)


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
    economy: EconomySettings = Field(default_factory=EconomySettings)
    memes: MemesSettings = Field(default_factory=MemesSettings)

    @property
    def daily_rent(self) -> float:
        rent = self.economy.daily_rent_usd
        return self.monthly.subscription_cost_usd / 30 if rent is None else rent

    @model_validator(mode="after")
    def _no_codex_with_money(self) -> "Settings":
        # Codex's read-only sandbox can still read local files (the .env with the wallet key), and market rules are
        # attacker-controlled text; the Claude backend only gets WebSearch/WebFetch.
        if self.mode == "live" and self.analysis.analyst == "codex":
            raise ValueError('analyst = "codex" não é permitido com mode = "live"; use analyst = "claude"')
        return self


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
    memes_private_key: SecretStr | None = None  # dedicated Solana wallet for the memecoin agent (base58)
    memes_live: str | None = None
    memes_rpc_url: SecretStr | None = None  # optional private RPC (its URL usually carries an API key)


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
        memes_private_key=get("MEMES_PRIVATE_KEY"),
        memes_live=get("MEMES_LIVE"),
        memes_rpc_url=get("MEMES_RPC_URL"),
    )

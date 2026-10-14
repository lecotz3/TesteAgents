from pathlib import Path

import pytest

from desk.config import ConfigError, load_secrets, load_settings, merged_env, read_env_file

REPO = Path(__file__).resolve().parents[1]


def test_repo_config_uses_guide_values():
    s = load_settings(REPO / "config.toml")
    assert s.mode == "paper"
    assert s.bankroll_start_usd == 100.0
    assert s.scan.min_liquidity_usd == 5000.0
    assert s.scan.min_hours_to_resolution == 2.0
    assert s.risk.min_edge_pp == 8.0
    assert s.risk.max_exit_slippage == 0.03
    assert s.risk.kelly_fraction == 0.5
    assert s.risk.max_position_pct == 0.06
    assert s.risk.max_open_positions == 5
    assert s.risk.drawdown_halt == 1.0 and s.memes.drawdown_halt == 1.0  # the owner's choice: switched off only when broke
    assert s.exits.exit_fair_change_pp == 5.0
    assert s.exits.exit_hours_before_resolution == 1.0
    assert s.analysis.analyst == "claude"
    assert s.analysis.claude_model == "sonnet"


def test_missing_sections_fall_back_to_defaults(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('mode = "paper"\n', encoding="utf-8")
    s = load_settings(cfg)
    assert s.risk.min_edge_pp == 8.0
    assert s.analysis.analyses_per_cycle == 6


def test_invalid_price_band_is_rejected(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[scan]\nprice_band = [0.9, 0.1]\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(cfg)


def test_missing_config_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "nope.toml")


def test_env_file_parsing_skips_comments_and_strips_quotes(tmp_path):
    env = tmp_path / ".env"
    env.write_text('# comment\nPOLY_WALLET="0xabc"\nEMPTY=\nTELEGRAM_CHAT_ID=42\n', encoding="utf-8")
    assert read_env_file(env) == {"POLY_WALLET": "0xabc", "EMPTY": "", "TELEGRAM_CHAT_ID": "42"}


def test_real_environment_overrides_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DESK_LIVE=no\n", encoding="utf-8")
    monkeypatch.setenv("DESK_LIVE", "I_UNDERSTAND")
    assert merged_env(env)["DESK_LIVE"] == "I_UNDERSTAND"


def test_secrets_never_show_private_key():
    secrets = load_secrets({"POLY_PRIVATE_KEY": "0xsupersecret", "TELEGRAM_BOT_TOKEN": "zz-telegram-secret-zz"})
    assert secrets.poly_private_key.get_secret_value() == "0xsupersecret"
    assert "0xsupersecret" not in repr(secrets)
    assert "zz-telegram-secret-zz" not in repr(secrets)
    assert secrets.poly_wallet is None
    assert secrets.desk_live is None


def test_wallet_address_comes_from_poly_wallet():
    assert load_secrets({"POLY_WALLET": " 0xabc "}).poly_wallet == "0xabc"


def test_codex_is_refused_in_live_mode(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('mode = "live"\n[analysis]\nanalyst = "codex"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="codex"):
        load_settings(cfg)


def test_jupiter_key_is_optional_and_hidden():
    secrets = load_secrets({"MEMES_JUPITER_API_KEY": " jup-secret-key "})
    assert secrets.memes_jupiter_api_key.get_secret_value() == "jup-secret-key"
    assert "jup-secret-key" not in repr(secrets)
    assert load_secrets({}).memes_jupiter_api_key is None


def test_jupiter_gateway_defaults_to_api_jup_ag(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("", encoding="utf-8")
    assert load_settings(cfg).memes.jup_api_url == "https://api.jup.ag"  # lite-api.jup.ag is being retired

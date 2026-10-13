import json
import subprocess
from pathlib import Path

import pytest

from desk.analyst import make_analyst
from desk.analyst.base import AnalystError, build_estimate_prompt, clean_env, load_schema, parse_analysis
from desk.analyst.claude_cli import ClaudeCliAnalyst
from desk.analyst.codex_cli import CodexCliAnalyst
from desk.config import AnalysisSettings
from tests.factories import NOW, make_market

VALID = {
    "fair_prob": 0.74,
    "official_resolution_source": "NWS Central Park",
    "confidence": "medium",
    "evidence": [{"claim": "c", "url": "https://x", "published": "2026-10-12"}] * 7,
    "exit_condition": "Exit if forecast < 60%",
    "reasoning": "r",
}


class FakeRunner:
    def __init__(self, stdout="", returncode=0, stderr="", raises=None, writes=None):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr
        self.raises, self.writes = raises, writes
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.raises:
            raise self.raises
        if self.writes is not None:
            Path(args[args.index("-o") + 1]).write_text(self.writes, encoding="utf-8")
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, self.stderr)


def claude_stdout(structured, is_error=False):
    return json.dumps({
        "type": "result", "subtype": "success", "is_error": is_error,
        "result": json.dumps(structured), "structured_output": structured,
    })


def test_prompt_has_rules_but_not_price():
    m = make_market(outcome_prices=(0.4321, 0.5679))
    prompt = build_estimate_prompt(m, NOW)
    assert m.question in prompt and m.description in prompt
    assert "0.4321" not in prompt and "43.21" not in prompt


def test_prompt_keeps_special_characters_in_rules():
    rules = "Pays $100 if {team} wins — São Paulo ✓ $outcome0"
    assert rules in build_estimate_prompt(make_market(description=rules), NOW)


def test_parse_analysis_truncates_evidence_and_normalizes_source():
    a = parse_analysis({**VALID, "official_resolution_source": "  "}, market_id="1", backend="t", now=NOW)
    assert len(a.evidence) == 5
    assert a.official_resolution_source is None
    assert a.created_at == NOW


@pytest.mark.parametrize("bad", [
    {**VALID, "fair_prob": 1.5},
    {**VALID, "confidence": "certain"},
    {k: v for k, v in VALID.items() if k != "exit_condition"},
    "not a dict",
])
def test_parse_analysis_rejects_invalid(bad):
    with pytest.raises(AnalystError):
        parse_analysis(bad, market_id="1", backend="t", now=NOW)


def test_schema_is_strict():
    schema = load_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_clean_env_drops_secrets():
    env = {"POLY_PRIVATE_KEY": "k", "DESK_LIVE": "x", "TELEGRAM_BOT_TOKEN": "t", "MEMES_PRIVATE_KEY": "m", "PATH": "p"}
    assert clean_env(env) == {"PATH": "p"}


def test_claude_estimate_runs_sandboxed(monkeypatch):
    monkeypatch.setenv("POLY_PRIVATE_KEY", "secret-key")
    runner = FakeRunner(stdout=claude_stdout(VALID))
    a = ClaudeCliAnalyst(model="sonnet", timeout=60, runner=runner).estimate(make_market(), NOW)
    assert a.fair_prob == 0.74 and a.backend == "claude"
    args, kwargs = runner.calls[0]
    assert args[1] == "-p"
    assert "--safe-mode" in args and "--json-schema" in args and "--no-session-persistence" in args
    assert args[args.index("--tools") + 1] == "WebSearch,WebFetch"
    assert args[args.index("--model") + 1] == "sonnet"
    assert "POLY_PRIVATE_KEY" not in kwargs["env"]
    assert "Will it rain" in kwargs["input"]
    assert kwargs["timeout"] == 60


@pytest.mark.parametrize("runner", [
    FakeRunner(stdout=claude_stdout(VALID, is_error=True)),
    FakeRunner(stdout="not json"),
    FakeRunner(stdout=json.dumps({"is_error": False, "result": "x"})),
    FakeRunner(returncode=1, stderr="usage limit reached"),
    FakeRunner(raises=subprocess.TimeoutExpired("claude", 60)),
    FakeRunner(raises=FileNotFoundError("claude")),
])
def test_claude_failures_become_analyst_errors(runner):
    with pytest.raises(AnalystError):
        ClaudeCliAnalyst(model="sonnet", timeout=60, runner=runner).estimate(make_market(), NOW)


def test_claude_postmortem_returns_text_without_tools():
    runner = FakeRunner(stdout=json.dumps({"is_error": False, "result": " texto "}))
    assert ClaudeCliAnalyst(model="sonnet", timeout=60, runner=runner).postmortem("resumo") == "texto"
    args, kwargs = runner.calls[0]
    assert args[args.index("--tools") + 1] == ""
    assert "--json-schema" not in args
    assert "resumo" in kwargs["input"]


def test_codex_estimate_reads_output_file(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    runner = FakeRunner(writes=json.dumps(VALID))
    a = CodexCliAnalyst(model="", timeout=60, runner=runner).estimate(make_market(), NOW)
    args, kwargs = runner.calls[0]
    assert args[1] == "exec" and "--output-schema" in args and args[-1] == "-"
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "web_search=live" in args
    assert "-m" not in args
    assert "TELEGRAM_BOT_TOKEN" not in kwargs["env"]
    assert a.backend == "codex" and a.fair_prob == 0.74


def test_codex_without_output_file_raises():
    with pytest.raises(AnalystError):
        CodexCliAnalyst(model="gpt-5", timeout=60, runner=FakeRunner()).estimate(make_market(), NOW)


def test_make_analyst_picks_backend():
    assert make_analyst(AnalysisSettings()).name == "claude"
    assert make_analyst(AnalysisSettings(analyst="codex")).name == "codex"


def test_clean_env_drops_api_keys_so_the_subscription_is_used():
    env = {"ANTHROPIC_API_KEY": "a", "ANTHROPIC_AUTH_TOKEN": "b", "OPENAI_API_KEY": "c", "PATH": "p"}
    assert clean_env(env) == {"PATH": "p"}


def test_prompt_forbids_looking_up_market_odds():
    prompt = build_estimate_prompt(make_market(), NOW)
    assert "Polymarket" in prompt and "Kalshi" in prompt and "odds" in prompt


def test_claude_ask_returns_structured_output_for_any_schema(monkeypatch):
    monkeypatch.setenv("MEMES_PRIVATE_KEY", "wallet-key")
    schema = {"type": "object", "properties": {"verdict": {"type": "string"}}}
    runner = FakeRunner(stdout=claude_stdout({"verdict": "avoid"}))
    out = ClaudeCliAnalyst(model="sonnet", timeout=60, runner=runner).ask("pergunta", schema)
    assert out == {"verdict": "avoid"}
    args, kwargs = runner.calls[0]
    assert json.loads(args[args.index("--json-schema") + 1]) == schema
    assert args[args.index("--tools") + 1] == "WebSearch,WebFetch"
    assert "MEMES_PRIVATE_KEY" not in kwargs["env"] and kwargs["input"] == "pergunta"


def test_claude_ask_without_structured_output_fails():
    runner = FakeRunner(stdout=json.dumps({"is_error": False, "result": "x"}))
    with pytest.raises(AnalystError):
        ClaudeCliAnalyst(model="sonnet", timeout=60, runner=runner).ask("p", {"type": "object"})

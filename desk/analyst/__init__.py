"""Analyst backends: Claude Code (default) or Codex."""
from __future__ import annotations

from desk.analyst.base import Analyst, AnalystError
from desk.analyst.claude_cli import ClaudeCliAnalyst
from desk.analyst.codex_cli import CodexCliAnalyst
from desk.config import AnalysisSettings

__all__ = ["Analyst", "AnalystError", "ClaudeCliAnalyst", "CodexCliAnalyst", "make_analyst"]


def make_analyst(s: AnalysisSettings) -> Analyst:
    if s.analyst == "codex":
        return CodexCliAnalyst(model=s.codex_model, timeout=s.analyst_timeout_seconds)
    return ClaudeCliAnalyst(model=s.claude_model, timeout=s.analyst_timeout_seconds)

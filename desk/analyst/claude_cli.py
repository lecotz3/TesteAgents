"""Analyst backed by Claude Code in headless mode (`claude -p`), using the user's subscription."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from typing import Callable

from desk.analyst.base import (
    AnalystError,
    build_estimate_prompt,
    build_postmortem_prompt,
    clean_env,
    load_schema,
    parse_analysis,
)
from desk.models import Analysis, Market

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flash when run by the Windows scheduler
RESEARCH_TOOLS = "WebSearch,WebFetch"

Runner = Callable[..., subprocess.CompletedProcess]


class ClaudeCliAnalyst:
    name = "claude"

    def __init__(self, model: str, timeout: int, runner: Runner = subprocess.run, binary: str = "claude"):
        self._model = model
        self._timeout = timeout
        self._run = runner
        self._binary = shutil.which(binary) or binary

    def build_args(self, *, schema: dict | None, tools: str) -> list[str]:
        # --safe-mode (not --bare): --bare only accepts ANTHROPIC_API_KEY and ignores the subscription login.
        args = [
            self._binary, "-p",
            "--output-format", "json",
            "--model", self._model,
            "--tools", tools,
            "--permission-mode", "dontAsk",
            "--safe-mode",
            "--strict-mcp-config",
            "--no-session-persistence",
        ]
        if tools:
            args += ["--allowedTools", tools]
        if schema is not None:
            args += ["--json-schema", json.dumps(schema)]
        return args

    def _invoke(self, prompt: str, args: list[str]) -> dict:
        with tempfile.TemporaryDirectory(prefix="desk-analyst-", ignore_cleanup_errors=True) as workdir:
            try:
                proc = self._run(
                    args, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=self._timeout, cwd=workdir, env=clean_env(os.environ), creationflags=NO_WINDOW,
                )
            except subprocess.TimeoutExpired as exc:
                raise AnalystError(f"claude excedeu {self._timeout}s") from exc
            except OSError as exc:
                raise AnalystError(f"não foi possível iniciar o claude: {exc}") from exc
        if proc.returncode != 0:
            raise AnalystError(f"claude saiu com código {proc.returncode}: {(proc.stderr or '').strip()[-300:]}")
        try:
            data = json.loads(proc.stdout)
        except ValueError as exc:
            raise AnalystError("claude devolveu saída que não é JSON") from exc
        if not isinstance(data, dict) or data.get("is_error"):
            subtype = data.get("subtype") if isinstance(data, dict) else "?"
            raise AnalystError(f"claude reportou erro ({subtype})")
        return data

    def estimate(self, market: Market, now: datetime) -> Analysis:
        args = self.build_args(schema=load_schema(), tools=RESEARCH_TOOLS)
        data = self._invoke(build_estimate_prompt(market, now), args)
        structured = data.get("structured_output")
        if structured is None:
            raise AnalystError("claude não devolveu structured_output")
        return parse_analysis(structured, market_id=market.id, backend=self.name, now=now)

    def postmortem(self, summary: str) -> str:
        data = self._invoke(build_postmortem_prompt(summary), self.build_args(schema=None, tools=""))
        return str(data.get("result") or "").strip()

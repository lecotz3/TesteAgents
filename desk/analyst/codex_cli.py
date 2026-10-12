"""Analyst backed by the OpenAI Codex CLI in non-interactive mode (`codex exec`)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
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

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

Runner = Callable[..., subprocess.CompletedProcess]


class CodexCliAnalyst:
    name = "codex"

    def __init__(self, model: str, timeout: int, runner: Runner = subprocess.run, binary: str = "codex"):
        self._model = model
        self._timeout = timeout
        self._run = runner
        self._binary = shutil.which(binary) or binary

    def build_args(self, *, schema_path: Path | None, out_path: Path) -> list[str]:
        args = [
            self._binary, "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--sandbox", "read-only",
            "-c", "web_search=live",
            "-o", str(out_path),
        ]
        if schema_path is not None:
            args += ["--output-schema", str(schema_path)]
        if self._model:
            args += ["-m", self._model]
        args.append("-")  # prompt comes from stdin
        return args

    def _invoke(self, prompt: str, with_schema: bool) -> str:
        with tempfile.TemporaryDirectory(prefix="desk-analyst-", ignore_cleanup_errors=True) as workdir:
            out_path = Path(workdir) / "last_message.txt"
            schema_path = None
            if with_schema:
                schema_path = Path(workdir) / "schema.json"
                schema_path.write_text(json.dumps(load_schema()), encoding="utf-8")
            args = self.build_args(schema_path=schema_path, out_path=out_path)
            try:
                proc = self._run(
                    args, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=self._timeout, cwd=workdir, env=clean_env(os.environ), creationflags=NO_WINDOW,
                )
            except subprocess.TimeoutExpired as exc:
                raise AnalystError(f"codex excedeu {self._timeout}s") from exc
            except OSError as exc:
                raise AnalystError(f"não foi possível iniciar o codex: {exc}") from exc
            if proc.returncode != 0:
                raise AnalystError(f"codex saiu com código {proc.returncode}: {(proc.stderr or '').strip()[-300:]}")
            if not out_path.exists():
                raise AnalystError("codex não escreveu a resposta final")
            return out_path.read_text(encoding="utf-8")

    def estimate(self, market: Market, now: datetime) -> Analysis:
        text = self._invoke(build_estimate_prompt(market, now), with_schema=True)
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise AnalystError("codex devolveu resposta que não é JSON") from exc
        return parse_analysis(data, market_id=market.id, backend=self.name, now=now)

    def postmortem(self, summary: str) -> str:
        return self._invoke(build_postmortem_prompt(summary), with_schema=False).strip()

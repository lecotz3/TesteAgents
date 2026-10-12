# Polymarket Desk

Robô que varre o Polymarket a cada 10 minutos, usa **Claude Code** (ou Codex) como analista para estimar probabilidades e opera só quando a distorção passa de 8pp, com ½ Kelly, teto de 6% por trade e parada em 40% de drawdown. Começa em **paper** (simulação no livro real, com taxas).

- **O que depende de você** (credenciais, decisões, como obter cada uma): [`docs/PENDENCIAS.md`](docs/PENDENCIAS.md)
- Guia de uso: [`docs/GUIA.md`](docs/GUIA.md)
- Design: [`docs/superpowers/specs/2026-10-12-polymarket-desk-design.md`](docs/superpowers/specs/2026-10-12-polymarket-desk-design.md)
- VPS: [`deploy/VPS.md`](deploy/VPS.md)

```powershell
uv sync
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m desk cycle
```

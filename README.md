# Polymarket Desk

Robô que varre o Polymarket a cada 10 minutos, usa **Claude Code** (ou Codex) como analista para estimar probabilidades e opera só quando a distorção passa de 8pp, com ½ Kelly, teto de 6% por trade e parada em 40% de drawdown. Ele **paga para existir** (aluguel diário + custo por análise) e é desligado se não lucrar mais do que gasta. Começa em **paper** (simulação no livro real, com taxas).

Um segundo agente, de **memecoins na Solana** (os tokens do fomo.family), compra e vende sozinho a cada 3 minutos: filtros de mercado e de segurança no código, cotações reais da Jupiter, o Claude só veta, stops e realização de lucro por regra. Também começa em paper e também paga para existir. Detalhes: [`docs/GUIA.md`](docs/GUIA.md#memecoins-solana) e [`docs/PENDENCIAS.md`](docs/PENDENCIAS.md) (D8 e C4).

- **O que depende de você** (credenciais, decisões, como obter cada uma): [`docs/PENDENCIAS.md`](docs/PENDENCIAS.md)
- **Painel:** duplo clique em `scripts\dashboard.cmd` → http://127.0.0.1:8765
- Guia de uso: [`docs/GUIA.md`](docs/GUIA.md)
- Design: [`docs/superpowers/specs/2026-10-12-polymarket-desk-design.md`](docs/superpowers/specs/2026-10-12-polymarket-desk-design.md)
- VPS: [`deploy/VPS.md`](deploy/VPS.md)

```powershell
uv sync
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m desk cycle
.venv\Scripts\python -m desk memes
```

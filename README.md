# Polymarket Desk

Robô que varre o Polymarket a cada 10 minutos, usa **Claude Code** (ou Codex) como analista para estimar probabilidades e opera só quando a distorção passa de 8pp, com ½ Kelly, teto de 6% por trade. Ele **paga para existir** (aluguel diário + custo por análise) e é desligado quando o dinheiro acaba: precisa lucrar mais do que gasta. Começa em **paper** (simulação no livro real, com taxas).

Um segundo agente, de **memecoins na Solana** (os tokens do fomo.family), compra e vende sozinho a cada 3 minutos: descobre no DexScreener e nas listas da Jupiter, filtra mercado, segurança e o que o token mostra no X (sem custo), confere a cotação real na Jupiter, o Claude só veta, e vende por stops, realização de lucro ou se o post do X sumir. Também começa em paper e também paga para existir. Detalhes: [`docs/GUIA.md`](docs/GUIA.md#memecoins-solana) e [`docs/PENDENCIAS.md`](docs/PENDENCIAS.md) (D8 e C4).

- **O que depende de você** (credenciais, decisões, como obter cada uma): [`docs/PENDENCIAS.md`](docs/PENDENCIAS.md)
- **Painel:** http://127.0.0.1:8765 (sempre ligado depois do `scripts\install_task.ps1`; atalho "Painel dos robos" na área de trabalho)
- Guia de uso: [`docs/GUIA.md`](docs/GUIA.md)
- Design: [`docs/superpowers/specs/2026-10-12-polymarket-desk-design.md`](docs/superpowers/specs/2026-10-12-polymarket-desk-design.md)
- VPS: [`deploy/VPS.md`](deploy/VPS.md)

```powershell
uv sync
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m desk cycle
.venv\Scripts\python -m desk memes
```

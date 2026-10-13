# Polymarket Desk — design

**Data:** 2026-10-12
**Origem:** guia "How to Build a Self-Funding Grok Agent That Trades Polymarket", adaptado para Claude Code (principal) e Codex (alternativo) no lugar do Grok Bot.

## 1. Objetivo e contexto

Construir um robô ("desk") que, a cada 10 minutos, varre os mercados ativos do Polymarket, estima uma probabilidade justa para os candidatos usando um LLM com busca na web, entra apenas quando a distorção é grande e o livro permite sair, dimensiona com ½ Kelly sob tetos rígidos, registra tudo em `trades.txt` e para sozinho após 40% de drawdown.

**Decisões do usuário:**

| Tema | Decisão |
|---|---|
| Modo | Paper primeiro; real depois, atrás de flag |
| Onde roda | PC Windows agora (Agendador de Tarefas); código portátil para VPS Linux depois |
| Cérebro | Assinatura Claude Pro/Max via `claude -p` (padrão); ChatGPT Plus/Pro via `codex exec` (alternativo) |
| Sentimento | Só busca na web (sem X/xAI) |
| Jurisdição | Usuário declara operar de país permitido; modo real protegido por checagem oficial de geobloqueio |
| Abordagem | A — código determinístico no comando; LLM só como analista |
| Avisos | Telegram (opcional) |

**Fatos verificados que o guia original ignora:**

- Todos os mercados cobram taxa *taker*: `taxa = cotas × feeRate × p × (1 − p)`; makers não pagam. Cada mercado traz `feeSchedule {rate, exponent, takerOnly}` na Gamma API.
- Ordem mínima de **5 cotas** (`orderMinSize`) e tick de 0,01 (`orderPriceMinTickSize`) em todos os mercados amostrados.
- Gamma API devolve no máximo 100 mercados por página.
- Brasil está em *close-only* (site e API) na lista oficial de geobloqueio; `polymarket.com` não resolve DNS na rede atual do usuário. Paper funciona (só usa `gamma-api` e `clob`, que resolvem).
- O exemplo do próprio guia (preço 0,62, justo 0,74, banca US$50 → US$3 = 4,8 cotas) cai abaixo do mínimo de 5 cotas. Por isso a banca paper padrão é **US$100**.

**Não-objetivos:** leitura do X; execução pelo navegador; qualquer saque automático; market making; contornar geobloqueio.

## 2. Arquitetura

Projeto Python (≥3.11) na raiz do repositório, pacote `desk`. Estado em `state/` (fora do git).

```
pyproject.toml            # deps: httpx, pydantic; extra [live]: py-clob-client; dev: pytest, respx
config.toml               # todos os parâmetros (versionado, valores do guia)
.env.example              # chaves do modo real e Telegram (o .env real é ignorado pelo git)
desk/
  __main__.py, cli.py     # python -m desk {cycle,status,monthly,resume,live-check,analyze}
  config.py               # carrega config.toml + env; valida com pydantic
  models.py               # Market, OrderBook, Analysis, Position, Fill, Decision
  gamma.py                # varredura paginada da Gamma API → Market
  clob.py                 # /book por token; simulação de compra/venda percorrendo níveis
  fees.py                 # taxa por cota a partir do feeSchedule
  screener.py             # filtros + escolha de quem analisar no ciclo
  sizing.py               # Kelly com taxa, ½ Kelly, tetos, ordem mínima, tick
  exits.py                # regras de saída e de liquidação na resolução
  store.py                # SQLite: positions, trades, analyses, equity, flags
  journal.py              # trades.txt legível
  notify.py               # Telegram ou só log
  geoblock.py             # GET https://polymarket.com/api/geoblock (fail-closed)
  lock.py                 # trava de arquivo contra ciclos sobrepostos
  cycle.py                # orquestra um ciclo
  analyst/
    base.py               # interface Analyst.estimate(market) -> Analysis; postmortem(...)
    claude_cli.py         # subprocess `claude -p`
    codex_cli.py          # subprocess `codex exec`
    prompt.md             # prompt de estimativa
    postmortem.md         # prompt de post-mortem / pivô
    schema.json           # JSON Schema da Analysis
  executor/
    base.py               # Executor.buy(...) / sell(...) -> Fill
    paper.py              # preenchimento simulado no livro real, com taxas
    live.py               # py-clob-client, ordens FOK
scripts/install_task.ps1, uninstall_task.ps1
deploy/crontab, deploy/VPS.md
docs/GUIA.md              # guia de uso em português
tests/                    # pytest, sem rede (fixtures gravadas)
state/                    # desk.db, trades.txt, desk.log, desk.lock (gitignored)
```

Cada módulo tem uma responsabilidade e é testável isoladamente. `cycle.py` é o único que conhece todos; os demais dependem só de `models.py` e de suas próprias dependências diretas.

## 3. Configuração padrão (`config.toml`)

| Chave | Padrão | Origem |
|---|---|---|
| `mode` | `"paper"` | decisão |
| `bankroll_start_usd` | `100` | ajuste por ordem mínima |
| `max_markets_scanned` | `1000` | guia |
| `min_liquidity_usd` | `5000` | guia |
| `min_hours_to_resolution` | `2` | guia |
| `price_band` | `[0.03, 0.97]` | novo (evita extremos) |
| `skip_categories` / `allow_categories` | `[]` / `[]` (vazio = todas) | guia (lista de pulo) |
| `analyses_per_cycle` | `6` | novo (cota do plano) |
| `analysis_ttl_hours` | `3` | novo |
| `held_reanalysis_minutes` | `60` | novo |
| `analyst_failure_cooldown_minutes` | `30` | novo |
| `min_confidence` | `"medium"` | novo |
| `min_edge_pp` | `8` | guia |
| `max_exit_slippage` | `0.03` | guia |
| `kelly_fraction` | `0.5` | guia |
| `max_position_pct` | `0.06` | guia |
| `max_open_positions` | `5` | guia |
| `drawdown_halt` | `0.40` | guia |
| `exit_edge_closed_pp` | `1` | interpretação de "mispricing closed" |
| `exit_fair_change_pp` | `5` | guia |
| `exit_hours_before_resolution` | `1` | guia |
| `analyst` | `"claude"` | decisão (`"claude"` ou `"codex"`) |
| `claude_model` | `"sonnet"` | decisão |
| `codex_model` | `""` (padrão do Codex) | decisão |
| `analyst_timeout_seconds` | `300` | novo |
| `subscription_cost_usd` | `20` | guia (ajustável) |
| `monthly_reserve_usd` | `50` | guia |

Segredos só via ambiente/`.env`: `POLY_PRIVATE_KEY`, `POLY_FUNDER`, `POLY_SIGNATURE_TYPE`, `DESK_LIVE`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

## 4. O ciclo (`python -m desk cycle`)

Executado a cada 10 min pelo agendador. Adquire `state/desk.lock` (trava considerada velha após 30 min); se não conseguir, sai sem fazer nada.

1. **Liquidação.** Posição cujo mercado a Gamma mostra como `closed` com `outcomePrices` em 0/1 é liquidada a 1 ou 0 (paper). No modo real, registra e avisa para resgatar manualmente.
2. **Marcação a mercado.** Para cada posição aberta, busca o livro do token e avalia pelo melhor *bid* (se o livro falhar, usa o preço de entrada). `equity = caixa + Σ valor`. Grava em `equity`; atualiza o pico. Repetida no fim do ciclo.
3. **Drawdown.** Se `(pico − equity) / pico ≥ drawdown_halt` e não estiver `halted`: liga `halted`, pede post-mortem ao analista (§6), anexa ao `trades.txt`, avisa. Enquanto `halted`, nenhuma entrada nova; saídas continuam. Só `python -m desk resume` desliga — e redefine o pico para a equity atual (senão pararia de novo no ciclo seguinte).
4. **Saídas** (antes de entradas). Posições abertas têm prioridade de reanálise a cada `held_reanalysis_minutes`, fora do orçamento de mercados novos. Vende tudo se qualquer uma:
   - vantagem atual do lado mantido `< exit_edge_closed_pp` (distorção fechou);
   - `|fair_prob_atual − fair_prob_entrada| > exit_fair_change_pp`;
   - faltam `< exit_hours_before_resolution` horas para `endDate`.
   Saída que falha por falta de liquidez é tentada de novo no próximo ciclo.
5. **Varredura.** Gamma `markets?active=true&closed=false`, paginada (100/página) até `max_markets_scanned`. Filtros: `enableOrderBook` e `acceptingOrders`; `liquidityNum ≥ min_liquidity_usd`; horas até `endDate ≥ min_hours_to_resolution`; preço do primeiro resultado dentro de `price_band`; categoria não pulada; descrição (regras) não vazia; sem posição aberta no mesmo mercado.
   *Categoria:* `feeType.split("_fees")[0]` (ex.: `politics_fees` → `politics`, `sports_fees_nfl_cfb_oct26` → `sports`, `economics_fees` → `economics`); `feeType` nulo → `other`. Valores observados na API em 2026-10-12.
6. **Análise.** Candidatos sem análise válida (idade `< analysis_ttl_hours`) e fora de *cooldown* são ordenados por `volume24hr` desc.; analisa os primeiros `analyses_per_cycle`. Pula se `halted` ou se já há `max_open_positions`.
7. **Decisão de entrada** para cada mercado com análise válida, `confidence ≥ min_confidence` e `official_resolution_source` não nulo:
   - **Pré-filtro barato:** só busca livros se `max(q − preço_gamma)` entre os dois lados for maior que `min_edge_pp − 3` pp (preços da Gamma; evita centenas de chamadas ao CLOB por ciclo).
   - `p` = `fair_prob` do resultado 0; lado SIM usa token 0 com `q = p`; lado NÃO usa token 1 com `q = 1 − p`.
   - Busca o livro do token; `ask` = melhor *ask*. `vantagem = q − ask − taxa_por_cota(ask)`. Escolhe o lado de maior vantagem; segue só se `vantagem × 100 > min_edge_pp`.
   - **Tamanho** (§5) → `n` cotas. Se `n < orderMinSize`, pula com motivo registrado.
   - **Livro:** simula compra de `n` cotas percorrendo os *asks* → preço médio `P̄`, limite `P_lim` (pior nível tocado). Simula venda de `n` cotas percorrendo os *bids* → preço médio `B̄`. `slippage_saida = (melhor_bid − B̄) / melhor_bid`. Segue só se `≤ max_exit_slippage`. Recalcula a vantagem com `P̄`; se cair abaixo do mínimo, pula.
   - **Executa** via executor (§7) com preço limite `P_lim`, FOK.
   - **Registra** posição + trade no SQLite e um bloco no `trades.txt` com: mercado, lado, `fair_prob`, entrada, cotas, custo, taxa, condição de saída do analista, fonte oficial, motivo/evidências. A condição de saída é obrigatória: sem ela, não entra.
8. Libera a trava; escreve uma linha de resumo no `desk.log`. Três ciclos seguidos com exceção → aviso.

## 5. Dimensionamento (`sizing.py`)

Para comprar um token a preço `P` com probabilidade estimada `q` e taxa por cota `φ(P) = rate × (P × (1 − P))^exponent` (exponent padrão 1, conforme docs):

```
P' = P + φ(P)                         # custo efetivo por cota
f* = (q − P') / (1 − P')              # = fórmula do guia: b=(1−P')/P', f=(b·q−(1−q))/b
f  = max(0, f*) × kelly_fraction
stake = min(f, max_position_pct) × equity, limitado ao caixa disponível
n  = floor(stake / P' ) com 2 casas decimais de cota
```

`n` é calculado primeiro com `P` = melhor *ask*. Depois da simulação no livro (§4, passo 7), recalcula uma vez com `P = P̄`: se `n × (P̄ + φ(P̄))` exceder o teto, reduz `n` para caber. Sem entrada se `f* ≤ 0`, se `posições_abertas ≥ max_open_positions` ou se `n < orderMinSize`. O exemplo do guia (P=0,62, q=0,74, sem taxa) dá `f* ≈ 0,316`, `f = 0,158`, teto 6% vence — teste unitário fixa esses números.

## 6. Analista (`analyst/`)

**Interface:** `estimate(market: Market) -> Analysis` e `postmortem(summary: str) -> str`.

**Entrada do prompt:** pergunta, resultados, descrição completa (regras de resolução), `resolutionSource` declarado, `endDate`, data/hora atual. **Sem o preço atual** (evita ancoragem). O prompt manda: identificar a fonte oficial que decide; buscar na web notícias e dados verificáveis recentes; pesar credibilidade e recência; ignorar promoção paga; responder só o JSON.

**Saída (`schema.json`):**

```json
{
  "fair_prob": 0.0,                         // probabilidade do resultado 0, em [0,1]
  "official_resolution_source": "string|null",
  "confidence": "low|medium|high",
  "evidence": [{"claim": "", "url": "", "published": ""}],   // até 5
  "exit_condition": "string",
  "reasoning": "string"                     // curto
}
```

Validação com pydantic; inválido → falha registrada e *cooldown*.

**Backend Claude** (`claude_cli.py`):

```
claude -p <prompt> --output-format json --json-schema <schema>
       --model <claude_model> --tools WebSearch,WebFetch
       --allowedTools WebSearch WebFetch --permission-mode dontAsk
       --safe-mode --strict-mcp-config --no-session-persistence
```

- `--safe-mode` (não `--bare`, que exige `ANTHROPIC_API_KEY` e não usa a assinatura).
- Executado em diretório temporário vazio, com ambiente **sem** `POLY_*`, `DESK_*` e `TELEGRAM_*`.
- Timeout `analyst_timeout_seconds`; resultado lido do campo estruturado do JSON de saída.

**Backend Codex** (`codex_cli.py`): `codex exec` com `--output-schema`, sandbox somente leitura, busca web habilitada e mesmo ambiente limpo. Flags exatas confirmadas na implementação contra `codex exec --help`; testado de verdade só se o Codex estiver instalado.

**Post-mortem:** ao entrar em `halted`, o analista recebe o resumo dos trades (do SQLite) e devolve texto com: o que deu errado, quais categorias performaram melhor/pior e qual categoria tem as regras de resolução mais claras e os dados mais previsíveis agora (o "pivô" do guia). O robô só **sugere**; a mudança é feita pelo usuário em `allow_categories`/`skip_categories`.

## 7. Execução

**Paper (`executor/paper.py`):** preenche percorrendo o livro real no momento da ordem até `n` cotas sem ultrapassar `P_lim` (FOK: se não houver profundidade, não preenche). Taxa por nível = `cotas × rate × p × (1 − p)`, debitada do caixa. Venda simétrica nos *bids*.

**Real (`executor/live.py`):** só ativo se `mode = "live"` **e** `DESK_LIVE=I_UNDERSTAND`. Antes de cada ordem: `geoblock.check()` → se `blocked` verdadeiro, erro de rede ou resposta inesperada, aborta (fail-closed) e avisa. Usa `py-clob-client` (`ClobClient(host, key, chain_id=137, signature_type, funder)`, credenciais L2 derivadas), ordem a mercado `FOK` com preço-limite `P_lim` (compra: valor em USDC com 2 casas; venda: cotas com 2 casas — exigência de precisão do CLOB para FOK). Preenchimento confirmado pela resposta; falha → nenhuma posição registrada, aviso. Carteira dedicada contendo só a banca. Não existe função de saque.

`python -m desk live-check`: verifica geobloqueio, credenciais e saldo USDC; não envia ordens.

## 8. Autofinanciamento (`python -m desk monthly`)

Agendado diariamente, mas só relata uma vez por mês (chave `last_monthly_report` no SQLite; `--force` ignora) — assim funciona mesmo se o PC estiver desligado no dia 1º. Se `equity ≥ subscription_cost_usd + monthly_reserve_usd`: avisa que cobre a assinatura, saque sugerido = `subscription_cost_usd`, máximo mantendo a reserva = `min(caixa, equity − monthly_reserve_usd)`. Senão, avisa o déficit. Em paper, a mensagem é marcada `[PAPER]`. Também anota no `trades.txt`. Quem saca é sempre o usuário.

## 9. Erros

| Falha | Tratamento |
|---|---|
| Gamma/CLOB fora/timeout | etapa pulada, log; ciclo segue com o que tiver |
| Livro vazio ou desatualizado | mercado pulado |
| Analista: timeout, JSON inválido, cota esgotada | mercado em *cooldown*; log; após 3 falhas seguidas no ciclo, para de analisar no ciclo |
| Ordem real recusada/parcial | nada registrado (FOK); aviso |
| Geobloqueio indisponível/bloqueado | sem ordens reais; aviso |
| Exceção no ciclo | log com stack; 3 ciclos seguidos → aviso |
| Trava presa | considerada velha após 30 min |

## 10. Testes

`pytest`, sem rede (respx para HTTP; subprocess do analista simulado):

- `fees`: fórmula oficial em pontos conhecidos (p=0,5, rate 0,05 → 0,0125/cota).
- `sizing`: exemplo do guia; taxa reduz `f`; teto 6%; caixa; mínimo de 5 cotas; tick.
- `clob`: percorrer níveis, preço médio, `P_lim`, slippage de saída, livro raso.
- `screener`: cada filtro; ordenação; orçamento; categoria via `feeType`.
- `exits`: as três regras; liquidação 0/1.
- `store` + `cycle`: drawdown liga `halted`, bloqueia entradas, mantém saídas, `resume` libera; ciclo completo com analista e executor falsos e fixtures gravadas reais da Gamma/CLOB.
- `analyst`: parsing do JSON de saída do `claude -p`; montagem dos argumentos; ambiente sem segredos.
- `executor/live`: cliente simulado; geobloqueio fail-closed; flag dupla.
- `journal`: bloco do `trades.txt` contém todos os campos obrigatórios.

Verificação manual: `python -m desk analyze <slug>` roda o analista real em um mercado; `python -m desk cycle` real em paper.

## 11. Operação

- **Windows:** `scripts/install_task.ps1` cria a tarefa "PolymarketDesk" a cada 10 min (`.venv\Scripts\python.exe -m desk cycle`, diretório do repo, só com usuário logado — o `claude` precisa das credenciais do usuário) e a tarefa mensal do dia 1º.
- **VPS:** `deploy/crontab` + `deploy/VPS.md` (instalar Python, Claude Code e login, clonar, `.env`, cron).
- **Acompanhamento:** `python -m desk status` mostra equity, pico, drawdown, posições, últimas análises e trades. `docs/GUIA.md` traz o checklist das primeiras 48h do guia original (conferir 3 mercados à mão, tamanho ≤ 6%, condição de saída antes da entrada) e a seção "Quando dá errado".

## 12. Escopo desta rodada

Entregue e verificado: modo paper ponta a ponta rodando no PC; analista Claude real; agendador Windows; guia.
Entregue com testes simulados, sem uso real: executor real, backend Codex (real se instalado), arquivos de VPS.

## 13. Riscos conhecidos

- A "vantagem" do guia vinha do X; com busca web, a vantagem real é incerta — o paper existe para medir isso.
- Limites da assinatura podem esgotar com muitos ciclos; `analyses_per_cycle` e `analysis_ttl_hours` controlam o consumo.
- Estimativas de LLM tendem a excesso de confiança; filtros de confiança, 8pp, ½ Kelly e 6% mitigam, não eliminam.
- O valor de US$5.273 do guia é uma alegação de marketing, não uma expectativa.

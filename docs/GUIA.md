# Guia do Polymarket Desk

Versão do guia "Self-Funding Grok Agent That Trades Polymarket" com **Claude Code** (ou Codex) no lugar do Grok Bot.

## O que mudou em relação ao guia original

| Guia original (Grok Bot) | Aqui |
|---|---|
| Bot decide tudo sozinho no chat | Código Python aplica as regras; o Claude só estima probabilidades |
| Lê o X em tempo real | Busca na web (sem X) — a "vantagem" do guia fica menor |
| Computador na nuvem do Grok | Seu PC (Agendador de Tarefas) agora; VPS depois (`deploy/VPS.md`) |
| Opera pelo navegador logado | Paper simula no livro real; real usa a API oficial (CLOB) |
| Ignora taxas e ordem mínima | Desconta a taxa taker de cada mercado e respeita o mínimo de 5 cotas |
| Saque automático por rotina | Relatório mensal; **quem saca é você** |

Os US$5.273 do guia são uma alegação de marketing. O modo paper existe para descobrir, com dados, se a estratégia tem vantagem **antes** de arriscar dinheiro.

## Instalação (Windows)

1. Pré-requisitos: [uv](https://docs.astral.sh/uv/) e Claude Code logado (`claude` abre e responde).
2. Na raiz do repositório: `uv sync`
3. Testes: `.venv\Scripts\python -m pytest -q`

## Primeiros comandos

```powershell
.venv\Scripts\python -m desk analyze <slug-ou-id>   # o Claude analisa 1 mercado, sem operar
.venv\Scripts\python -m desk cycle                  # roda 1 ciclo completo (paper)
.venv\Scripts\python -m desk status                 # banca, posições, últimos trades
```

O slug é o final da URL do mercado (`polymarket.com/event/<slug>`) — ou use o `id` da Gamma API.
O registro legível fica em `state\trades.txt`; o log técnico em `state\desk.log`.

Um ciclo analisa até 6 mercados novos (≈1–3 min cada com busca na web). Ciclos lentos não se sobrepõem: a trava pula o próximo disparo.

## Deixar rodando sozinho

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1     # liga (a cada 10 min)
powershell -ExecutionPolicy Bypass -File scripts\uninstall_task.ps1   # desliga
```

A tarefa roda só com sua sessão do Windows aberta (o `claude` usa seu login). O PC não pode dormir — ajuste em Configurações > Energia.

## Painel

Dê duplo clique em `scripts\dashboard.cmd`, ou rode `.venv\Scripts\python -m desk dashboard`. O painel abre em http://127.0.0.1:8765 e se atualiza sozinho a cada 30s. Ele mostra:
- equity e linha de desligamento;
- ganho operando contra custo de existir;
- fôlego em dias;
- o que o Claude concluiu sobre cada mercado e por que o robô entrou ou pulou;
- posições, trades, ciclos, diário e log.

É somente leitura e só abre neste computador.

## O robô paga para existir

Todo dia ele paga um "aluguel" (a assinatura rateada), e cada análise do Claude custa `cost_per_analysis_usd`. Os dois saem do caixa dele. O gasto diário tem teto de `max_daily_burn_pct` da equity. Se as perdas somadas aos custos derrubarem a banca até a linha de drawdown, ele é **desligado** (ver "Parada por drawdown"). Os detalhes e os ajustes estão em `docs/PENDENCIAS.md`, item D7.

## As primeiras 48 horas (do guia original)

Abra `state\trades.txt` a cada poucas horas e confira:

1. **As distorções são reais?** Abra 3 mercados em que o robô entrou e veja se você concorda com o valor justo.
2. **O tamanho está certo?** Toda entrada deve custar ≤ 6% da banca. Se aparecer 15%, algo quebrou.
3. **Tem condição de saída antes da entrada?** Toda ENTRADA traz "Condição de saída". Sem isso é aposta, não trade.

Depois de 48h de registros limpos, confira uma vez por dia com `python -m desk status`.

## Parâmetros (`config.toml`)

Os números do guia já vêm configurados: vantagem mínima de 8pp, ½ Kelly, máximo de 6% por trade, 5 posições, saída com no máximo 3% de slippage, liquidez ≥ US$5.000, ≥ 2h até a resolução, parada em 40% de drawdown.

- `analyses_per_cycle` e `analysis_ttl_hours` controlam quanto da sua cota do Claude é usada.
- `allow_categories` / `skip_categories` filtram por categoria (`weather`, `sports`, `politics`, `crypto`, `economics`, `other`…).
- `analyst = "codex"` troca o analista para o Codex (instale com `npm i -g @openai/codex` e faça login com `codex`). Só no paper: no modo real o robô exige o Claude, porque o Codex consegue ler arquivos locais (inclusive o `.env`).

## Quando dá errado

| Sintoma | O que fazer |
|---|---|
| Não acha nenhum trade | Normal: 8pp é exigente de propósito. Não baixe abaixo de 5 — é onde começa o ruído. |
| Achou mas não entrou | Veja `pulos[...]` no resumo do ciclo: livro raso, saída cara ou ordem mínima. Ele fez o certo. |
| Banca caindo sem perdas grandes | Taxas e spread. Suba `min_liquidity_usd` para 10000 e baixe `max_exit_slippage` para 0.02. |
| Opera mercados "no achismo" | O analista devolve `official_resolution_source = null` e o robô pula; para cortar a categoria, use `skip_categories`. |
| "analista falhou" no log | Limite do plano, timeout ou `claude` fora do PATH. O mercado entra em espera de 30 min; após 3 falhas o ciclo para de analisar. |
| Quer colocar mais dinheiro | Não. A condição de sobrevivência só funciona se a banca for real. |

## Desligamento por drawdown (o "pivô")

Ao cair 40% do pico, por perdas, por custos ou pelos dois, o robô é desligado: para de abrir posições, de analisar e de pagar aluguel (as saídas continuam), escreve um **post-mortem** no `trades.txt` com uma proposta de categoria mais previsível (o guia trocou cripto por clima) e te avisa. Leia, ajuste `allow_categories`/`skip_categories` se concordar, e rode:

```powershell
.venv\Scripts\python -m desk resume
```

## Avisos no celular (opcional)

1. No Telegram, fale com `@BotFather`, crie um bot e copie o token.
2. Mande qualquer mensagem para o seu bot e abra `https://api.telegram.org/bot<TOKEN>/getUpdates` para achar o `chat.id`.
3. Coloque `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` no `.env`.

## Modo real (só depois do paper provar algo)

1. Confirme que o Polymarket é permitido onde você mora **e** de onde o robô roda.
2. Crie uma conta Polymarket **dedicada** e deposite só a banca (US$100–150, vira pUSD; com menos, 6% da banca não chega à ordem mínima de 5 cotas e quase toda entrada é pulada). Faça 1 trade manual pequeno pelo site para liberar as aprovações. Nunca use sua carteira principal.
3. `uv sync --extra live`
4. Copie `.env.example` para `.env` e preencha `POLY_PRIVATE_KEY`, `POLY_WALLET` e `DESK_LIVE=I_UNDERSTAND` (passo a passo de como obter cada um em [`PENDENCIAS.md`](PENDENCIAS.md)). **Nunca cole essas chaves em chat nenhum** (nem com o Claude).
5. Em `config.toml`, troque `mode = "live"` e ajuste `bankroll_start_usd` para o saldo real. **Obrigatório:** renomeie a pasta `state` do paper (ex.: `state-paper`). O robô se recusa a rodar o modo real em cima do registro do paper.
6. `.venv\Scripts\python -m desk live-check` — precisa terminar com "PRONTO para modo real".
7. Acompanhe o primeiro trade real de perto.

Antes de cada ordem real o robô consulta o geobloqueio oficial; se a região estiver bloqueada (ou a checagem falhar), nada é enviado. Não existe função de saque no código. Mercados resolvidos precisam ser resgatados por você no site.

## Autofinanciamento

Todo mês, `python -m desk monthly` (agendado) diz se a banca cobre a assinatura (`subscription_cost_usd`) mais a reserva de US$50 e quanto dá para sacar. O saque é sempre manual.

## Memecoins (Solana)

Segundo agente, com banca, registro e diário próprios (`state\memes.db`, `state\memes.txt`) e a mesma lógica de "pagar para existir". Configuração na seção `[memes]` do `config.toml`.

| Etapa | Quem decide | O que acontece |
|---|---|---|
| Descoberta | DexScreener + Jupiter | Novidades do DexScreener (perfis, destaques pagos, comunidades) e as listas da Jupiter: mais negociados, mais orgânicos e em alta (~260 tokens por ciclo) |
| Filtro de mercado | Código | Liquidez, idade (1h a 72h), volume e negócios na última hora, pressão de compra, sem alta vertical, market cap e o **organic score** da Jupiter (nota de negociação real, não de bots) |
| X (sem custo) | Código + FxTwitter | Lê o post ou o perfil do X que o token divulga: views, likes e quem postou, ou seguidores e idade do perfil. Rejeita link apagado, narrativa fraca (poucas views/likes) e perfil sem seguidores. Os que passam são ordenados pelo alcance da narrativa |
| Segurança | Código + RugCheck | Sem poder de imprimir ou congelar, liquidez travada, holders espalhados, sem taxa de transferência |
| Custo real | Código + Jupiter | Ida e volta ≤ 5%, impacto ≤ 2%, existe rota de venda, preço da cotação bate com o DexScreener |
| Reputação | Claude (só veta) | Recebe os dados do X e busca o token pelo endereço na web e no X: golpe, cópia, imitação, conta comprada, post que não é sobre o token. Veta por sinal de alerta, não por falta de notícia (token de poucas horas quase nunca tem) |
| Compra | Código | 3% da banca, até 4 tokens ao mesmo tempo |
| Venda | Código | Stop -30%, metade ao dobrar, stop móvel de 30% do pico, liquidez caindo pela metade, 24h no máximo, e **vende tudo se o post/perfil do X do token for apagado** depois da compra |

Ele roda em dois ritmos. A cada **3 minutos**, o ciclo completo descobre, filtra, consulta o Claude e compra. A cada **1 minuto**, uma rodada leve só confere preços e regras de saída, sem descobrir nem consultar o Claude, para o stop não esperar uma checagem do Claude terminar. As duas nunca vendem a mesma posição ao mesmo tempo.

```powershell
.venv\Scripts\python -m desk memes             # um ciclo agora
.venv\Scripts\python -m desk memes-exits       # só as saídas agora
.venv\Scripts\python -m desk memes-status      # banca, posições, trades
.venv\Scripts\python -m desk memes-live-check  # confere carteira e saldos do modo real (não negocia)
.venv\Scripts\python -m desk memes-resume      # religa depois de desligado
.venv\Scripts\python -m desk memes-sell-all --yes  # emergência: vende tudo agora e pausa as compras
```

- **Paper:** cada compra e venda simulada usa a cotação exata que a Jupiter executaria naquele instante, mais uma estimativa da taxa da rede.
- **Real:** fica atrás de duas travas: `[memes] mode = "live"` e `MEMES_LIVE=I_UNDERSTAND`. A chave da carteira dedicada (`MEMES_PRIVATE_KEY`) só assina a parte dela em cada transação, nunca é registrada no log e nunca chega ao Claude. Passo a passo: `docs/PENDENCIAS.md`, C4.
- **Proteções do modo real** (todas testadas com respostas simuladas; a rede de verdade só entra quando você ligar):
  - A compra é recusada se a ordem final da Jupiter vier mais de 3% pior que a cotação aprovada. Isso pega preço que correu durante a checagem do Claude e a troca "gasless" (sem SOL na carteira), que num teste real cobrou 10% de taxa.
  - Resposta perdida: o agente pergunta de novo à Jupiter por até 90 s, com a mesma transação assinada (ela nunca executa duas vezes). Se ainda ficar incerta, ele confere a carteira por 10 min: compra que entrou vira posição com stop; venda que saiu é registrada pelo preço da tentativa.
  - Aluguel: cada token novo prende ~0,0015 SOL numa conta da carteira. De hora em hora ele fecha as contas que as vendas deixaram vazias, e o SOL volta.
  - Avisa no Telegram se faltar SOL com posições abertas (sem SOL as vendas pequenas falham) e se ficou mais de 15 min sem rodar com posições abertas (PC dormindo).
  - `memes-live-check` pede à Jupiter uma troca real de US$1 para a sua carteira e a assina localmente **sem enviar**.
- **Jupiter:** usa `api.jup.ag` (o antigo `lite-api` está sendo desligado). Sem chave: 1 chamada a cada 2 s, o que já basta. Uma chave grátis em developers.jup.ag/portal (`MEMES_JUPITER_API_KEY` no `.env`) dobra isso.
- **Só alertas:** `auto_trade = false`. Ele manda os tokens aprovados no Telegram, com link do fomo.family, e você decide.
- **Paga para existir:** cada checagem do Claude custa US$0,01. Caiu 40% do pico, é desligado: para de comprar e de gastar, mas segue vendendo pelas regras.
- **Freio diário:** perdeu 15% da banca em 24h, pausa as compras até a queda das últimas 24h voltar abaixo disso (`daily_loss_pause_pct`). As vendas continuam.
- **Vender tudo agora:** `python -m desk memes-sell-all --yes` vende todas as posições a preço de mercado e pausa as compras até `memes-resume`. Sem o `--yes` ele só mostra o que venderia.
- **Parar na hora:** `enabled = false` na seção `[memes]` do `config.toml` para tudo (compras e vendas) a partir do próximo minuto. Para parar só as compras e manter as saídas, use `auto_trade = false`.
- **Prova em paper:** o painel ("Pronto p/ dinheiro real?"), o `memes-status`, o `memes-live-check` e o relatório mensal mostram quantos dias, trades fechados e qual resultado depois dos custos o paper já tem. Meta: 14 dias, 30 trades e lucro.
- **X sem custo, com limites:** a busca de menções no X é paga (a API oficial não tem plano grátis desde fev/2026). O código lê o que o próprio token divulga pelo FxTwitter (serviço gratuito, não oficial, que pode mudar) e o Claude procura menções pela busca da web. Se um dia quiser contagem de menções e autores, a fonte do X é uma interface (`XSource` em `desk/memes/x.py`): dá para plugar a API oficial ou o twitterapi.io sem mexer no resto.

## Regras para lembrar

- Nunca cole chave privada, seed ou senha em chat. Chaves só no `.env`.
- Comece com US$100–150 (menos que isso trava na ordem mínima). O robô precisa merecer operar mais.
- 8pp mínimo, 6% por trade, 5 posições, ½ Kelly sempre.
- Toda entrada tem condição de saída registrada antes.
- 40% de drawdown = parar e fazer post-mortem. Sem exceção.
- Mercado de previsão não é permitido em todo lugar. Confira sua jurisdição.

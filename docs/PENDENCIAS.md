# O que depende de você

Tudo que eu (Claude) não posso fazer por você: credenciais, dinheiro e decisões suas. Nada aqui é obrigatório para o modo **paper**, que já roda sozinho com o login do Claude Code que você já tem.

> Regra de ouro: **nunca cole chave privada, seed ou senha em chat nenhum** (nem comigo). Elas vão só no arquivo `.env`, na sua máquina, e o `.env` nunca vai para o git.

## Situação em 13/10/2026

| Item | Estado |
|---|---|
| Código | Pronto, 227 testes passando, na branch `feat/polymarket-desk` (PR #1 no GitHub) |
| Agendador do Windows | **Ligado.** `PolymarketDesk` roda um ciclo a cada 10 min (paper); `PolymarketDeskMonthly` roda todo dia e relata 1x por mês |
| Modo paper | Rodando sozinho: cada ciclo varre ~1000 mercados, ~470 passam no filtro, o Claude analisa até 3. Ainda sem entradas (nenhuma distorção ≥ 8pp) e sem erros |
| Economia | O robô paga para existir: aluguel diário (assinatura rateada) + US$0,01 por análise, saindo do caixa dele. Se não lucrar mais que isso, é **desligado** na linha de 40% |
| Painel | `scripts\dashboard.cmd` (duplo clique) ou `python -m desk dashboard` → http://127.0.0.1:8765 |
| Modo real | **Bloqueado deste PC**: `python -m desk live-check` responde "Geobloqueio: BLOQUEADO (ConnectError)" porque esta rede não alcança `polymarket.com` |

---

## 0. Ver o que está acontecendo

Dê duplo clique em `scripts\dashboard.cmd`. Ele abre o painel no navegador, que se atualiza a cada 30s e mostra:
- **Vida do robô:** equity, quanto ganhou operando, quanto custou existir, dias de fôlego até ser desligado e o gráfico contra a linha de desligamento.
- **O que o robô decidiu:** cada mercado que o Claude analisou, com preço, valor justo, vantagem e o motivo de ter entrado ou pulado.
- **Posições, trades e ciclos**, além do diário (`trades.txt`) e do log técnico.

O painel só lê dados e fica acessível apenas neste computador. Para fechá-lo, feche a janela preta.

O agendador já está ligado. Para conferir: `Get-ScheduledTask PolymarketDesk*`. Para desligar: `scripts\uninstall_task.ps1`. Para religar: `scripts\install_task.ps1`.

---

## Decisões (com minha recomendação)

### D1. Quanto da sua cota do Claude o robô pode usar
Cada ciclo analisa até `analyses_per_cycle` mercados novos (≈30–60 s cada, com busca na web), no mesmo plano que você usa no dia a dia. Deixei **3** (≈18 análises por hora), um meio-termo seguro. O guia original não fixa esse número.

- **Claude Pro:** baixe para `analyses_per_cycle = 2` em `config.toml`, senão você vai bater o limite de uso e o seu Claude Code pessoal também trava.
- **Claude Max:** pode subir até 6 (≈36 por hora), o que cobre mais mercados por dia.
- Teto diário: `max_analyses_per_day = 150` (em qualquer janela de 24h). Ao atingir, o robô para de analisar e só gerencia as saídas.
- Se o analista falhar em todas as chamadas por 3 ciclos seguidos, você recebe um aviso no Telegram.
- Sinal de exagero: `analista falhou` em `state\desk.log`, ou o seu Claude dizendo que atingiu o limite.
- Para pausar sem desinstalar, abra o Agendador de Tarefas e desative `PolymarketDesk`.

### D2. Quando passar do paper para o real
Recomendo exigir **tudo** isto antes:
1. Pelo menos 4 semanas de paper e pelo menos 30 trades fechados (`python -m desk status`).
2. Resultado positivo **depois** das taxas (a equity já desconta taxas).
3. Nenhuma entrada maior que 6% da banca.
4. Você abriu 5 trades ao acaso e concorda com o "valor justo" que o Claude estimou.

Se não passar, o robô não tem vantagem, e tudo bem: você descobriu isso sem perder dinheiro.

### D3. De onde o modo real vai rodar
Este PC não consegue nem consultar o geobloqueio do Polymarket: o nome `polymarket.com` não resolve nesta rede. Pode ser bloqueio do provedor, da rede corporativa ou do país. Por segurança, o robô **recusa** qualquer ordem real quando não consegue confirmar que a região é liberada.

Opções:
- **(a)** Rodar de uma rede doméstica num país onde o Polymarket é permitido para você.
- **(b)** Rodar numa VPS **no país onde você mora e pode operar legalmente** (passo a passo em `deploy/VPS.md`).

Não use VPN nem VPS em outro país para contornar bloqueio: isso viola os termos do Polymarket e pode congelar seus fundos. A lista oficial de países fica na página de restrições geográficas da documentação do Polymarket. Confira antes; ela mudou várias vezes em 2026.

### D4. Categorias de mercado
Hoje o robô olha todas as categorias. Se quiser foco (o guia original acabou migrando para **clima**), edite em `config.toml`:
```toml
allow_categories = ["weather", "economics"]   # só estas
skip_categories  = ["sports"]                 # nunca estas
```

### D5. Analista: Claude ou Codex
O padrão é o Claude (`analyst = "claude"`). Para usar sua assinatura do ChatGPT:
1. `npm i -g @openai/codex`
2. Rode `codex` uma vez e faça login.
3. Em `config.toml`, troque para `analyst = "codex"`.

O Codex vale só para o paper. No modo real o robô exige o Claude, porque o sandbox do Codex ainda lê arquivos locais (inclusive o `.env` com a chave da carteira).

### D6. Juntar a branch na `main`
Há um Pull Request aberto no GitHub (`lecotz3/TesteAgents`). Revise e dê **Merge** quando quiser. O robô roda da pasta local, então isso não muda nada no que já está rodando.

---

### D7. Quanto o robô paga para existir (`[economy]` no `config.toml`)
Cada ação custa dinheiro do próprio caixa do robô:
- **Aluguel diário:** a assinatura rateada (US$20/30 ≈ US$0,67/dia).
- **US$0,01 por análise:** cada "pensamento".
- **Teto de gasto:** 1,5% da equity por dia. Com US$100, são US$1,50/dia. Com pouco dinheiro, ele precisa escolher onde pensar.

Sem lucro, em ~33 dias ele cai 40% e é **desligado**. Desligado, ele para de operar, de analisar e de pagar aluguel, escreve o post-mortem e te avisa. Para religar: `python -m desk resume`.

Ajustes possíveis:
- **Modo difícil:** `cost_per_analysis_usd = 0.10` simula o preço de API.
- **Mais fôlego:** banca maior. Como o aluguel é fixo, ele pesa menos.
- **Valores fixos:** `daily_rent_usd` fixa o aluguel em vez de usar a assinatura.

Minha recomendação: deixe como está no paper. É exatamente o teste que importa, porque mostra se ele se paga.

No modo real, esses custos são contábeis: saem do caixa do robô no registro, não da carteira.

## Credenciais (só quando decidir usar)

### C1. Avisos no celular pelo Telegram (opcional, recomendado)
1. No Telegram, abra `@BotFather` → `/newbot` → escolha nome e usuário → copie o **token** (algo como `123456:ABC...`).
2. Mande qualquer mensagem ("oi") para o seu bot novo.
3. No navegador, abra `https://api.telegram.org/bot<TOKEN>/getUpdates` (troque `<TOKEN>`) e copie o número em `"chat":{"id": ...}`.
4. Copie `.env.example` para `.env` (se ainda não existir) e preencha:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=987654321
   ```
5. Pronto. Você recebe aviso de entradas, saídas, parada por drawdown, relatório mensal e falhas repetidas.

### C2. Carteira para o modo real (só depois da D2 e da D3)
O modo real usa o SDK oficial `polymarket-client` (Protocol V2, saldo em **pUSD**).

1. **Conta dedicada:** crie uma conta Polymarket **nova**, só para o robô (e-mail novo), a partir de um lugar onde o Polymarket é permitido. Nunca use sua carteira principal.
2. **Depósito:** deposite só a banca inicial (US$100–150; com menos, 6% da banca não chega à ordem mínima de 5 cotas e quase toda entrada é pulada). O Polymarket converte para pUSD.
3. **Aprovações:** faça **um** trade manual pequeno (US$1) pelo site. O site pede as aprovações de trading que o robô precisa.
4. **Chave privada (`POLY_PRIVATE_KEY`):**
   - Conta criada por e-mail: no site, menu do perfil → **Settings** → **Export Private Key**. Se não achar, a central de ajuda do Polymarket tem o artigo "How to export your private key".
   - Conta com MetaMask: no MetaMask, *Detalhes da conta* → *Mostrar chave privada* (da conta **dedicada**).
5. **Endereço da carteira (`POLY_WALLET`):** o endereço que guarda o saldo no Polymarket (aparece no perfil e na tela de depósito, começa com `0x`). Se deixar vazio, o SDK usa a Deposit Wallet padrão da chave.
6. Preencha o `.env`:
   ```
   POLY_PRIVATE_KEY=0x...
   POLY_WALLET=0x...
   DESK_LIVE=I_UNDERSTAND
   ```
7. Instale o extra e valide:
   ```powershell
   uv sync --extra live
   .venv\Scripts\python -m desk live-check
   ```
   Precisa terminar com **"PRONTO para modo real."** e mostrar o seu saldo.
8. Em `config.toml`: `mode = "live"` e `bankroll_start_usd` igual ao saldo real. **Obrigatório:** renomeie a pasta `state` para `state-paper`. O robô se recusa a usar o registro do paper no modo real.
9. Acompanhe o primeiro trade real de perto. O código do modo real foi testado com simulações, **nunca contra a exchange de verdade** (daqui não é possível). Por isso, comece com a banca mínima.

O robô **não tem função de saque**. Sacar e resgatar mercados resolvidos é sempre com você, pelo site.

### C3. Rodar numa VPS (opcional)
Na VPS não há navegador para logar no Claude. No **seu PC**, rode `claude setup-token` e copie o token gerado para a VPS como `CLAUDE_CODE_OAUTH_TOKEN=...` (detalhes em `deploy/VPS.md`, passo 3).

---

## Comandos do dia a dia

```powershell
.venv\Scripts\python -m desk status        # banca, posições, últimos trades
.venv\Scripts\python -m desk cycle         # força um ciclo agora
.venv\Scripts\python -m desk analyze <slug-ou-id>   # Claude analisa 1 mercado, sem operar
.venv\Scripts\python -m desk resume        # retoma depois de parada por drawdown (leia o post-mortem antes)
powershell -ExecutionPolicy Bypass -File scripts\uninstall_task.ps1   # desliga o agendador
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1     # liga de novo
```

Registros: `state\trades.txt` (legível), `state\desk.log` (técnico), `state\desk.db` (banco).

## Avisos honestos
- **Lucro não é garantido.** Os US$5.273 do guia original são marketing. O modo paper existe para provar (ou desmentir) a vantagem antes de arriscar dinheiro.
- Mercado de previsão não é permitido em todo lugar. A responsabilidade legal é sua.
- O PC precisa ficar ligado, logado e sem dormir para o agendador rodar (Configurações → Energia).

# O que depende de você

Tudo que eu (Claude) não posso fazer por você: credenciais, dinheiro e decisões suas. Nada aqui é obrigatório para o modo **paper**, que já roda sozinho com o login do Claude Code que você já tem.

> Regra de ouro: **nunca cole chave privada, seed ou senha em chat nenhum** (nem comigo). Elas vão só no arquivo `.env`, na sua máquina, e o `.env` nunca vai para o git.

## Situação em 13/10/2026

| Item | Estado |
|---|---|
| Código | Pronto, 438 testes passando, na branch `feat/polymarket-desk` (PR #1 no GitHub) |
| Agendador do Windows | **Ligado.** `PolymarketDesk` roda um ciclo a cada 10 min (paper); `PolymarketDeskMemes` roda as memecoins a cada 3 min (paper); `PolymarketDeskMemesExits` confere as saídas das memecoins a cada 1 min; `PolymarketDeskMonthly` roda todo dia e relata 1x por mês |
| Modo paper | Rodando sozinho: cada ciclo varre ~1000 mercados, ~470 passam no filtro, o Claude analisa até 3. Ainda sem entradas (nenhuma distorção ≥ 8pp) e sem erros |
| Economia | O robô paga para existir: aluguel diário (assinatura rateada) + US$0,01 por análise, saindo do caixa dele. Se não lucrar mais que isso, é **desligado** na linha de 40% |
| Painel | `scripts\dashboard.cmd` (duplo clique) ou `python -m desk dashboard` → http://127.0.0.1:8765 |
| Modo real (Polymarket) | **Bloqueado para você**: em 13/10 o próprio Polymarket respondeu `Geobloqueio: BLOQUEADO (país=BR região=SP)`. Veja "Caminho para o dinheiro real" |
| Memecoins (Solana) | **Agente pronto em paper**: compra e vende sozinho com cotações reais da Jupiter, banca própria de US$100 (`state\memes.db`). Tarefa `PolymarketDeskMemes` a cada 3 min. No primeiro ciclo agendado (13/10, 11:33) o Claude vetou o único token que chegou até ele (sem fontes, possível cópia de outro ticker); ainda sem compras. Modo real pronto no código, travado até você criar a carteira (C4) |

---

## Caminho para o dinheiro real

**Resposta curta:** hoje só um dos dois robôs pode receber dinheiro real com você, o de **memecoins**. E ele ainda não deveria, porque o paper não provou nada (até agora ele não fez nenhuma compra).

### Polymarket: não dá, a partir do Brasil
A checagem oficial do Polymarket (`python -m desk live-check`) respondeu **BLOQUEADO, país=BR, região=SP**. O robô recusa ordem real nessa situação, e isso não é falha dele: é a regra do Polymarket para o seu local. Usar VPN ou VPS em outro país para contornar isso viola os termos, e o Polymarket pode congelar o saldo. Por isso eu não configuro e não recomendo.

O desk do Polymarket continua útil em **paper**: ele mede se o Claude acha distorções de verdade. Se um dia você estiver morando num país liberado, o caminho está na C2.

### Memecoins: o caminho, em ordem
Cada passo depende do anterior:

1. **Telegram (C1).** Obrigatório: sem ele, o `memes-live-check` não libera o modo real. No modo real, um aviso de "compra incerta" ou "não conseguiu vender" exige que você confira a carteira.
2. **Não deixar o PC dormir.** Hoje ele dorme depois de 3h parado, e dormindo o stop-loss não roda. Rode `powercfg /change standby-timeout-ac 0` (para desfazer, troque `0` por `180`). Deixe o PC na tomada, ligado e logado. Se o agente ficar mais de 15 minutos sem rodar com posições abertas, ele avisa no Telegram quando voltar. Stop-loss na própria Jupiter (que rodaria com o PC desligado) exige no mínimo US$10 por ordem, e as compras aqui são de US$3; com uma banca bem maior isso passa a valer a pena.
3. **Paper provando que se paga (D8).** Pelo menos 2 semanas e 30 compras e vendas fechadas, com resultado positivo **depois** dos custos. O painel mostra isso no cartão "Pronto p/ dinheiro real?" (também no `memes-status` e no relatório mensal). Se não passar, não ligue o real: o robô não tem vantagem e você descobriu de graça.
4. **Carteira Solana dedicada (C4).** Carteira nova, só com a banca, mais ~0,1 SOL para taxas. Chave no `.env`, nunca no chat.
5. **`memes-live-check` dizendo "PRONTO".** Ele confere: modo, as duas travas, chave válida, saldo de SOL e de USDC, e o Telegram.
6. **Virar a chave.** Em `config.toml`, na seção `[memes]`: `mode = "live"` e `bankroll_start_usd` igual ao USDC da carteira. Renomeie `state\memes.db` para `memes-paper.db`.
7. **Primeira semana com banca mínima** (US$30–50) e olho no Telegram e no painel. O modo real foi testado com transações simuladas, nunca contra a rede de verdade.
8. **Impostos.** Ganho com cripto em carteira própria entra no imposto de renda e as regras mudaram recentemente. Confirme com um contador como declarar.

Regras que não mudam no modo real: o robô **não saca** (tirar dinheiro é sempre com você) e cada token recebe no máximo 3% da banca. Caiu 40% do pico, ele é desligado.

---

## 0. Ver o que está acontecendo

Dê duplo clique em `scripts\dashboard.cmd`. Ele abre o painel no navegador, que se atualiza a cada 30s e mostra:
- **Vida do robô:** equity, quanto ganhou operando, quanto custou existir, dias de fôlego até ser desligado e o gráfico contra a linha de desligamento.
- **O que o robô decidiu:** cada mercado que o Claude analisou, com preço, valor justo, vantagem e o motivo de ter entrado ou pulado.
- **Posições, trades e ciclos**, além do diário (`trades.txt`) e do log técnico.
- **Memecoins (Solana)**, mais abaixo: banca, custo, queda até a linha de desligamento, posições (com link para o fomo.family), os tokens que ele checou a fundo e por que comprou ou não, compras e vendas.

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
Em 13/10/2026 o `polymarket.com` voltou a responder nesta rede, e a checagem oficial de geobloqueio respondeu **BLOQUEADO (país=BR, região=SP)**. O robô **recusa** qualquer ordem real quando a região é bloqueada ou quando não consegue confirmar que ela é liberada.

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

### D8. Memecoins: até onde deixar o agente ir
O agente de memecoins (`[memes]` no `config.toml`) já compra e vende sozinho **em paper**. Como ele decide:
1. **Descobre** tokens no DexScreener (os mesmos do fomo.family) e nas listas da Jupiter (mais negociados, mais orgânicos, em alta).
2. **Filtros de mercado:** liquidez ≥ US$50 mil, de 1h a 72h de vida, volume e negócios na última hora, mais compra que venda, sem alta vertical, market cap entre US$150 mil e US$25 milhões, e organic score da Jupiter ≥ 50 (pouco robô).
3. **X, sem custo:** lê o post ou o perfil do X que o token divulga. Rejeita link apagado, post com pouco alcance (menos de 5 mil views e 50 likes) e perfil com menos de 100 seguidores. Os que passam são checados na ordem do maior alcance.
4. **Segurança (RugCheck):** sem poder de imprimir nem de congelar tokens, liquidez ≥ 80% travada, top 10 ≤ 30%, insiders ≤ 10%, criador ≤ 5%, sem taxa de transferência.
5. **Cotação real (Jupiter):** comprar e vender de volta na hora não pode custar mais de 5%, e precisa existir rota de venda (pega "honeypot").
6. **Claude (só veta):** recebe os dados do X e busca na web e no X se o token é golpe, cópia, conta comprada ou propaganda paga. Veta quando acha sinal de alerta; falta de notícia sobre um token de poucas horas não é motivo. Ele nunca obriga uma compra.
7. **Compra** 3% da banca (US$3 com US$100), no máximo 4 tokens ao mesmo tempo.
8. **Vende** pelas regras: stop de -30%, metade ao dobrar, stop móvel de 30% abaixo do pico, liquidez caindo pela metade, 24h no máximo, ou na hora se o post/perfil do X do token for apagado.
9. **Freios:** perdeu 15% em 24h, pausa as compras; caiu 40% do pico, é desligado. As saídas rodam a cada minuto mesmo com um freio ativo.
10. **Paga para existir:** cada checagem do Claude custa US$0,01 do caixa dele. Se cair 40% do pico, é **desligado**: para de comprar e de gastar, mas continua vendendo pelas regras.

Suas escolhas:
- **Só alertas (opção B):** `auto_trade = false`. Ele manda no Telegram os tokens aprovados e você compra à mão no fomo.family, se quiser.
- **Uso do Claude:** até 30 checagens por dia e 2 por ciclo (`max_vets_per_day`, `max_vets_per_cycle`). No Claude Pro, deixe assim. `analyst = "none"` desliga o Claude e ele compra só pelas regras (não recomendo).
- **Passar para o real:** exija pelo menos 2 semanas e 30 trades fechados em paper com resultado positivo **depois** dos custos (`python -m desk memes-status`). A maioria dos robôs de memecoin perde dinheiro; o paper existe para descobrir isso de graça.

## Credenciais (só quando decidir usar)

### C1. Avisos no celular pelo Telegram (opcional, recomendado)
1. No Telegram, abra `@BotFather` → `/newbot` → escolha um nome e um usuário (terminando em `bot`) → copie o **token** (algo como `123456:ABC...`).
2. Abra o arquivo `.env` na pasta do projeto (`notepad .env`), cole o token na linha `TELEGRAM_BOT_TOKEN=` e salve. O `.env` já existe e nunca vai para o git. **Não cole o token em chat.**
3. No Telegram, abra o seu bot novo, toque em **Iniciar** e mande "oi".
4. Rode:
   ```powershell
   .venv\Scripts\python -m desk telegram-setup
   ```
   O comando confere o token, acha a sua conversa com o bot, grava `TELEGRAM_CHAT_ID` no `.env` e manda uma mensagem de teste.
5. Pronto: os próximos ciclos já mandam os avisos. Você recebe:
   - compras e vendas (Polymarket e memecoins);
   - as memecoins aprovadas;
   - desligamento por queda;
   - falhas repetidas;
   - o relatório mensal.

   Os avisos de compra e venda em paper das memecoins podem ser desligados com `notify_paper_trades = false`.

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
8. Em `config.toml`: `mode = "live"` e `bankroll_start_usd` igual ao saldo real. **Obrigatório:** renomeie `state\desk.db` para `desk-paper.db` e `state\trades.txt` para `trades-paper.txt`. Não renomeie a pasta `state` inteira: ela guarda também o registro das memecoins. O robô se recusa a usar o registro do paper no modo real.
9. Acompanhe o primeiro trade real de perto. O código do modo real foi testado com simulações, **nunca contra a exchange de verdade** (daqui não é possível). Por isso, comece com a banca mínima.

O robô **não tem função de saque**. Sacar e resgatar mercados resolvidos é sempre com você, pelo site.

### C3. Rodar numa VPS (opcional)
Na VPS não há navegador para logar no Claude. No **seu PC**, rode `claude setup-token` e copie o token gerado para a VPS como `CLAUDE_CODE_OAUTH_TOKEN=...` (detalhes em `deploy/VPS.md`, passo 3).

---

### C4. Carteira Solana para as memecoins (só depois da D8)
1. **Carteira nova e dedicada:** instale a Phantom ou a Solflare e crie uma carteira **nova**, só para o agente. Nunca use a carteira onde estão seus outros fundos.
2. **Fundos:** mande para ela só a banca das memecoins, em **USDC** na rede Solana (ex.: US$100), mais **~0,1 SOL** para as taxas da rede. Cada operação custa centavos de centavo, mas cada token novo prende ~0,002 SOL de aluguel da conta (recuperável fechando contas vazias na carteira). O agente não compra com menos de 0,05 SOL e avisa se faltar SOL com posições abertas: sem SOL, a Jupiter só faz trocas "gasless", que recusam valores abaixo de US$5-10 e cobraram 10% de taxa num teste real. Por isso, também, toda compra é recusada se a ordem final vier mais de 3% pior que a cotação aprovada.
3. **Chave privada (base58):**
   - Phantom: *Configurações → Gerenciar contas → (a conta) → Mostrar chave privada*.
   - Solflare: *Configurações → (a carteira) → Exportar chave privada*.

   É um texto longo de letras e números. **Não é a frase de 12/24 palavras**: o agente não aceita a frase.
4. Preencha o `.env` (nunca cole isso em chat):
   ```
   MEMES_PRIVATE_KEY=<chave base58>
   MEMES_LIVE=I_UNDERSTAND
   ```
   **Opcional:** `MEMES_RPC_URL=` com um RPC gratuito da Helius ou QuickNode, mais estável que o RPC público.
   **Opcional:** `MEMES_JUPITER_API_KEY=` com uma chave grátis da Jupiter ([developers.jup.ag/portal](https://developers.jup.ag/portal)). Sem ela o agente funciona, mas a Jupiter só aceita 1 chamada a cada 2 segundos; com ela, 1 por segundo.
5. Instale o extra e valide:
   ```powershell
   uv sync --all-extras
   .venv\Scripts\python -m desk memes-live-check
   ```
   Ele mostra o endereço público e os saldos de SOL e de USDC e confere se o Telegram está configurado. Também pede à Jupiter uma troca de verdade de US$1 USDC → SOL para a sua carteira e a assina localmente, **sem enviar**: prova que a chave e a Jupiter funcionam sem gastar nada. Precisa terminar com **"PRONTO para modo real nas memecoins."**
6. Em `config.toml`, na seção `[memes]`: `mode = "live"` e `bankroll_start_usd` igual ao USDC na carteira.
7. **Obrigatório:** renomeie `state\memes.db` para `state\memes-paper.db`. O agente se recusa a usar o registro do paper no modo real.
8. Acompanhe as primeiras compras de perto. O código assina só a parte da carteira em cada transação da Jupiter e foi testado com transações simuladas, **nunca contra a rede de verdade**.

Se a resposta de uma troca se perder, o agente pergunta de novo à Jupiter por até 90 segundos (ela nunca executa a mesma troca duas vezes). Se mesmo assim a compra ficar "incerta", ele avisa no Telegram e confere a carteira por 10 minutos: se os tokens chegaram, assume a posição com stop e as demais saídas; se não, avisa que o USDC não foi gasto. O `memes-status` mostra as compras incertas pendentes Com uma venda incerta é igual: se os tokens saíram da carteira, ele registra a venda pelo preço da tentativa (confira o USDC); se não saíram em 10 minutos, vende de novo. O agente **não tem função de saque**; tirar dinheiro da carteira é sempre com você.

## Comandos do dia a dia

```powershell
.venv\Scripts\python -m desk status        # banca, posições, últimos trades
.venv\Scripts\python -m desk cycle         # força um ciclo agora
.venv\Scripts\python -m desk analyze <slug-ou-id>   # Claude analisa 1 mercado, sem operar
.venv\Scripts\python -m desk resume        # retoma depois de parada por drawdown (leia o post-mortem antes)
.venv\Scripts\python -m desk memes-status  # memecoins: banca, posições, últimos trades
.venv\Scripts\python -m desk memes         # força um ciclo das memecoins agora
.venv\Scripts\python -m desk memes-resume  # religa o agente de memecoins depois de desligado
powershell -ExecutionPolicy Bypass -File scripts\uninstall_task.ps1   # desliga o agendador (as três tarefas)
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1     # liga de novo
```

Registros: `state\trades.txt` e `state\memes.txt` (legíveis), `state\desk.log` (técnico), `state\desk.db` e `state\memes.db` (bancos).

## Avisos honestos
- **Lucro não é garantido.** Os US$5.273 do guia original são marketing. O modo paper existe para provar (ou desmentir) a vantagem antes de arriscar dinheiro.
- Mercado de previsão não é permitido em todo lugar. A responsabilidade legal é sua.
- **Memecoins são o risco mais alto daqui:** a maioria vai a zero, golpes passam por qualquer filtro e o preço anda 30% em minutos. Confira também os termos da Jupiter e as regras do seu país. Só coloque o que aceita perder inteiro.
- O PC precisa ficar ligado, logado e sem dormir para o agendador rodar (Configurações → Energia).

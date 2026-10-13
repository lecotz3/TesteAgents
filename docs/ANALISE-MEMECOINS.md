# Análise: memecoins (fomo.family) e sinais de redes sociais

Pedido: avaliar se o agente pode operar memecoins como o token do link (fomo.family, Solana), com base em informações que recebe ou em integração com redes sociais. Dados coletados em 13/10/2026.

## Resposta curta

- **Pelo fomo.family, não.** O fomo é um app social de trading de memecoins (você segue traders e vê as compras deles). Não encontrei API pública para robôs. Automatizar o app pelo navegador seria frágil, provavelmente contra os termos de uso, e exigiria entregar o login da sua conta ao robô.
- **Os mesmos tokens, sim, tecnicamente.** Um robô opera direto na blockchain Solana: dados grátis (DexScreener, GeckoTerminal), checagem de golpe (RugCheck) e execução pela API do Jupiter, com uma carteira Solana dedicada. O fomo vira só a sua vitrine para olhar.
- **Economicamente, é muito pior que o Polymarket para um agente como o nosso.**
  - O analista Claude leva de 30 a 60 s por análise. Memecoin quente se move em segundos, e os robôs "sniper" compram no primeiro bloco.
  - Cerca de 98,6% dos tokens lançados no pump.fun mostraram padrão de rug pull ou pump-and-dump, segundo o estudo da Solidus Labs.
  - Sinais de rede social são o principal instrumento desses golpes.
- **Minha recomendação:** não colocar dinheiro real nisso agora. Se quiser testar, as opções estão no fim do documento: B (só alertas) ou A ("Meme Desk" em paper), reaproveitando a economia ("paga para existir"), o painel e o diário que já existem.

## O token do link: SEALS ("Save The Seals")

Dados públicos do DexScreener e do RugCheck, por volta das 13h UTC de 13/10:

| Item | Valor |
|---|---|
| Criado | Hoje, 09:53 UTC (pool na PumpSwap). Tinha **~3 horas de vida** |
| Variação | **+3.362% em 6h**; +268% na última hora |
| Market cap | ~US$1,39 milhão |
| Liquidez | ~US$112 mil no pool principal (mais dois pools na Meteora) |
| Negócios | ~12 mil em 24h (6,3 mil compras, 6,1 mil vendas) |
| RugCheck | Score 1, sem alertas listados; **só 35,7% da liquidez travada** |

O que isso significa: é um token de horas, em plena euforia. Quem o vê subindo num feed social já está atrás dos bots e de quem lançou. Com 64% da liquidez destravada, ela pode ser retirada a qualquer momento. Não é uma avaliação deste token em particular; é o perfil típico do mercado.

## Como um robô faria (se você quiser seguir)

1. **Descoberta.** Pools novos e tokens em alta pelas APIs do DexScreener e do GeckoTerminal (grátis, com limite de taxa). Também dá para acompanhar os tokens que "graduam" do pump.fun.
2. **Filtros duros, no código e não no LLM.** São a primeira defesa e eliminam a maior parte dos golpes. Exigem:
   - autoridade de mint e de freeze revogadas;
   - liquidez travada ou queimada acima de um mínimo;
   - concentração dos 10 maiores holders abaixo de um teto;
   - RugCheck sem riscos "danger";
   - idade e liquidez mínimas;
   - compras e vendas reais (não só bots);
   - cotação do Jupiter com impacto de preço baixo.
3. **Sinais sociais** (cada um com custo, que entra na economia do robô):
   - **X (Twitter):** desde fev/2026 é pago por uso, cerca de US$0,005 por post lido. Ler 1.000 posts por dia dá ~US$150/mês, mais que a banca inteira. Com a regra "paga para existir", isso por si só desligaria o robô.
   - **Telegram:** canais públicos lidos com uma conta sua (API id/hash de my.telegram.org), sem custo. Os canais de "call" são, em grande parte, pump-and-dump pagos.
   - **Farcaster** (API Neynar, plano grátis) e **Reddit** (API grátis e limitada) têm menos ruído e menos volume.
   - **O papel do Claude:** filtro de narrativa e de manipulação (shill, bots, promessas). Não serve para estimar "probabilidade", porque memecoin não resolve em sim ou não.
4. **Decisão e risco.**
   - Posições minúsculas: 1–2% da banca.
   - Stop e alvo automáticos (ex.: vende metade em +100%, zera em −30%).
   - Tempo máximo de posse.
   - Teto de posições.
   - Mesma parada por drawdown.
5. **Execução.** API Ultra do Jupiter (pede a ordem, assina e executa), com chave em portal.jup.ag e uma carteira Solana **dedicada** com a chave no `.env`. No paper, a simulação usa as cotações do Jupiter, que são de graça e já incluem o impacto de preço.

## Riscos (sem rodeio)

- **Taxa de golpe:** ~98,6% dos tokens do pump.fun com padrão de rug ou pump-and-dump (Solidus Labs, 2025). O filtro precisa ser rígido, e mesmo assim muitos golpes passam.
- **Velocidade:** robôs com latência de milissegundos compram antes. Um agente que pensa por 30 s tende a comprar o topo e a virar a liquidez de saída de alguém.
- **Manipulação social:** influenciadores pagos, contas-robô e canais de "call" existem justamente para gerar compradores.
- **Armadilhas técnicas:** tokens que não deixam vender (honeypot), taxas escondidas (Token-2022) e freeze de carteira.
- **Injeção de prompt:** nome, descrição e site do token são texto escrito pelo golpista. O analista precisa tratá-los como dado, nunca como instrução, e a carteira jamais pode ficar ao alcance do LLM.
- **Custos:**
  - de 0,5% a 1% por lado;
  - priority fee;
  - slippage de 1% a 5% em pools rasos.

  Em um giro rápido, isso come qualquer vantagem pequena.
- **Legal e impostos:** no Brasil, operar cripto em carteira própria não tem o bloqueio geográfico do Polymarket, mas os ganhos têm regra própria de imposto de renda; confirme a regra vigente com um contador. O app fomo pode ter restrições próprias de país nos termos de uso.

## Opções

| Opção | O que é | Custo e risco |
|---|---|---|
| **A. Meme Desk em paper** | Segundo robô, com o mesmo esqueleto (economia, painel, diário). Descobre, filtra, usa o Claude como filtro de narrativa e simula as compras com cotações reais do Jupiter. Roda de 2 a 4 semanas antes de qualquer decisão | Zero dinheiro; gasta cota do Claude (com teto) |
| **B. Só alertas** | O robô filtra tokens que passam nos critérios de segurança e manda no Telegram; você decide e compra no fomo, à mão | O mais barato e seguro; não automatiza a compra |
| **C. Dinheiro real já, ou automatizar o app fomo** | Não recomendo | Alta chance de perder a banca; risco de conta e de chave |

> **Atualização (13/10/2026):** a opção **A** foi implementada, com a **B** embutida (`auto_trade = false` vira só alertas). Ela roda em paper a cada 3 minutos e tem o modo real pronto atrás de duas travas. Veja `docs/GUIA.md` (Memecoins) e `docs/PENDENCIAS.md` (D8 e C4).

**Recomendação:** começar pela **B**, que fica pronta rápido e já mostra se os filtros acham algo que preste, e evoluir para a **A**. Nada de dinheiro real até o paper provar, com taxas e slippage realistas, que o robô se paga.

## Fontes

- [Solidus Labs: 2025 Rug Pull Report (Solana)](https://www.soliduslabs.com/reports/solana-rug-pulls-pump-dumps-crypto-compliance) e a cobertura no [The Defiant](https://thedefiant.io/news/defi/alarming-99-of-memecoin-launches-on-pumpfun-are-pump-and-dumps-or-rug-pulls-report)
- O que é o fomo: [review no Medium](https://medium.com/@gemQueenx/fomo-app-solana-memecoin-trading-app-review-and-referral-code-e9e766fce316), [AirdropAlert](https://airdropalert.com/blogs/fomo-app-review/) e [MadeOnSol](https://madeonsol.com/compare/fomo-vs-pump-fun) (fontes secundárias; taxas a confirmar no app)
- Jupiter: [documentação da API](https://hub.jup.ag/docs/api) e o [guia de integração](https://skills.sh/jup-ag/agent-skills/integrating-jupiter)
- Preço da API do X em 2026: [OpenTweet](https://opentweet.io/x-api-pricing) e [bundle.social](https://bundle.social/blog/x-api-pricing-2026-costs-limits) (confirme em docs.x.com)
- Dados do token: API pública do DexScreener (`/latest/dex/tokens/<mint>`) e do RugCheck (`/v1/tokens/<mint>/report/summary`)

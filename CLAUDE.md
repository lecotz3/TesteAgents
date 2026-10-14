# TesteAgents: context for Claude Code

This file carries the working context between computers (Claude's own memory stays on the machine where it was written). User-facing docs are in Portuguese: `docs/PENDENCIAS.md` (everything the user must provide or decide), `docs/GUIA.md`, `README.md`.

## What this is
Two trading agents, adapted from the guide "How to Build a Self-Funding Grok Agent That Trades Polymarket", with Claude Code (`claude -p`, default) or Codex (`codex exec`) as the analyst:
- **Polymarket desk** (`desk/`): scans markets every 10 min, the LLM only estimates probabilities, deterministic rules trade (8pp edge, ½ Kelly, 6% cap). **Paper only for this user:** Polymarket's own geoblock check returned BLOCKED (BR/SP). Never help bypass geoblocking (no VPN/VPS in another country).
- **Memecoin agent** (`desk/memes/`, Solana): discovery on DexScreener + Jupiter lists, market filters, free X signals via FxTwitter, RugCheck safety, real Jupiter quotes, Claude can only veto. It buys 3% of the bankroll, holds at most 4 positions, and exits by fixed rules (stop -30%, half at +100%, trailing stop 30%, 24h max, X link gone). Paper fills at real Jupiter quotes. This is the user's only possible real-money path.
- Both "pay to exist" (`[economy]`, cost per Claude call) and are switched off when they don't make money. Since 2026-10-13 the user's `config.toml` sets `drawdown_halt = 1.0` for both: off only when the money runs out (equity under `economy.BROKE_USD` = $1; `economy.shutdown_line`). The code default stays 0.40 (the guide's 40% stop). To keep trying until broke, memecoin buys never go under `min_trade_usd` while there is cash for it.

## Working rules (from the user)
- **Commit and push straight to `main`. No branches, no PRs.** Run the test suite (`.venv\Scripts\python -m pytest -q`, 524 tests as of 2026-10-13, 9 skipped without the live extras) before each commit.
- Work autonomously to the end. Stop only to ask a real question, when tokens run out, or when the PC is off. Anything the user must provide (credentials, keys, decisions) goes into `docs/PENDENCIAS.md` with step-by-step instructions instead of blocking.
- Never handle private keys, seeds or tokens in chat; they live only in `.env` (never committed). Real money is always the user's decision. Real mode needs `mode="live"` in `config.toml` plus `DESK_LIVE` / `MEMES_LIVE=I_UNDERSTAND`.
- Keep risk rules deterministic in code. Be honest that profit is not guaranteed.
- TDD: write the failing test first. Commit messages are short and say what changed and why.

## Where the robots run (important with two computers)
- Since 2026-10-13 21:47 the scheduled tasks (`scripts\install_task.ps1`) run on the **desktop DESKTOP-7FOLHUU** (user's decision; no battery, never sleeps on AC, S3 standby only), from `C:\Users\Leonardo\Projetos\TesteAgents`:
  - `PolymarketDesk`: every 10 min;
  - `PolymarketDeskMemes`: every 3 min;
  - `PolymarketDeskMemesExits`: every 1 min;
  - `PolymarketDeskMemesReview`: hourly (`memes-review --max 60`);
  - `PolymarketDeskMonthly`: daily;
  - `PolymarketDeskDashboard`: the panel at http://127.0.0.1:8765, started at logon and re-checked every 5 min. Desktop shortcut "Painel dos robos".
- The tasks run the working tree directly: a committed change is live at the next run. The panel loads its code once; restart it (`Stop-ScheduledTask` / `Start-ScheduledTask PolymarketDeskDashboard`) after changing `desk/dashboard*`.
- The old **laptop** still had its tasks installed when the move happened. The user was asked (PENDENCIAS A1) to run `scripts\uninstall_task.ps1` there. Its `state\` (a few hours of paper, VEIL/SEALS) stays there; the desktop started a fresh paper history.
- `state/` (SQLite ledgers, journals, logs) and `.env` are **not in git** on purpose. They are live data and secrets.
- **Run the agents on one computer only.** In paper, a second copy just builds a separate, confusing history and spends the Claude quota twice. In live mode, two copies on the same wallet would trade with two separate ledgers. To move them again: uninstall on the old PC first, then copy `state\` and `.env` by hand.
- **This network's DNS refuses Polymarket** (`gamma-api`/`clob.polymarket.com`: "DNS operation refused"). The desk cycle runs but scans nothing and says so on the panel. That is Brazil's block: never route around it (no VPN, no alternative DNS).

## Setting up another computer for development
```powershell
git clone https://github.com/lecotz3/TesteAgents.git
cd TesteAgents
uv sync                      # add --extra memes-live / --extra live only for real-mode work
copy .env.example .env       # fill only what is needed; paper mode needs nothing
.venv\Scripts\python -m pytest -q
```
`claude` must be logged in for the vetter/analyst calls; tests do not need it.

## Status (2026-10-13)
- Code complete for both agents. The memecoin live mode is hardened against the real Jupiter API but has never sent a real transaction:
  - an order more than 3% worse than the approved quote is refused;
  - a lost `execute` answer is polled for 90s with the same signed transaction; uncertain buys are adopted from the wallet and uncertain sells settled at the attempt's price;
  - empty token accounts are closed hourly to get the rent back;
  - a low-SOL alert, a PC-asleep alert, and `memes-sell-all --yes`.
- **Paper results:** the desktop's paper history starts 2026-10-13 21:50 (US$100). Its first two exits (VEIL, CRAWLER) were false "X link deleted" sells, fixed the same night (see below). The readiness meter needs 14 days, 30 closed trades and a positive result after costs.
- **FxTwitter misfires:** it answers "User not found" (HTTP 404) for live accounts in about 1 call of 3. `FxTwitter._get` now believes "not found" only after 3 answers in a row, and the `x_link_dead` exit needs the link missing on every check for `X_GONE_MINUTES` (5) (`x_missing:<pos id>` meta). Discovery rejections still use a single (3-try) check.
- **`memes-review` is done** (`desk/memes/review.py`, `python -m desk memes-review [--max N]`, hourly task, panel table "Os filtros acertam?"). It replays `rules.exit_decision` on GeckoTerminal 1-minute closes for `max_hold_hours` after each deep-checked decision (operational ones skipped; deduped by (mint, decision)), entry at the first close, liquidity fixed at entry, flat 2% cost, X exit not simulated. Only finished windows are fetched; results are cached in `state/memes-review.json` keyed by the exit rules (`rules_key`), so changing the rules recomputes. The panel reads only the cache (`max_fetch=0`). `PacedApi` in `sources.py` is the generic spaced/429-retrying client; `JupiterApi` subclasses it.
- **"Em alta" tracker** (user asked to track the biggest memecoin moves with fomo.family): fomo has no public API (its lists come from internal endpoints and the browser cache; the user's Chrome is logged into fomo, never act on that account). Its "Em alta" overlaps Jupiter's public `toptraded`/`toptrending` lists, so `desk/memes/movers.py` ranks the young (`movers_max_age_days`) tokens of the Jupiter lists the cycle already fetched (`JupiterTokens.lists`, cached per run) by |1h change|, stores `movers_keep` per cycle in the `movers` table (3 days), and the panel shows them with fomo links, the robot's market-filter verdict, and each token's 24h peak. Tracking only, never buys.
- **Pending from the user** (see `docs/PENDENCIAS.md`): uninstall the tasks on the laptop (A1), Telegram (required for live checks), the paper proof, then a dedicated Solana wallet.
- Deferred Polymarket minors (#9, #11, #12, #18, #20) are listed in a PR #1 comment. They are low priority while that desk is paper-only and DNS-blocked.

## Possible next steps
- Once `memes-review` has a few days of finished windows, read it with the user: a filter whose rejections average positive is a candidate to loosen; a negative "Comprou" average means the agent has no edge.
- The Polymarket desk keeps paying rent while DNS-blocked and runs out of money in ~118 days without trading (runway now counts the fixed rent). The user can pause it by disabling `PolymarketDesk`; changing the economy rules for blocked days is the user's call.

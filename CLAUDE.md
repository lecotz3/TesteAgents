# TesteAgents: context for Claude Code

This file carries the working context between computers (Claude's own memory stays on the machine where it was written). User-facing docs are in Portuguese: `docs/PENDENCIAS.md` (everything the user must provide or decide), `docs/GUIA.md`, `README.md`.

## What this is
Two trading agents, adapted from the guide "How to Build a Self-Funding Grok Agent That Trades Polymarket", with Claude Code (`claude -p`, default) or Codex (`codex exec`) as the analyst:
- **Polymarket desk** (`desk/`): scans markets every 10 min, the LLM only estimates probabilities, deterministic rules trade (8pp edge, ½ Kelly, 6% cap, 40% drawdown halt). **Paper only for this user:** Polymarket's own geoblock check returned BLOCKED (BR/SP). Never help bypass geoblocking (no VPN/VPS in another country).
- **Memecoin agent** (`desk/memes/`, Solana): discovery on DexScreener + Jupiter lists, market filters, free X signals via FxTwitter, RugCheck safety, real Jupiter quotes, Claude can only veto. It buys 3% of the bankroll, holds at most 4 positions, and exits by fixed rules (stop -30%, half at +100%, trailing stop 30%, 24h max, X link gone). Paper fills at real Jupiter quotes. This is the user's only possible real-money path.
- Both "pay to exist" (`[economy]`, cost per Claude call) and switch themselves off at a 40% drawdown.

## Working rules (from the user)
- **Commit and push straight to `main`. No branches, no PRs.** Run the test suite (`.venv\Scripts\python -m pytest -q`, 484 tests as of 2026-10-13) before each commit.
- Work autonomously to the end. Stop only to ask a real question, when tokens run out, or when the PC is off. Anything the user must provide (credentials, keys, decisions) goes into `docs/PENDENCIAS.md` with step-by-step instructions instead of blocking.
- Never handle private keys, seeds or tokens in chat; they live only in `.env` (never committed). Real money is always the user's decision. Real mode needs `mode="live"` in `config.toml` plus `DESK_LIVE` / `MEMES_LIVE=I_UNDERSTAND`.
- Keep risk rules deterministic in code. Be honest that profit is not guaranteed.
- TDD: write the failing test first. Commit messages are short and say what changed and why.

## Where the robots run (important with two computers)
- The scheduled tasks (`scripts\install_task.ps1`) run on the **original Windows PC**, from its local folder:
  - `PolymarketDesk`: every 10 min;
  - `PolymarketDeskMemes`: every 3 min;
  - `PolymarketDeskMemesExits`: every 1 min;
  - `PolymarketDeskMonthly`: daily.
- That PC needs `git pull` to pick up new code.
- `state/` (SQLite ledgers, journals, logs) and `.env` are **not in git** on purpose. They are live data and secrets. The paper history the readiness proof depends on lives in that PC's `state/memes.db`.
- **Run the agents on one computer only.** In paper, a second copy just builds a separate, confusing history. In live mode, two copies on the same wallet would trade with two separate ledgers. Do not run `install_task.ps1` on a second machine unless the robots are being moved: first uninstall them on the old PC (`scripts\uninstall_task.ps1`), then copy `state\` and `.env` by hand.

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
- **Paper results:** first buys were VEIL and SEALS on 2026-10-13. VEIL sold half at +132%. Equity ≈ US$103 from US$100. The readiness meter needs 14 days, 30 closed trades and a positive result after costs.
- **Pending from the user** (see `docs/PENDENCIAS.md`): Telegram (required for live checks), keeping the PC from sleeping, the paper proof, then a dedicated Solana wallet.
- Deferred Polymarket minors (#9, #11, #12, #18, #20) are listed in a PR #1 comment. They are low priority while that desk is paper-only.

## Next step (planned, not started): `memes-review`
A CLI report, `desk/memes/review.py` + `python -m desk memes-review`. For every deep-checked decision in `memes.db` (bought, vetter_avoid, safety/X/quote rejections; skip operational ones like vet_error, buy_failed, wallet_*), it replays the real `rules.exit_decision` on the price history after the decision. It then reports per decision reason: count, average return, wins, worst and best. The point is to learn whether each filter and Claude's veto avoid losers, faster than the 30-trade paper proof.
- **Price data:** GeckoTerminal's free API (verified working):
  - pool lookup: `/api/v2/networks/solana/tokens/{mint}/pools`
  - candles: `/api/v2/networks/solana/pools/{pool}/ohlcv/minute?aggregate=1&limit=1000&currency=usd&token={mint}&before_timestamp=...`, newest first, page backwards. `token=<mint>` also works when the mint is the quote side of the pool.
- **Rate limit:** about 30 calls/min, so space calls about 2.1s apart and retry 429s. A generic paced client can be extracted from `JupiterApi` in `desk/memes/sources.py`.
- **Replay:** use the 1-minute close (the real exit loop sees one price a minute). Liquidity stays at the entry level, because candles carry no liquidity data. Partial take-profit follows the real fractions. Subtract a flat ~2% round-trip cost. A window still under 24h is reported as open.
- **Cache:** save complete 24h windows in `state/memes-review.json`. Dedupe by (mint, decision), keeping the first.
- **Sanity check:** compare the simulated "bought" results with the real paper positions.

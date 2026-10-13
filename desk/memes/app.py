"""Commands of the memecoin agent: python -m desk {memes, memes-status, memes-resume, memes-live-check}."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from desk.analyst.claude_cli import ClaudeCliAnalyst
from desk.config import ConfigError, Secrets, Settings
from desk.executor import LIVE_CONFIRMATION
from desk.journal import Journal, format_note
from desk.lock import cycle_lock
from desk.memes.cycle import MemeDeps, run_meme_cycle
from desk.memes.executor import LiveMemeExecutor, MemeExecutor, PaperMemeExecutor, load_wallet, raw_usdc
from desk.memes.models import USDC_MINT
from desk.memes.readiness import readiness
from desk.memes.sources import FETCH_ERRORS, DexScreener, Jupiter, JupiterTokens, RugCheck, SolanaRpc
from desk.memes.store import MemeStore
from desk.memes.vetter import MemeVetter
from desk.memes.x import FxTwitter
from desk.notify import Notifier
from desk.store import ModeMismatch

log = logging.getLogger("desk")
FAILURE_ALERT_EVERY = 3


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class MemesApp:
    settings: Settings
    secrets: Secrets
    store: MemeStore
    journal: Journal
    notifier: Notifier
    http: httpx.Client

    def close(self) -> None:
        self.store.close()
        self.http.close()


def build_memes_app(config_path: Path, env_path: Path) -> MemesApp:
    from desk.cli import load_runtime  # the CLI imports this module; import lazily to avoid a cycle

    settings, secrets, http, notifier = load_runtime(config_path, env_path)
    m = settings.memes
    store = MemeStore(settings.state_dir / "memes.db", start_cash=m.bankroll_start_usd)
    try:
        store.ensure_mode(m.mode)
    except ModeMismatch as exc:
        store.close()
        http.close()
        raise ConfigError(
            f'{settings.state_dir / "memes.db"} guarda o registro das memecoins no modo "{exc.stored}". Para o modo '
            f'"{exc.requested}", renomeie o arquivo (ex.: memes-{exc.stored}.db) e rode de novo'
        ) from exc
    return MemesApp(settings, secrets, store, Journal(settings.state_dir / "memes.txt"), notifier, http)


def rpc_url(app: MemesApp) -> str:
    return app.secrets.memes_rpc_url.get_secret_value() if app.secrets.memes_rpc_url else app.settings.memes.rpc_url


def make_meme_executor(app: MemesApp) -> MemeExecutor:
    m, secrets = app.settings.memes, app.secrets
    jupiter = Jupiter(app.http)
    if m.mode == "paper":
        return PaperMemeExecutor(jupiter, network_fee_usd=m.network_fee_usd)
    if secrets.memes_live != LIVE_CONFIRMATION:
        raise ConfigError('[memes] mode = "live" exige MEMES_LIVE=I_UNDERSTAND no .env (segunda trava)')
    if secrets.memes_private_key is None:
        raise ConfigError('[memes] mode = "live" exige MEMES_PRIVATE_KEY no .env (carteira Solana dedicada)')
    wallet = load_wallet(secrets.memes_private_key.get_secret_value())
    return LiveMemeExecutor(jupiter, wallet, SolanaRpc(app.http, rpc_url(app)),
                            network_fee_usd=m.network_fee_usd, min_sol=m.min_sol_balance)


def make_vetter(app: MemesApp) -> MemeVetter | None:
    if app.settings.memes.analyst == "none":
        return None
    a = app.settings.analysis
    return MemeVetter(ClaudeCliAnalyst(model=a.claude_model, timeout=a.analyst_timeout_seconds))


def _record_failure(app: MemesApp, message: str) -> None:
    failures = app.store.consecutive_failures() + 1
    app.store.set_consecutive_failures(failures)
    if failures % FAILURE_ALERT_EVERY == 0:
        app.notifier.send(f"Memecoins: {failures} ciclos seguidos com erro. Último: {message}. Veja state/desk.log.")


def cmd_memes_cycle(app: MemesApp) -> int:
    if not app.settings.memes.enabled:
        log.info("memes: agente desligado no config.toml ([memes] enabled = false)")
        return 0
    with cycle_lock(app.settings.state_dir / "memes.lock") as acquired:
        if not acquired:
            log.info("memes: outro ciclo ainda está rodando; nada a fazer")
            return 0
        try:
            deps = MemeDeps(
                settings=app.settings.memes, store=app.store, dex=DexScreener(app.http), rugcheck=RugCheck(app.http),
                jupiter=Jupiter(app.http), vetter=make_vetter(app), executor=make_meme_executor(app),
                journal=app.journal, notifier=app.notifier, now=utcnow,
                x=FxTwitter(app.http, app.settings.memes.x_api_url) if app.settings.memes.x_enabled else None,
                tokens=JupiterTokens(app.http),
            )
        except ConfigError as exc:
            log.error("memes: configuração inválida: %s", exc)
            _record_failure(app, str(exc))
            return 2
        try:
            report = run_meme_cycle(deps)
        except Exception as exc:  # noqa: BLE001 - every crash must be counted and reported
            log.exception("memes: ciclo falhou")
            _record_failure(app, f"{type(exc).__name__}: {exc}")
            return 1
        app.store.set_consecutive_failures(0)
        log.info("memes: ciclo ok: %s", report.summary())
        print(report.summary())
        return 0


def cmd_memes_status(app: MemesApp) -> int:
    m, store, now = app.settings.memes, app.store, utcnow()
    last = store.last_equity()
    equity = last if last is not None else store.cash()
    peak = store.peak()
    drawdown = (peak - equity) / peak if peak > 0 else 0.0
    kind = "compra e venda automáticas" if m.auto_trade else "só alertas"
    print(f"Memecoins | modo: {m.mode} ({kind}) | Claude: {m.analyst} | Parado: {'SIM' if store.is_halted() else 'não'}")
    print(f"Equity: ${equity:.2f} | Caixa: ${store.cash():.2f} | Pico: ${peak:.2f} | Queda: {drawdown:.1%} | "
          f"Desliga em: ${peak * (1 - m.drawdown_halt):.2f}")
    print(f"Custos: ${store.total_costs():.2f} (24h: ${store.costs_since(now - timedelta(hours=24)):.2f}) | "
          f"Checagens do Claude 24h: {store.analyst_calls_since(now - timedelta(hours=24))}/{m.max_vets_per_day} | "
          f"Líquido: ${equity - m.bankroll_start_usd:+.2f}")
    print(f"Prova em paper: {readiness(store, m, now).summary}")
    positions = store.open_positions()
    print(f"Posições abertas ({len(positions)}/{m.max_positions}):")
    for p in positions:
        price = p.last_price_usd or p.entry_price_usd
        print(f"  - {p.symbol} ({p.name}) | custo ${p.cost_usd:.2f} | vale ${p.value_at(price):.2f} "
              f"({price / p.entry_price_usd - 1:+.0%}) | pico {p.peak_price_usd / p.entry_price_usd - 1:+.0%} | "
              f"aberta {p.opened_at:%m-%d %H:%M} | https://fomo.family/tokens/solana/{p.mint}")
    print("Últimos trades:")
    for t in store.trades(limit=10):
        print(f"  - {t['ts']:%Y-%m-%d %H:%M} {t['side']} {t['symbol']} ${t['usd']:.2f} "
              f"(caixa {t['cash_delta']:+.2f}) {t['reason']}")
    return 0


def cmd_memes_resume(app: MemesApp) -> int:
    last = app.store.last_equity()
    equity = last if last is not None else app.store.cash()
    app.store.set_halted(False)
    app.store.reset_peak(equity)
    note = f"Agente de memecoins religado manualmente. Novo pico de referência: ${equity:.2f}."
    app.journal.append(format_note("RETOMADA", note, utcnow()))
    print(note)
    return 0


def cmd_memes_live_check(app: MemesApp) -> int:
    m, secrets, ok = app.settings.memes, app.secrets, True
    if m.mode != "live":
        print('config.toml: [memes] mode ainda é "paper"')
        ok = False
    if secrets.memes_live != LIVE_CONFIRMATION:
        print("MEMES_LIVE não está definido como I_UNDERSTAND no .env")
        ok = False
    if app.secrets.telegram_bot_token is None or not app.secrets.telegram_chat_id:
        print("Telegram não configurado: no modo real, avisos de ordem incerta e de venda que falhou precisam chegar "
              "até você. Rode: python -m desk telegram-setup")
        ok = False
    if secrets.memes_private_key is None:
        print("MEMES_PRIVATE_KEY ausente no .env")
        return _verdict(False)
    try:
        wallet = load_wallet(secrets.memes_private_key.get_secret_value())
    except ConfigError as exc:
        print(f"Carteira: {exc}")
        return _verdict(False)
    print(f"Carteira (endereço público): {wallet.owner}")
    rpc = SolanaRpc(app.http, rpc_url(app))
    try:
        sol = rpc.sol_balance(wallet.owner)
        usdc = raw_usdc(rpc.token_balance(wallet.owner, USDC_MINT))
    except FETCH_ERRORS as exc:
        print(f"Não consegui ler o saldo pelo RPC: {type(exc).__name__}")
        return _verdict(False)
    print(f"Saldo: {sol:.4f} SOL (mínimo para taxas: {m.min_sol_balance}) | USDC: ${usdc:.2f} | "
          f"caixa no registro do agente: ${app.store.cash():.2f}")
    if sol < m.min_sol_balance:
        print("Pouco SOL para pagar as taxas da rede.")
        ok = False
    if usdc < m.min_trade_usd:
        print("Sem USDC para comprar.")
        ok = False
    proof = readiness(app.store, m, utcnow())
    if not proof.ready:
        print(f"Aviso: o paper ainda não provou que o agente se paga ({proof.summary}). "
              "A recomendação é esperar (docs/PENDENCIAS.md, D8); a decisão é sua.")
    return _verdict(ok)


def _verdict(ok: bool) -> int:
    print("PRONTO para modo real nas memecoins." if ok else "NÃO está pronto para modo real nas memecoins.")
    return 0 if ok else 1


def run_command(command: str, app: MemesApp) -> int:
    handlers = {"memes": cmd_memes_cycle, "memes-status": cmd_memes_status, "memes-resume": cmd_memes_resume,
                "memes-live-check": cmd_memes_live_check}
    return handlers[command](app)

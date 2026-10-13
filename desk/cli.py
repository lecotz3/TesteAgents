"""Command line: python -m desk {cycle,status,monthly,resume,analyze,live-check,dashboard,memes,memes-status,...}."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import httpx

from desk.analyst import make_analyst
from desk.analyst.base import AnalystError
from desk.clob import BookClient
from desk.config import ConfigError, Secrets, Settings, load_secrets, load_settings, merged_env
from desk.cycle import Deps, run_cycle
from desk.executor import LIVE_CONFIRMATION, make_executor
from desk.executor.base import ExecutionError
from desk.dashboard import make_server
from desk.economy import economics
from desk.executor.sdk import SdkTrader
from desk.gamma import GammaClient
from desk.geoblock import check_geoblock
from desk.journal import Journal, format_note
from desk.lock import cycle_lock
from desk.notify import Notifier
from desk.store import ModeMismatch, Store

log = logging.getLogger("desk")
USER_AGENT = "polymarket-desk/0.1"
FAILURE_ALERT_EVERY = 3


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class App:
    settings: Settings
    secrets: Secrets
    store: Store
    journal: Journal
    notifier: Notifier
    http: httpx.Client


def load_runtime(config_path: Path, env_path: Path) -> tuple[Settings, Secrets, httpx.Client, Notifier]:
    """What every agent shares: settings, secrets, one HTTP client and the alert channel."""
    settings = load_settings(config_path)
    if not settings.state_dir.is_absolute():
        settings.state_dir = config_path.resolve().parent / settings.state_dir
    secrets = load_secrets(merged_env(env_path))
    http = httpx.Client(timeout=20.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    token = secrets.telegram_bot_token.get_secret_value() if secrets.telegram_bot_token else None
    return settings, secrets, http, Notifier(http, token, secrets.telegram_chat_id)


def build_app(config_path: Path, env_path: Path) -> App:
    settings, secrets, http, notifier = load_runtime(config_path, env_path)
    store = Store(settings.state_dir / "desk.db", start_cash=settings.bankroll_start_usd)
    try:
        store.ensure_mode(settings.mode)
    except ModeMismatch as exc:
        store.close()
        http.close()
        raise ConfigError(
            f'a pasta {settings.state_dir} guarda o registro do modo "{exc.stored}". Para o modo "{exc.requested}", '
            f"renomeie-a (ex.: state-{exc.stored}) ou aponte state_dir para outra pasta no config.toml"
        ) from exc
    journal = Journal(settings.state_dir / "trades.txt")
    return App(settings, secrets, store, journal, notifier, http)


def setup_logging(state_dir: Path) -> None:
    logger = logging.getLogger("desk")
    if logger.handlers:
        return
    state_dir.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = RotatingFileHandler(state_dir / "desk.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    if sys.stderr is not None:  # pythonw (Task Scheduler) has no console
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        logger.addHandler(stream)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines would print the Telegram URL


def _equity(app: App) -> float:
    equity = app.store.last_equity()
    return equity if equity is not None else app.store.cash()


def _record_failure(app: App, message: str) -> None:
    failures = app.store.consecutive_failures() + 1
    app.store.set_consecutive_failures(failures)
    if failures % FAILURE_ALERT_EVERY == 0:
        app.notifier.send(f"Desk: {failures} ciclos seguidos com erro. Último: {message}. Veja state/desk.log.")


def cmd_cycle(app: App) -> int:
    with cycle_lock(app.settings.state_dir / "desk.lock") as acquired:
        if not acquired:
            log.info("outro ciclo ainda está rodando; nada a fazer")
            return 0
        try:
            deps = Deps(
                settings=app.settings, store=app.store, markets=GammaClient(app.http), books=BookClient(app.http),
                analyst=make_analyst(app.settings.analysis),
                executor=make_executor(app.settings, app.secrets, app.http),
                journal=app.journal, notifier=app.notifier, now=utcnow,
            )
        except (ConfigError, ExecutionError) as exc:
            log.error("configuração inválida: %s", exc)
            _record_failure(app, str(exc))
            return 2
        except Exception as exc:  # noqa: BLE001 - e.g. the live SDK failing to connect; never die silently
            log.exception("não foi possível montar o ciclo")
            _record_failure(app, f"{type(exc).__name__}: {exc}")
            return 2
        try:
            report = run_cycle(deps)
        except Exception as exc:  # noqa: BLE001 - every crash must be counted and reported
            log.exception("ciclo falhou")
            _record_failure(app, f"{type(exc).__name__}: {exc}")
            return 1
        app.store.set_consecutive_failures(0)
        log.info("ciclo ok: %s", report.summary())
        print(report.summary())
        return 0


def cmd_status(app: App) -> int:
    s, store = app.settings, app.store
    equity, peak = _equity(app), store.peak()
    drawdown = (peak - equity) / peak if peak > 0 else 0.0
    print(f"Modo: {s.mode} | Analista: {s.analysis.analyst} | Parado: {'SIM' if store.is_halted() else 'não'}")
    print(f"Equity: ${equity:.2f} | Caixa: ${store.cash():.2f} | Pico: ${peak:.2f} | Drawdown: {drawdown:.1%}")
    e = economics(store, s, utcnow())
    runway = f"{e.runway_days:.0f} dias" if e.runway_days is not None else "sem gasto recente"
    print(f"Custos de operação: ${e.costs_total:.2f} (24h: ${e.costs_24h:.2f} de ${e.budget_24h:.2f} permitidos) | "
          f"Resultado de trading: ${e.trading_pnl:+.2f} | Líquido: ${e.net:+.2f}")
    print(f"Linha de desligamento: ${e.shutdown_line:.2f} | Fôlego sem lucro: {runway}")
    positions = store.open_positions()
    print(f"Posições abertas ({len(positions)}/{s.risk.max_open_positions}):")
    for p in positions:
        print(f"  - {p.question} | {p.outcome} | {p.shares:.2f} cotas @ {p.avg_price:.3f} | "
              f"justo {p.entry_fair_prob:.0%} | saída: {p.exit_condition}")
    print("Últimos trades:")
    for t in store.trades(limit=10):
        print(f"  - {t.ts:%Y-%m-%d %H:%M} {t.action} {t.shares:.2f} @ {t.price:.3f} "
              f"caixa {t.cash_delta:+.2f} ({t.reason}) mercado {t.market_id}")
    return 0


def cmd_monthly(app: App, force: bool = False, now: datetime | None = None) -> int:
    now = now or utcnow()
    month = now.strftime("%Y-%m")
    if not force and app.store.get_meta("last_monthly_report") == month:
        return 0
    m = app.settings.monthly
    equity, cash = _equity(app), app.store.cash()
    target = m.subscription_cost_usd + m.monthly_reserve_usd
    tag = "[PAPER] " if app.settings.mode == "paper" else ""
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    before = app.store.equity_before(month_start)
    start_equity = before if before is not None else app.settings.bankroll_start_usd
    month_costs = app.store.costs_since(month_start)
    month_net = equity - start_equity
    verdict = "se pagou" if month_net >= 0 else "não se pagou"
    economy = (f"No mês: trading ${month_net + month_costs:+.2f}, custos ${month_costs:.2f}, "
               f"líquido ${month_net:+.2f}: {verdict}. ")
    if equity >= target:
        max_withdraw = max(0.0, min(cash, equity - m.monthly_reserve_usd))
        message = (f"{tag}Relatório mensal {month}: equity ${equity:.2f}. {economy}Cobre a assinatura. "
                   f"Saque sugerido: ${m.subscription_cost_usd:.2f} (máximo mantendo a reserva de "
                   f"${m.monthly_reserve_usd:.2f}: ${max_withdraw:.2f}). O saque é manual.")
    else:
        message = (f"{tag}Relatório mensal {month}: equity ${equity:.2f}. {economy}Faltam ${target - equity:.2f} para cobrir "
                   f"assinatura (${m.subscription_cost_usd:.2f}) + reserva (${m.monthly_reserve_usd:.2f}). Nada a sacar.")
    app.journal.append(format_note("RELATÓRIO MENSAL", message, now))
    app.notifier.send(message)
    print(message)
    app.store.set_meta("last_monthly_report", month)
    return 0


def cmd_resume(app: App) -> int:
    equity = _equity(app)
    app.store.set_halted(False)
    app.store.reset_peak(equity)
    note = f"Trading retomado manualmente. Novo pico de referência: ${equity:.2f}."
    app.journal.append(format_note("RETOMADA", note, utcnow()))
    print(note)
    return 0


def cmd_analyze(app: App, ref: str) -> int:
    gamma = GammaClient(app.http)
    market = gamma.market_by_id(ref) if ref.isdigit() else gamma.market_by_slug(ref)
    if market is None:
        print(f"Mercado não encontrado: {ref}")
        return 1
    try:
        a = make_analyst(app.settings.analysis).estimate(market, utcnow())
    except AnalystError as exc:
        print(f"Analista falhou: {exc}")
        return 1
    print(json.dumps({
        "mercado": market.question,
        "resultados": market.outcomes,
        "precos_gamma": market.outcome_prices,
        "fair_prob_resultado0": a.fair_prob,
        "diferenca_pp_resultado0": round((a.fair_prob - market.outcome_prices[0]) * 100, 1),
        "confianca": a.confidence,
        "fonte_oficial": a.official_resolution_source,
        "condicao_de_saida": a.exit_condition,
        "raciocinio": a.reasoning,
        "evidencias": [asdict(e) for e in a.evidence],
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_live_check(app: App) -> int:
    ok = True
    geo = check_geoblock(app.http)
    print(f"Geobloqueio: {'BLOQUEADO' if geo.blocked else 'liberado'} "
          f"(país={geo.country or '?'} região={geo.region or '?'}){' erro=' + geo.error if geo.error else ''}")
    ok &= not geo.blocked
    if app.settings.mode != "live":
        print('config.toml: mode ainda é "paper"')
        ok = False
    if app.secrets.desk_live != LIVE_CONFIRMATION:
        print("DESK_LIVE não está definido como I_UNDERSTAND no .env")
        ok = False
    if app.secrets.poly_private_key is None:
        print("POLY_PRIVATE_KEY ausente no .env")
        ok = False
    else:
        try:
            trader = SdkTrader.connect(app.secrets.poly_private_key.get_secret_value(), app.secrets.poly_wallet)
            balance = trader.usdc_balance()
            print(f"Saldo pUSD na carteira: ${balance:.2f} | Caixa no registro do desk: ${app.store.cash():.2f}")
            for problem in trader.account_problems():
                print(f"Conta: {problem}")
                ok = False
        except Exception as exc:  # noqa: BLE001 - report any client failure to the user
            print(f"Falha ao conectar no Polymarket: {type(exc).__name__}: {exc}")
            ok = False
    print("PRONTO para modo real." if ok else "NÃO está pronto para modo real.")
    return 0 if ok else 1


def cmd_dashboard(app: App, port: int = 8765, open_browser: bool = True) -> int:
    try:
        server = make_server(app.settings.state_dir, app.settings, port=port)
    except OSError as exc:
        print(f"Não consegui abrir a porta {port} ({exc}). O painel já está aberto? Tente --port 8766.")
        return 2
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Painel em {url} (só nesta máquina). Feche esta janela ou aperte Ctrl+C para parar.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def run_memes_command(command: str, config_path: Path, env_path: Path) -> int:
    from desk.memes import app as memes

    try:
        app = memes.build_memes_app(config_path, env_path)
    except ConfigError as exc:
        print(f"Erro de configuração: {exc}")
        return 2
    setup_logging(app.settings.state_dir)
    try:
        return memes.run_command(command, app)
    finally:
        app.close()


def main(argv: list[str] | None = None) -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="desk", description="Polymarket desk (paper/live)")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("cycle", help="roda um ciclo do loop")
    sub.add_parser("status", help="mostra banca, posições e últimos trades")
    monthly = sub.add_parser("monthly", help="relatório mensal de autofinanciamento (1x por mês)")
    monthly.add_argument("--force", action="store_true", help="relata mesmo se já relatou este mês")
    sub.add_parser("resume", help="retoma após parada por drawdown")
    analyze = sub.add_parser("analyze", help="roda o analista em um mercado (id ou slug), sem operar")
    analyze.add_argument("market")
    sub.add_parser("live-check", help="verifica se o modo real está pronto (não envia ordens)")
    dashboard = sub.add_parser("dashboard", help="abre o painel local no navegador (só leitura)")
    dashboard.add_argument("--port", type=int, default=8765)
    dashboard.add_argument("--no-browser", action="store_true", help="não abre o navegador sozinho")
    sub.add_parser("memes", help="roda um ciclo do agente de memecoins (Solana)")
    sub.add_parser("memes-status", help="banca, posições e trades do agente de memecoins")
    sub.add_parser("memes-resume", help="religa o agente de memecoins após parada por queda")
    sub.add_parser("memes-live-check", help="verifica se o modo real das memecoins está pronto (não negocia)")
    args = parser.parse_args(argv)

    if args.command.startswith("memes"):
        return run_memes_command(args.command, args.config, args.env_file)

    try:
        app = build_app(args.config, args.env_file)
    except ConfigError as exc:
        print(f"Erro de configuração: {exc}")
        return 2
    setup_logging(app.settings.state_dir)
    try:
        if args.command == "cycle":
            return cmd_cycle(app)
        if args.command == "status":
            return cmd_status(app)
        if args.command == "monthly":
            return cmd_monthly(app, force=args.force)
        if args.command == "resume":
            return cmd_resume(app)
        if args.command == "analyze":
            return cmd_analyze(app, args.market)
        if args.command == "dashboard":
            return cmd_dashboard(app, port=args.port, open_browser=not args.no_browser)
        return cmd_live_check(app)
    finally:
        app.store.close()
        app.http.close()

"""python -m desk telegram-setup: checks the bot token, finds your chat, saves TELEGRAM_CHAT_ID and sends a test."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import httpx

from desk.config import load_secrets, merged_env

API = "https://api.telegram.org"
CHAT_UPDATES = ("message", "edited_message", "channel_post", "my_chat_member")


@dataclass(frozen=True)
class Chat:
    id: str
    label: str


def find_chats(updates: list[dict]) -> list[Chat]:
    """Every chat that talked to the bot, newest first, each once."""
    seen: dict[str, Chat] = {}
    for upd in sorted(updates, key=lambda u: u.get("update_id", 0), reverse=True):
        for kind in CHAT_UPDATES:
            chat = (upd.get(kind) or {}).get("chat")
            if not chat or "id" not in chat:
                continue
            name = chat.get("title") or chat.get("first_name") or chat.get("username") or "?"
            seen.setdefault(str(chat["id"]), Chat(str(chat["id"]), f"{name} ({chat.get('type', '?')})"))
    return list(seen.values())


def set_env_value(path: Path, key: str, value: str) -> None:
    """Set KEY=value in a .env file, keeping every other line as it is."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key:
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class _Bot:
    def __init__(self, http: httpx.Client, token: str):
        self._http = http
        self._base = f"{API}/bot{token}"

    def call(self, method: str, payload: dict | None = None) -> object:
        if payload is None:
            resp = self._http.get(f"{self._base}/{method}")
        else:
            resp = self._http.post(f"{self._base}/{method}", json=payload)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise ValueError(data.get("description") or "resposta sem ok")
        return data.get("result")


def run_setup(env_path: Path, http: httpx.Client) -> int:
    if not env_path.exists():
        example = env_path.parent / ".env.example"
        if example.exists():
            shutil.copyfile(example, env_path)
        else:
            env_path.write_text("TELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=\n", encoding="utf-8")
        print(f"Criei {env_path} (ele nunca vai para o git).")
    secrets = load_secrets(merged_env(env_path))
    if secrets.telegram_bot_token is None:
        print("Falta o token do bot. No Telegram, fale com @BotFather → /newbot → escolha nome e usuário → copie o token.")
        print(f"Abra {env_path} (ex.: notepad .env), cole na linha TELEGRAM_BOT_TOKEN= e salve.")
        print("Depois mande \"oi\" para o seu bot e rode de novo: .venv\\Scripts\\python -m desk telegram-setup")
        return 2
    bot = _Bot(http, secrets.telegram_bot_token.get_secret_value())
    try:
        me = bot.call("getMe")
    except httpx.HTTPStatusError as exc:
        # only the status: the exception text carries the URL, and the URL carries the token
        print(f"O Telegram recusou o token (HTTP {exc.response.status_code}). Confira TELEGRAM_BOT_TOKEN no .env.")
        return 2
    except (httpx.HTTPError, ValueError) as exc:
        print(f"Não consegui falar com o Telegram ({type(exc).__name__}). Confira a internet e tente de novo.")
        return 2
    username = f"@{me.get('username', '?')}"
    print(f"Token ok: bot {username}.")
    try:
        chats = find_chats(bot.call("getUpdates"))
    except (httpx.HTTPError, ValueError) as exc:
        print(f"Não consegui ler as mensagens do bot ({type(exc).__name__}).")
        return 2
    if not chats:
        print(f"O bot ainda não recebeu mensagem. Abra {username} no Telegram, toque em Iniciar, mande \"oi\" e rode de novo.")
        return 1
    chat = chats[0]
    if len(chats) > 1:
        print("Outras conversas com o bot: " + ", ".join(c.label for c in chats[1:]) + ". Usei a mais recente.")
    set_env_value(env_path, "TELEGRAM_CHAT_ID", chat.id)
    print(f"Conversa escolhida: {chat.label}. Salvei TELEGRAM_CHAT_ID em {env_path}.")
    try:
        bot.call("sendMessage", {"chat_id": chat.id, "text": (
            "Desk conectado. Você vai receber aqui: compras e vendas (Polymarket e memecoins), memecoins aprovadas, "
            "desligamento por queda, falhas repetidas e o relatório mensal.")})
    except (httpx.HTTPError, ValueError) as exc:
        print(f"Salvei a conversa, mas a mensagem de teste falhou ({type(exc).__name__}).")
        return 1
    print("Mensagem de teste enviada: confira o Telegram. Os próximos ciclos já mandam os avisos.")
    return 0

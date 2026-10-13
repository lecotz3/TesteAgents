import json

import httpx
import respx

from desk import cli
from desk.telegram_setup import find_chats, set_env_value

TOKEN = "123456:ABCsecretTOKEN"
API = f"https://api.telegram.org/bot{TOKEN}"


def update(chat_id, kind="message", chat_type="private", name="Leo", uid=1):
    chat = {"id": chat_id, "type": chat_type, "first_name": name} if chat_type == "private" else \
        {"id": chat_id, "type": chat_type, "title": name}
    return {"update_id": uid, kind: {"chat": chat, "date": 1700000000 + uid, "text": "oi"}}


def test_find_chats_newest_first_without_repeats():
    chats = find_chats([update(10, uid=1), update(-20, chat_type="group", name="Turma", uid=2), update(10, uid=3),
                        update(30, kind="my_chat_member", chat_type="supergroup", name="Alertas", uid=4)])
    assert [(c.id, c.label) for c in chats] == [("30", "Alertas (supergroup)"), ("10", "Leo (private)"),
                                                ("-20", "Turma (group)")]


def test_set_env_value_replaces_or_appends_and_keeps_the_rest(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comentário\nTELEGRAM_CHAT_ID=\nPOLY_PRIVATE_KEY=0xabc\n", encoding="utf-8")
    set_env_value(env, "TELEGRAM_CHAT_ID", "987")
    set_env_value(env, "NEW_KEY", "x")
    assert env.read_text(encoding="utf-8") == "# comentário\nTELEGRAM_CHAT_ID=987\nPOLY_PRIVATE_KEY=0xabc\nNEW_KEY=x\n"


def run(tmp_path, env_text, capsys):
    env = tmp_path / ".env"
    if env_text is not None:
        env.write_text(env_text, encoding="utf-8")
    code = cli.main(["--config", str(tmp_path / "config.toml"), "--env-file", str(env), "telegram-setup"])
    return code, capsys.readouterr().out, env


def test_without_token_it_explains_botfather(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    code, out, _ = run(tmp_path, "TELEGRAM_BOT_TOKEN=\n", capsys)
    assert code == 2 and "@BotFather" in out


@respx.mock
def test_invalid_token_is_reported_without_leaking_it(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    respx.get(API + "/getMe").mock(return_value=httpx.Response(401, json={"ok": False, "description": "Unauthorized"}))
    code, out, _ = run(tmp_path, f"TELEGRAM_BOT_TOKEN={TOKEN}\n", capsys)
    assert code == 2 and "token" in out.lower() and TOKEN not in out


@respx.mock
def test_no_message_yet_asks_to_talk_to_the_bot(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    respx.get(API + "/getMe").mock(return_value=httpx.Response(200, json={"ok": True, "result": {"username": "meu_desk_bot"}}))
    respx.get(API + "/getUpdates").mock(return_value=httpx.Response(200, json={"ok": True, "result": []}))
    code, out, _ = run(tmp_path, f"TELEGRAM_BOT_TOKEN={TOKEN}\n", capsys)
    assert code == 1 and "@meu_desk_bot" in out


@respx.mock
def test_success_saves_the_chat_and_sends_a_test_message(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    respx.get(API + "/getMe").mock(return_value=httpx.Response(200, json={"ok": True, "result": {"username": "meu_desk_bot"}}))
    respx.get(API + "/getUpdates").mock(return_value=httpx.Response(200, json={"ok": True, "result": [update(4242)]}))
    send = respx.post(API + "/sendMessage").mock(return_value=httpx.Response(200, json={"ok": True}))
    code, out, env = run(tmp_path, f"TELEGRAM_BOT_TOKEN={TOKEN}\nTELEGRAM_CHAT_ID=\nDESK_LIVE=\n", capsys)
    assert code == 0
    assert env.read_text(encoding="utf-8") == f"TELEGRAM_BOT_TOKEN={TOKEN}\nTELEGRAM_CHAT_ID=4242\nDESK_LIVE=\n"
    assert json.loads(send.calls[0].request.read())["chat_id"] == "4242"
    assert "Leo" in out and "@meu_desk_bot" in out and TOKEN not in out


@respx.mock
def test_network_failure_is_reported_without_the_url(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    respx.get(API + "/getMe").mock(side_effect=httpx.ConnectError("boom " + API))
    code, out, _ = run(tmp_path, f"TELEGRAM_BOT_TOKEN={TOKEN}\n", capsys)
    assert code == 2 and TOKEN not in out and "ConnectError" in out


def test_creates_env_from_example_when_missing(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    (tmp_path / ".env.example").write_text("# exemplo\nTELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=\n", encoding="utf-8")
    code, out, env = run(tmp_path, None, capsys)
    assert code == 2 and env.read_text(encoding="utf-8") == "# exemplo\nTELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=\n"
    assert str(env) in out


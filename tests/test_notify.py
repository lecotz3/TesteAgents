import json

import httpx
import respx

from desk.notify import Notifier


def test_disabled_notifier_only_logs(caplog):
    Notifier().send("olá")
    assert "olá" in caplog.text


@respx.mock
def test_sends_telegram_message():
    route = respx.post("https://api.telegram.org/botTOKEN/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    with httpx.Client() as http:
        Notifier(http, "TOKEN", "42").send("teste")
    assert route.called
    assert json.loads(route.calls[0].request.content) == {"chat_id": "42", "text": "teste"}


@respx.mock
def test_telegram_failure_is_swallowed_and_token_not_logged(caplog):
    respx.post("https://api.telegram.org/botTOKEN/sendMessage").mock(return_value=httpx.Response(500))
    with httpx.Client() as http:
        Notifier(http, "TOKEN", "42").send("teste")
    assert "TOKEN" not in caplog.text

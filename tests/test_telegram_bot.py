import io
import json
import threading
import urllib.error
import urllib.parse
from datetime import datetime, timedelta

import pytest

from widget_reloj_clima import secrets_store, telegram_bot
from widget_reloj_clima.reminders import Reminder, ReminderStore
from widget_reloj_clima.telegram_bot import (
    TelegramBot,
    TelegramClient,
    TelegramError,
    format_alert,
    new_pairing_code,
    validate_token,
)

TOKEN = "123456789:AAH" + "x" * 32
NOW = datetime(2026, 11, 21, 12, 0)
OWNER = 555
STRANGER = 999


# ---------- utilidades ----------
class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class RecordingOpener:
    def __init__(self, result=True, ok=True, exc=None):
        self.requests = []
        self.result, self.ok, self.exc = result, ok, exc

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if self.exc:
            raise self.exc
        return FakeResponse(json.dumps({"ok": self.ok, "result": self.result,
                                        "description": "Bad Request: chat not found"}).encode())


class FakeClient:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send_message(self, chat_id, text):
        if self.fail:
            raise TelegramError("caído")
        self.sent.append((chat_id, text))


@pytest.fixture
def setup(tmp_path):
    store = ReminderStore(tmp_path / "r.json", now=NOW)
    client = FakeClient()
    linked = {"chat": None}
    bot = TelegramBot(client, store, lambda: linked["chat"], lambda c: linked.update(chat=c), now=lambda: NOW)
    return bot, client, store, linked


def msg(text, chat=OWNER):
    return {"update_id": 1, "message": {"chat": {"id": chat}, "text": text}}


def last_reply(client):
    return client.sent[-1][1]


# ---------- token y cliente HTTP ----------
def test_validate_token():
    assert validate_token(f"  {TOKEN} ") == TOKEN
    for bad in ("", "abc", "123:short", "abc:" + "x" * 35):
        with pytest.raises(ValueError):
            validate_token(bad)


def test_pairing_code_is_six_digits():
    code = new_pairing_code()
    assert len(code) == 6 and code.isdigit()


def test_send_message_posts_form_data():
    opener = RecordingOpener()
    TelegramClient(TOKEN, opener).send_message(OWNER, "hola")

    request, timeout = opener.requests[0]
    assert request.full_url.endswith("/sendMessage")
    assert urllib.parse.parse_qs(request.data.decode()) == {"chat_id": [str(OWNER)], "text": ["hola"]}
    assert timeout == telegram_bot.SEND_TIMEOUT_S


def test_get_updates_and_get_me():
    opener = RecordingOpener(result=[{"update_id": 7}])
    client = TelegramClient(TOKEN, opener)
    assert client.get_updates(offset=5) == [{"update_id": 7}]
    assert "offset=5" in opener.requests[0][0].data.decode()
    opener.result = {"username": "mi_bot"}
    assert client.get_me()["username"] == "mi_bot"


def test_get_updates_ignores_non_list_result():
    assert TelegramClient(TOKEN, RecordingOpener(result={"raro": 1})).get_updates(None) == []


@pytest.mark.parametrize(
    ("exc", "message"),
    [
        (urllib.error.HTTPError("u", 401, "Unauthorized", {}, None), "rechazó el token"),
        (urllib.error.HTTPError("u", 500, "boom", {}, None), "error 500"),
        (urllib.error.URLError("sin red"), "Sin conexión"),
        (TimeoutError(), "Sin conexión"),
    ],
)
def test_client_errors_never_leak_token(exc, message):
    with pytest.raises(TelegramError, match=message) as info:
        TelegramClient(TOKEN, RecordingOpener(exc=exc)).send_message(OWNER, "x")
    assert TOKEN not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_client_api_error_and_bad_json():
    with pytest.raises(TelegramError, match="chat not found"):
        TelegramClient(TOKEN, RecordingOpener(ok=False)).send_message(OWNER, "x")

    def bad_json(request, timeout):
        return FakeResponse(b"<html>")

    with pytest.raises(TelegramError, match="no válida"):
        TelegramClient(TOKEN, bad_json).send_message(OWNER, "x")


# ---------- vinculación ----------
def test_unlinked_bot_asks_for_pairing(setup):
    bot, client, _, linked = setup
    bot.handle_update(msg("hola"))
    assert "privado" in last_reply(client) and linked["chat"] is None


def test_correct_code_links_chat(setup):
    bot, client, _, linked = setup
    bot.handle_update(msg(f"/vincular {bot.pairing_code}"))
    assert linked["chat"] == OWNER
    assert "vinculado" in last_reply(client)


def test_wrong_codes_lock_pairing(setup):
    bot, client, _, linked = setup
    for _ in range(telegram_bot.MAX_PAIRING_ATTEMPTS):
        bot.handle_update(msg("/vincular 000000" if bot.pairing_code != "000000" else "/vincular 111111"))
    assert bot.pairing_locked
    bot.handle_update(msg(f"/vincular {bot.pairing_code}"))
    assert linked["chat"] is None
    assert "Demasiados intentos" in last_reply(client)


def test_messages_from_other_chats_are_ignored(setup):
    bot, client, store, linked = setup
    linked["chat"] = OWNER
    bot.handle_update(msg("hoy a las 22 robar datos", chat=STRANGER))
    assert client.sent == [] and store.pending() == ()


@pytest.mark.parametrize("update", [{}, {"message": "x"}, {"message": {"chat": {"id": "a"}, "text": "hola"}},
                                    {"message": {"chat": {"id": OWNER}}}])
def test_malformed_updates_are_ignored(setup, update):
    bot, client, _, _ = setup
    bot.handle_update(update)
    assert client.sent == []


# ---------- comandos ----------
@pytest.fixture
def linked_bot(setup):
    bot, client, store, linked = setup
    linked["chat"] = OWNER
    return bot, client, store


def test_plain_text_creates_reminder(linked_bot):
    bot, client, store = linked_bot
    bot.handle_update(msg("hoy a las 22 imprimir moldes"))
    reminder = store.pending()[0]
    assert (reminder.text, reminder.due, reminder.source) == ("Imprimir moldes", NOW.replace(hour=22), "telegram")
    assert last_reply(client) == "✅ Te avisaré hoy 22:00: Imprimir moldes (#1)"


def test_recordar_command(linked_bot):
    bot, client, store = linked_bot
    bot.handle_update(msg("/recordar mañana 9:30 llamar al banco"))
    assert store.pending()[0].text == "Llamar al banco"


def test_invalid_reminder_explains_why(linked_bot):
    bot, client, store = linked_bot
    bot.handle_update(msg("imprimir moldes"))
    assert "Indica la hora" in last_reply(client) and store.pending() == ()


def test_list_and_delete(linked_bot):
    bot, client, store = linked_bot
    bot.handle_update(msg("/lista"))
    assert "No tienes" in last_reply(client)
    store.add("Uno", NOW + timedelta(hours=1), now=NOW)
    bot.handle_update(msg("/lista@mi_bot"))
    assert "#1 · hoy 13:00 · Uno" in last_reply(client)
    bot.handle_update(msg("/borrar #1"))
    assert "Borrado: Uno" in last_reply(client) and store.pending() == ()
    bot.handle_update(msg("/borrar 1"))
    assert "No existe" in last_reply(client)
    bot.handle_update(msg("/borrar"))
    assert "Indica el número" in last_reply(client)


def test_list_is_truncated(linked_bot, monkeypatch):
    bot, client, store = linked_bot
    monkeypatch.setattr(telegram_bot, "MAX_LIST_ITEMS", 2)
    for i in range(4):
        store.add(f"r{i}", NOW + timedelta(hours=i + 1), now=NOW)
    bot.handle_update(msg("/lista"))
    assert "… y 2 más" in last_reply(client)


@pytest.mark.parametrize("text", ["/start", "/ayuda", "/help", "/desconocido"])
def test_help_commands(linked_bot, text):
    bot, client, _ = linked_bot
    bot.handle_update(msg(text))
    assert "/lista" in last_reply(client)


def test_reply_failure_is_swallowed(tmp_path):
    store = ReminderStore(tmp_path / "r.json", now=NOW)
    bot = TelegramBot(FakeClient(fail=True), store, lambda: OWNER, lambda c: None, now=lambda: NOW)
    bot.handle_update(msg("/ayuda"))  # no lanza excepción


# ---------- avisos ----------
def test_notify_sends_alert_when_linked(linked_bot):
    bot, client, _ = linked_bot
    reminder = Reminder(1, "Imprimir moldes", NOW, NOW)
    assert bot.notify(reminder)
    assert client.sent[-1] == (OWNER, "⏰ Imprimir moldes\n(hoy 12:00)")


def test_notify_without_link_or_on_error(setup, tmp_path):
    bot, client, _, _ = setup
    assert not bot.notify(Reminder(1, "x", NOW, NOW))
    failing = TelegramBot(FakeClient(fail=True), ReminderStore(tmp_path / "f.json", now=NOW),
                          lambda: OWNER, lambda c: None, now=lambda: NOW)
    assert not failing.notify(Reminder(1, "x", NOW, NOW))


def test_format_alert_marks_late_reminders():
    late = Reminder(1, "Regar", NOW - timedelta(hours=3), NOW - timedelta(days=1))
    assert format_alert(late, NOW) == "⏰ Regar\n(era para hoy 09:00)"


# ---------- ciclo de sondeo ----------
class ScriptedClient(FakeClient):
    def __init__(self, bot_holder, batches):
        super().__init__()
        self.batches = batches
        self.offsets = []
        self.bot_holder = bot_holder

    def get_updates(self, offset, timeout=0):
        self.offsets.append(offset)
        if not self.batches:
            self.bot_holder["bot"].stop()
            return []
        batch = self.batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return batch


def test_polling_loop_advances_offset_and_survives_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(telegram_bot, "MAX_BACKOFF_S", 0)
    holder = {}
    batches = [
        [{"update_id": 10, "message": {"chat": {"id": OWNER}, "text": "/ayuda"}}],
        TelegramError("red caída"),
        [{"update_id": 11, "message": None}, {"update_id": 12, "message": {"chat": {"id": OWNER}, "text": "/lista"}}],
    ]
    client = ScriptedClient(holder, batches)
    bot = TelegramBot(client, ReminderStore(tmp_path / "r.json", now=NOW), lambda: OWNER, lambda c: None,
                      now=lambda: NOW)
    holder["bot"] = bot
    bot._stop.wait = lambda seconds: None  # sin esperas reales en el test

    bot._run()

    assert client.offsets == [None, 11, 11, 13]
    assert len(client.sent) == 2


def test_start_and_stop_thread(tmp_path):
    holder = {}
    client = ScriptedClient(holder, [])
    bot = TelegramBot(client, ReminderStore(tmp_path / "r.json", now=NOW), lambda: OWNER, lambda c: None)
    holder["bot"] = bot
    bot.start()
    bot._thread.join(timeout=2)
    assert not bot._thread.is_alive()


def test_handler_exception_does_not_stop_loop(tmp_path, monkeypatch):
    holder = {}
    client = ScriptedClient(holder, [[{"update_id": 1, "message": {"chat": {"id": OWNER}, "text": "hola"}}]])
    bot = TelegramBot(client, ReminderStore(tmp_path / "r.json", now=NOW), lambda: OWNER, lambda c: None)
    holder["bot"] = bot
    monkeypatch.setattr(bot, "handle_update", lambda u: (_ for _ in ()).throw(RuntimeError("bug")))
    bot._run()
    assert client.offsets == [None, 2]


# ---------- almacén del token ----------
class MemoryKeyring:
    def __init__(self):
        self.data = {}

    def get_password(self, service, user):
        return self.data.get((service, user))

    def set_password(self, service, user, value):
        self.data[(service, user)] = value

    def delete_password(self, service, user):
        from keyring.errors import PasswordDeleteError

        if (service, user) not in self.data:
            raise PasswordDeleteError("no existe")
        del self.data[(service, user)]


@pytest.fixture
def memory_keyring(monkeypatch):
    kr = MemoryKeyring()
    monkeypatch.setattr(secrets_store, "keyring", kr)
    monkeypatch.delenv(secrets_store.TOKEN_ENV_VAR, raising=False)
    return kr


def test_token_roundtrip(memory_keyring):
    assert secrets_store.load_token() is None
    secrets_store.save_token(TOKEN)
    assert secrets_store.load_token() == TOKEN
    secrets_store.delete_token()
    secrets_store.delete_token()  # borrar dos veces no falla
    assert secrets_store.load_token() is None


def test_env_var_has_priority(memory_keyring, monkeypatch):
    secrets_store.save_token(TOKEN)
    monkeypatch.setenv(secrets_store.TOKEN_ENV_VAR, "999:env")
    assert secrets_store.load_token() == "999:env"


def test_keyring_failure_returns_none(monkeypatch):
    from keyring.errors import KeyringError

    class Broken:
        def get_password(self, *a):
            raise KeyringError("sin almacén")

    monkeypatch.setattr(secrets_store, "keyring", Broken())
    monkeypatch.delenv(secrets_store.TOKEN_ENV_VAR, raising=False)
    assert secrets_store.load_token() is None

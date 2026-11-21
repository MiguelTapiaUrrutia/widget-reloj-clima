"""Regresiones de los hallazgos de la revisión de código y de seguridad."""

import json
import threading
from datetime import datetime, timedelta

import pytest

from widget_reloj_clima import reminders as reminders_module
from widget_reloj_clima import telegram_bot
from widget_reloj_clima.reminder_service import ReminderService, TelegramState
from widget_reloj_clima.reminders import ReminderStore
from widget_reloj_clima.telegram_bot import TelegramBot, TelegramError

NOW = datetime(2026, 11, 21, 12, 0)
TOKEN = "123456789:AAH" + "x" * 32
OWNER = 555


def wait_until(condition, timeout_s=3.0):
    deadline = datetime.now() + timedelta(seconds=timeout_s)
    while datetime.now() < deadline:
        if condition():
            return True
        threading.Event().wait(0.02)
    return False


# ---------- almacén ----------
def test_ids_are_never_reused_even_after_restart(tmp_path):
    path = tmp_path / "r.json"
    store = ReminderStore(path, now=NOW)
    first = store.add("a", NOW + timedelta(hours=1), now=NOW)
    second = store.add("b", NOW + timedelta(hours=1), now=NOW)
    store.remove(second.id)

    assert store.add("c", NOW + timedelta(hours=1), now=NOW).id == 3
    reloaded = ReminderStore(path, now=NOW)
    reloaded.remove(3)
    assert reloaded.add("d", NOW + timedelta(hours=1), now=NOW).id == 4
    assert first.id == 1


def test_legacy_list_format_is_still_read(tmp_path):
    path = tmp_path / "r.json"
    legacy = [{"id": 7, "text": "viejo", "due": (NOW + timedelta(hours=1)).isoformat(),
               "created": NOW.isoformat()}]
    path.write_text(json.dumps(legacy), encoding="utf-8")
    store = ReminderStore(path, now=NOW)
    assert [r.text for r in store.pending()] == ["viejo"]
    assert store.add("nuevo", NOW + timedelta(hours=2), now=NOW).id == 8


def test_disk_failure_keeps_changes_and_retries(tmp_path, monkeypatch):
    store = ReminderStore(tmp_path / "r.json", now=NOW)
    real_replace = reminders_module.os.replace

    def locked(*args):
        raise PermissionError("bloqueado por OneDrive")

    monkeypatch.setattr(reminders_module.os, "replace", locked)
    store.add("Vence", NOW - timedelta(minutes=1), now=NOW)  # no lanza excepción
    assert store.has_unsaved_changes
    assert [r.text for r in store.take_due(NOW)] == ["Vence"]  # los avisos siguen funcionando

    monkeypatch.setattr(reminders_module.os, "replace", real_replace)
    store.retry_save()
    assert not store.has_unsaved_changes
    assert ReminderStore(tmp_path / "r.json", now=NOW).pending() == ()


def test_take_due_flushes_pending_writes(tmp_path, monkeypatch):
    store = ReminderStore(tmp_path / "r.json", now=NOW)
    real_replace = reminders_module.os.replace
    monkeypatch.setattr(reminders_module.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("x")))
    store.add("Futuro", NOW + timedelta(hours=1), now=NOW)
    monkeypatch.setattr(reminders_module.os, "replace", real_replace)
    store.take_due(NOW)
    assert not store.has_unsaved_changes


# ---------- bot ----------
class FakeClient:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


def make_bot(tmp_path, now=lambda: NOW, **kw):
    client = FakeClient()
    store = ReminderStore(tmp_path / "b.json", now=NOW)
    linked = {"chat": kw.pop("chat", OWNER)}
    bot = TelegramBot(client, store, lambda: linked["chat"], lambda c: linked.update(chat=c), now=now, **kw)
    return bot, client, store, linked


def test_offset_is_saved_before_processing_and_reused(tmp_path):
    saved = []

    class Client(FakeClient):
        def __init__(self):
            super().__init__()
            self.offsets = []

        def get_updates(self, offset, timeout=0):
            self.offsets.append(offset)
            if len(self.offsets) > 1:
                bot.stop()
                return []
            return [{"update_id": 41, "message": {"chat": {"id": OWNER}, "text": "/lista"}}]

    client = Client()
    bot = TelegramBot(client, ReminderStore(tmp_path / "o.json", now=NOW), lambda: OWNER, lambda c: None,
                      now=lambda: NOW, initial_offset=40, save_offset=saved.append)
    bot._run()
    assert client.offsets == [40, 42]
    assert saved == [42]


def test_updates_received_after_stop_are_left_for_the_next_bot(tmp_path):
    handled = []

    class Client(FakeClient):
        def get_updates(self, offset, timeout=0):
            bot.stop()  # se detiene mientras la petición estaba en curso
            return [{"update_id": 1, "message": {"chat": {"id": OWNER}, "text": "hola"}}]

    bot = TelegramBot(Client(), ReminderStore(tmp_path / "s.json", now=NOW), lambda: OWNER, lambda c: None,
                      save_offset=handled.append)
    bot._run()
    assert handled == []


def test_message_time_is_used_for_relative_reminders(tmp_path):
    bot, client, store, _ = make_bot(tmp_path)
    sent = NOW - timedelta(minutes=30)  # escrito con el PC apagado
    bot.handle_update({"update_id": 1, "message": {"chat": {"id": OWNER}, "text": "en 10 minutos sacar el pan",
                                                   "date": int(sent.timestamp())}})
    assert store.pending()[0].due == sent + timedelta(minutes=10)


@pytest.mark.parametrize("date_value", [True, "ayer", 10 ** 20, int((NOW + timedelta(days=1)).timestamp())])
def test_bad_or_future_message_dates_fall_back_to_now(tmp_path, date_value):
    bot, client, store, _ = make_bot(tmp_path)
    bot.handle_update({"update_id": 1, "message": {"chat": {"id": OWNER}, "text": "en 10 minutos x",
                                                   "date": date_value}})
    assert store.pending()[0].due == NOW + timedelta(minutes=10)


def test_pairing_lock_expires_with_a_new_code(tmp_path):
    clock = {"now": NOW}
    bot, client, _, linked = make_bot(tmp_path, now=lambda: clock["now"], chat=None)
    wrong = "000000" if bot.pairing_code != "000000" else "111111"
    for _ in range(telegram_bot.MAX_PAIRING_ATTEMPTS):
        bot.handle_update({"message": {"chat": {"id": 1}, "text": f"/vincular {wrong}"}})
    old_code = bot.pairing_code
    assert bot.pairing_locked
    bot.handle_update({"message": {"chat": {"id": 1}, "text": f"/vincular {old_code}"}})
    assert linked["chat"] is None

    clock["now"] = NOW + timedelta(minutes=telegram_bot.PAIRING_LOCK_MINUTES + 1)
    assert not bot.pairing_locked
    bot.handle_update({"message": {"chat": {"id": OWNER}, "text": f"/vincular {bot.pairing_code}"}})
    assert linked["chat"] == OWNER


@pytest.mark.parametrize("text", ["/vincular ١٢٣٤٥٦", "/borrar ١٢", "/borrar 99999999999999999999"])
def test_unicode_or_huge_numbers_are_rejected_politely(tmp_path, text):
    bot, client, _, _ = make_bot(tmp_path, chat=None if text.startswith("/vincular") else OWNER)
    bot.handle_update({"message": {"chat": {"id": OWNER}, "text": text}})
    assert client.sent  # respondió algo, sin excepción


@pytest.mark.parametrize("text", ["en 5000000 dias x", "en " + "9" * 50 + " horas x", "İİİİ mañana a las 22 x"])
def test_hostile_reminders_get_an_answer(tmp_path, text):
    bot, client, _, _ = make_bot(tmp_path)
    bot.handle_update({"message": {"chat": {"id": OWNER}, "text": text}})
    assert client.sent


# ---------- servicio: conexión ----------
class ScriptedClient:
    def __init__(self, failures):
        self.failures = failures

    def get_me(self):
        if self.failures:
            raise self.failures.pop(0)
        return {"username": "mi_bot"}

    def get_updates(self, offset, timeout=0):
        threading.Event().wait(0.05)
        return []

    def send_message(self, chat_id, text):
        pass


def make_service(tmp_path, factory, **kw):
    return ReminderService(ReminderStore(tmp_path / "svc.json", now=NOW), kw.pop("chat_id", None),
                           on_linked=lambda c: None, client_factory=factory, token_loader=lambda: TOKEN,
                           state=TelegramState(tmp_path / "state.json"), token_deleter=lambda: None, **kw)


def test_connect_retries_network_errors(tmp_path, monkeypatch):
    from widget_reloj_clima import reminder_service

    monkeypatch.setattr(reminder_service, "CONNECT_BACKOFF_START_S", 0.01)
    failures = [TelegramError("Sin conexión"), TelegramError("Sin conexión")]
    svc = make_service(tmp_path, lambda t: ScriptedClient(failures))
    svc.start_telegram()
    assert wait_until(lambda: "vincular" in svc.telegram_status)
    svc.stop()


def test_connect_does_not_retry_rejected_token(tmp_path):
    calls = []

    def factory(token):
        calls.append(token)
        return ScriptedClient([TelegramError("Telegram rechazó el token", retryable=False)])

    svc = make_service(tmp_path, factory)
    svc.start_telegram()
    assert wait_until(lambda: "rechazó" in svc.telegram_status and "reintentando" not in svc.telegram_status)
    threading.Event().wait(0.1)
    assert len(calls) == 1
    svc.stop()


def test_unlink_during_connect_wins(tmp_path):
    release = threading.Event()

    class SlowClient(ScriptedClient):
        def get_me(self):
            release.wait(2)
            return {"username": "mi_bot"}

    svc = make_service(tmp_path, lambda t: SlowClient([]))
    svc.start_telegram()
    svc.unlink_telegram()  # mientras get_me sigue esperando
    release.set()
    threading.Event().wait(0.2)
    assert svc._bot is None
    assert "sin configurar" in svc.telegram_status
    svc.stop()


def test_failed_telegram_sends_are_retried(tmp_path):
    attempts = []

    class FlakyBot:
        def notify(self, reminder):
            attempts.append(reminder.id)
            return len(attempts) > 1  # el primer envío falla

    svc = make_service(tmp_path, lambda t: ScriptedClient([]), chat_id=OWNER)
    svc._bot = FlakyBot()
    svc.store.add("Imprimir", NOW - timedelta(minutes=1), now=NOW)

    svc.take_due(NOW)
    assert wait_until(lambda: len(svc._unsent) == 1)
    svc.take_due(NOW + timedelta(seconds=10))  # aún no toca reintentar
    assert len(attempts) == 1
    svc.take_due(NOW + timedelta(minutes=2))
    assert wait_until(lambda: len(attempts) == 2 and not svc._unsent)


def test_state_offset_roundtrip(tmp_path):
    state = TelegramState(tmp_path / "st.json")
    assert state.load_offset() is None
    state.save_offset(123)
    assert state.load_offset() == 123
    state.clear()
    assert state.load_offset() is None
    (tmp_path / "st.json").write_text('{"offset": "x"}', encoding="utf-8")
    assert state.load_offset() is None

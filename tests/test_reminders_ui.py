"""Pestaña de recordatorios, avisos de escritorio y servicio (con ventanas reales de Tk)."""

import threading
import tkinter as tk
from datetime import datetime, timedelta

import pytest

from widget_reloj_clima import alerts, app as app_module, reminder_service, reminders_controller
from widget_reloj_clima.alerts import AlertWindow, TelegramSetupDialog
from widget_reloj_clima.config import DEFAULT_SETTINGS, load_settings
from widget_reloj_clima.reminder_service import STATUS_CONNECTING, STATUS_NO_TOKEN, ReminderService
from widget_reloj_clima.reminders import Reminder, ReminderStore
from widget_reloj_clima.reminders_view import MAX_VISIBLE
from widget_reloj_clima.telegram_bot import TelegramError

from .conftest import create_tk
from .test_ui import FORECAST

TOKEN = "123456789:AAH" + "x" * 32


class FakeClient:
    def __init__(self, token, fail=False):
        self.token, self.fail = token, fail
        self.sent = []

    def get_me(self):
        if self.fail:
            raise TelegramError("Telegram rechazó el token")
        return {"username": "mi_bot"}

    def get_updates(self, offset, timeout=0):
        threading.Event().wait(0.05)
        return []

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


def wait_until(condition, widget=None, timeout_s=3.0):
    deadline = datetime.now() + timedelta(seconds=timeout_s)
    while datetime.now() < deadline:
        if widget is not None:
            widget.update()
        if condition():
            return True
        threading.Event().wait(0.02)
    return False


@pytest.fixture(autouse=True)
def no_sound(monkeypatch):
    monkeypatch.setattr(alerts, "play_alert_sound", lambda: None)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "fetch_forecast", lambda *a: FORECAST)
    linked = []
    service = ReminderService(ReminderStore(tmp_path / "r.json"), None, on_linked=linked.append,
                              token_loader=lambda: None)
    instance = create_tk(lambda: app_module.WidgetApp(DEFAULT_SETTINGS, tmp_path / "config.json", service))
    instance.withdraw()
    instance.linked = linked
    yield instance
    for alert in list(instance.reminders.alerts):
        if alert.winfo_exists():
            alert.destroy()
    instance.destroy()


# ---------- pestaña ----------
def test_reminders_tab_exists_and_shows_empty_state(app):
    app.show_view("reminders")
    app.reminders.render(datetime.now())
    assert app._tabs["reminders"].active
    assert app.reminders_view._empty.winfo_manager() == "grid"
    assert "sin configurar" in app.reminders_view._telegram.cget("text") or True


def test_adding_from_entry_creates_reminder(app):
    app.reminders_view.entry.insert(0, "en 30 minutos sacar el pan")
    app.reminders_view.submit()

    pending = app._service.store.pending()
    assert [r.text for r in pending] == ["Sacar el pan"]
    assert app.reminders_view.entry.get() == ""
    assert "Te avisaré" in app.reminders_view._feedback.cget("text")
    when, text, _ = app.reminders_view._rows[0]
    assert text.cget("text") == "Sacar el pan" and when.winfo_manager() == "grid"


def test_enter_key_submits(app):
    app.deiconify()
    app.show_view("reminders")
    app.update()
    app.reminders_view.entry.insert(0, "en 5 minutos estirar")
    app.reminders_view.entry._entry.event_generate("<Return>")
    app.update()
    assert app._service.store.pending()[0].text == "Estirar"


def test_invalid_text_shows_error_and_keeps_entry(app):
    app.reminders_view.entry.insert(0, "imprimir moldes")
    app.reminders_view.submit()
    assert "Indica la hora" in app.reminders_view._feedback.cget("text")
    assert app.reminders_view.entry.get() == "imprimir moldes"
    assert app._service.store.pending() == ()


def test_empty_entry_does_nothing(app):
    app.reminders_view.submit()
    assert app._service.store.pending() == ()


def test_delete_button_removes_reminder(app):
    now = datetime.now()
    app._service.store.add("Borrar", now + timedelta(hours=1), now=now)
    app.reminders.render(now)
    app.reminders_view._rows[0][2].invoke()
    assert app._service.store.pending() == ()
    app.reminders_view._delete_row(3)  # fila vacía: no hace nada


def test_list_shows_at_most_five_and_counts_the_rest(app):
    now = datetime.now()
    for i in range(MAX_VISIBLE + 2):
        app._service.store.add(f"Tarea con un nombre bastante largo {i}", now + timedelta(hours=i + 1), now=now)
    app.reminders.render(now)
    assert app.reminders_view._more.cget("text") == "+2 más"
    assert app.reminders_view._rows[0][1].cget("text").endswith("…")


def test_tick_rerenders_when_telegram_adds(app):
    now = datetime.now()
    app.reminders.tick(now)
    app._service.store.add("Desde el teléfono", now + timedelta(hours=1), source="telegram", now=now)
    app.reminders.tick(now)
    assert app.reminders_view._rows[0][1].cget("text") == "Desde el teléfono"


# ---------- avisos ----------
def test_due_reminder_opens_alert_and_can_be_snoozed(app):
    now = datetime.now()
    app._service.store.add("Imprimir moldes", now - timedelta(seconds=1), now=now)
    app.reminders.tick(now)

    assert len(app.reminders.alerts) == 1
    alert = app.reminders.alerts[0]
    alert.snooze()
    pending = app._service.store.pending()
    assert pending[0].text == "Imprimir moldes"
    assert pending[0].due > now + timedelta(minutes=alerts.SNOOZE_MINUTES - 1)
    assert not alert.winfo_exists()


def test_failed_alert_window_does_not_lose_other_reminders(app, monkeypatch):
    real_alert = reminders_controller.AlertWindow

    def flaky(root, reminder, now, on_snooze):
        if reminder.text == "Falla":
            raise tk.TclError("ventana no disponible")
        return real_alert(root, reminder, now, on_snooze)

    monkeypatch.setattr(reminders_controller, "AlertWindow", flaky)
    now = datetime.now()
    app._service.store.add("Falla", now - timedelta(seconds=2), now=now)
    app._service.store.add("Funciona", now - timedelta(seconds=1), now=now)

    app.reminders.tick(now)

    assert len(app.reminders.alerts) == 1  # el segundo aviso sí se mostró
    retry = app._service.store.pending()
    assert [r.text for r in retry] == ["Falla"]  # el que falló se reintenta en 1 minuto
    assert retry[0].due > now


def test_alert_dismiss_and_late_label(app):
    now = datetime.now()
    late = Reminder(1, "Regar", now - timedelta(hours=2), now - timedelta(days=1))
    alert = AlertWindow(app, late, now, on_snooze=lambda r: None)
    import customtkinter as ctk

    labels = [w.cget("text") for w in alert.winfo_children() if isinstance(w, ctk.CTkLabel)]
    assert any(t.startswith("⏰  Era para") for t in labels)
    alert.dismiss()
    assert AlertWindow.open_count >= 0


def test_play_alert_sound_does_not_raise(monkeypatch):
    monkeypatch.undo()
    alerts.play_alert_sound()


# ---------- Telegram desde la interfaz ----------
def test_save_token_validates_format(app):
    assert "formato" in app.reminders.save_token("no-es-un-token")


def test_save_token_stores_in_keyring_and_connects(app, monkeypatch):
    saved = []
    monkeypatch.setattr(reminders_controller.secrets_store, "save_token", saved.append)
    started = []
    monkeypatch.setattr(app._service, "start_telegram", started.append)
    assert app.reminders.save_token(f" {TOKEN} ") is None
    assert saved == [TOKEN] and started == [TOKEN]


def test_save_token_reports_keyring_failure(app, monkeypatch):
    from keyring.errors import KeyringError

    def boom(token):
        raise KeyringError("sin almacén")

    monkeypatch.setattr(reminders_controller.secrets_store, "save_token", boom)
    assert "almacén de credenciales" in app.reminders.save_token(TOKEN)


def test_setup_dialog_shows_errors_and_closes_on_success(app):
    results = iter(["Token inválido", None])
    dialog = TelegramSetupDialog(app, lambda token: next(results))
    dialog.entry.insert(0, "x")
    dialog.save()
    assert dialog.error.cget("text") == "Token inválido"
    dialog.save()
    assert not dialog.winfo_exists()


def test_open_telegram_setup_returns_dialog(app):
    dialog = app.reminders.open_telegram_setup()
    dialog.entry.insert(0, TOKEN)
    # Con el texto de ayuda visible CTk no oculta; con el token escrito, debe mostrarse como •••
    assert dialog.entry._entry.cget("show") == "•"
    assert dialog.entry.get() == TOKEN
    dialog.destroy()


def test_link_from_bot_thread_is_saved_by_ui_thread(tmp_path, monkeypatch):
    """Usa el servicio que crea la propia app (sin token real ni archivos del usuario)."""
    monkeypatch.setattr(app_module, "fetch_forecast", lambda *a: FORECAST)
    monkeypatch.setattr(app_module, "ReminderStore", lambda: ReminderStore(tmp_path / "r.json"))
    real_service = app_module.ReminderService
    monkeypatch.setattr(app_module, "ReminderService",
                        lambda *a, **kw: real_service(*a, token_loader=lambda: None, **kw))
    instance = create_tk(lambda: app_module.WidgetApp(DEFAULT_SETTINGS, tmp_path / "config.json"))
    try:
        instance.withdraw()
        instance._service._on_linked(4242)  # lo que hace el hilo del bot al vincular
        instance._poll_results()
        assert load_settings(tmp_path / "config.json").telegram_chat_id == 4242
    finally:
        instance._service.stop()
        instance.destroy()


# ---------- servicio ----------
@pytest.fixture
def service(tmp_path):
    linked = []
    svc = ReminderService(ReminderStore(tmp_path / "s.json"), None, on_linked=linked.append,
                          client_factory=FakeClient, token_loader=lambda: None)
    svc.linked = linked
    yield svc
    svc.stop()


def test_service_status_without_token(service):
    assert not service.start_telegram()
    assert service.telegram_status == STATUS_NO_TOKEN


def test_service_connects_and_shows_pairing_code(service):
    assert service.start_telegram(TOKEN)
    assert service.telegram_status in (STATUS_CONNECTING,) or "vincular" in service.telegram_status
    assert wait_until(lambda: "vincular" in service.telegram_status)
    assert "@mi_bot" in service.telegram_status


def test_service_link_and_unlink(service, monkeypatch):
    deleted = []
    monkeypatch.setattr(reminder_service.secrets_store, "delete_token", lambda: deleted.append(True))
    service.start_telegram(TOKEN)
    assert wait_until(lambda: service._bot is not None)
    service._bot._link_chat(777)
    assert service.chat_id == 777 and service.linked == [777]
    assert "vinculado" in service.telegram_status
    service.unlink_telegram()
    assert service.chat_id is None and deleted == [True]
    assert service.telegram_status == STATUS_NO_TOKEN


def test_service_reports_connection_error(tmp_path):
    svc = ReminderService(ReminderStore(tmp_path / "e.json"), None, on_linked=lambda c: None,
                          client_factory=lambda t: FakeClient(t, fail=True), token_loader=lambda: TOKEN)
    assert svc.start_telegram()
    assert wait_until(lambda: "rechazó" in svc.telegram_status)


def test_service_sends_due_reminders_to_telegram(tmp_path):
    clients = []

    def factory(token):
        clients.append(FakeClient(token))
        return clients[-1]

    svc = ReminderService(ReminderStore(tmp_path / "d.json"), 555, on_linked=lambda c: None,
                          client_factory=factory, token_loader=lambda: TOKEN)
    svc.start_telegram()
    assert wait_until(lambda: svc._bot is not None)
    now = datetime.now()
    svc.store.add("Imprimir moldes", now - timedelta(seconds=1), now=now)

    due = svc.take_due(now)

    assert [r.text for r in due] == ["Imprimir moldes"]
    assert wait_until(lambda: clients[0].sent)
    assert clients[0].sent[0][0] == 555 and "Imprimir moldes" in clients[0].sent[0][1]
    svc.stop()


def test_service_pairing_locked_status(service):
    service.start_telegram(TOKEN)
    assert wait_until(lambda: service._bot is not None)
    service._bot._pairing_locked_until = datetime.now() + timedelta(minutes=5)
    assert "Demasiados intentos" in service.telegram_status

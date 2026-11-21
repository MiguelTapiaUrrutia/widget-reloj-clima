"""Une la pestaña de recordatorios con el servicio: alta, borrado, avisos y Telegram."""

from __future__ import annotations

import logging
import tkinter as tk
from datetime import datetime

from keyring.errors import KeyringError

from . import secrets_store
from .alerts import SNOOZE_MINUTES, AlertWindow, TelegramSetupDialog
from .dates import format_due
from .reminder_parser import ReminderParseError
from .reminder_service import ReminderService
from .reminders import Reminder
from .reminders_view import RemindersView
from .telegram_bot import validate_token

ALERT_RETRY_MINUTES = 1

log = logging.getLogger(__name__)


class RemindersController:
    def __init__(self, root: tk.Misc, view: RemindersView, service: ReminderService) -> None:
        self._root = root
        self._view = view
        self.service = service
        self._rendered_version = -1
        self._rendered_minute: tuple[int, int] | None = None
        self._status = ""
        self.alerts: list[AlertWindow] = []

    # ---------- acciones de la interfaz ----------
    def add(self, text: str) -> None:
        now = datetime.now()
        try:
            reminder = self.service.add_from_text(text, now)
        except (ReminderParseError, ValueError) as exc:
            self._view.show_feedback(str(exc), "error")
            return
        self._view.clear_entry()
        self._view.show_feedback(f"✓ Te avisaré {format_due(reminder.due, now)}: {reminder.text}", "ok")
        self.render(now)

    def delete(self, reminder_id: int) -> None:
        self.service.store.remove(reminder_id)
        self.render(datetime.now())

    def _snooze(self, reminder: Reminder) -> None:
        self.service.store.snooze(reminder.id, SNOOZE_MINUTES, datetime.now())
        self.render(datetime.now())

    # ---------- ciclo de cada segundo ----------
    def tick(self, now: datetime) -> None:
        for reminder in self.service.take_due(now):
            log.info("Aviso de recordatorio #%s", reminder.id)
            self.alerts = [a for a in self.alerts if a.winfo_exists()]
            try:
                self.alerts.append(AlertWindow(self._root, reminder, now, self._snooze))
            except Exception:  # noqa: BLE001 - si una ventana falla, los demás avisos deben salir
                log.exception("No se pudo mostrar el aviso #%s; se reintenta en 1 minuto", reminder.id)
                self.service.store.snooze(reminder.id, ALERT_RETRY_MINUTES, now)
        minute = (now.hour, now.minute)
        if self.service.store.version != self._rendered_version or minute != self._rendered_minute:
            self.render(now)
        status = self.service.telegram_status
        if status != self._status:
            self._status = status
            self._view.set_telegram_status(status)

    def render(self, now: datetime) -> None:
        self._rendered_version = self.service.store.version
        self._rendered_minute = (now.hour, now.minute)
        self._view.set_reminders(self.service.store.pending(), now)

    # ---------- Telegram ----------
    def open_telegram_setup(self) -> TelegramSetupDialog:
        return TelegramSetupDialog(self._root, self.save_token)

    def save_token(self, raw_token: str) -> str | None:
        """Valida y guarda el token. Devuelve un mensaje de error o None si salió bien."""
        try:
            token = validate_token(raw_token)
        except ValueError as exc:
            return str(exc)
        try:
            secrets_store.save_token(token)
        except KeyringError:
            log.exception("No se pudo guardar el token")
            return "No se pudo guardar el token en el almacén de credenciales de Windows"
        self.service.start_telegram(token)
        return None

    def unlink_telegram(self) -> None:
        self.service.unlink_telegram()

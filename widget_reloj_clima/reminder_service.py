"""Coordina recordatorios, avisos y el bot de Telegram (sin depender de la interfaz)."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import secrets_store
from .config import app_dir
from .reminder_parser import parse_reminder
from .reminders import Reminder, ReminderStore
from .telegram_bot import HTTP_TIMEOUT_S, TelegramBot, TelegramClient, TelegramError

log = logging.getLogger(__name__)

STATUS_NO_TOKEN = "Telegram sin configurar · clic derecho → Configurar Telegram"
STATUS_CONNECTING = "Conectando con Telegram…"
STATE_FILE_NAME = "telegram_state.json"
CONNECT_BACKOFF_START_S = 5
CONNECT_BACKOFF_MAX_S = 300
RESEND_EVERY = timedelta(minutes=1)
MAX_UNSENT = 50


class TelegramState:
    """Guarda el último mensaje leído (update_id) para no procesarlo dos veces tras reiniciar."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or app_dir() / STATE_FILE_NAME
        self._lock = threading.Lock()

    def load_offset(self) -> int | None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        offset = raw.get("offset") if isinstance(raw, dict) else None
        return offset if isinstance(offset, int) and not isinstance(offset, bool) else None

    def save_offset(self, offset: int) -> None:
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"offset": offset}), encoding="utf-8")
                os.replace(tmp, self._path)
            except OSError:
                log.warning("No se pudo guardar el estado de Telegram")

    def clear(self) -> None:
        with self._lock:
            self._path.unlink(missing_ok=True)


class ReminderService:
    def __init__(
        self,
        store: ReminderStore,
        chat_id: int | None,
        on_linked: Callable[[int | None], None],
        client_factory: Callable[[str], TelegramClient] = TelegramClient,
        token_loader: Callable[[], str | None] | None = None,
        state: TelegramState | None = None,
        token_deleter: Callable[[], None] | None = None,
    ) -> None:
        self.store = store
        self._chat_id = chat_id
        self._on_linked = on_linked
        self._client_factory = client_factory
        # se resuelven al usarse, para que los tests puedan sustituir secrets_store
        self._token_loader = token_loader or (lambda: secrets_store.load_token())
        self._token_deleter = token_deleter or (lambda: secrets_store.delete_token())
        self._state = state or TelegramState(state_path_for(store))
        self._lock = threading.Lock()
        self._bot: TelegramBot | None = None
        self._bot_username = ""
        self._error = ""
        self._connecting = False
        self._generation = 0  # cambia con cada conexión o desvinculación; invalida conexiones viejas
        self._stopped = threading.Event()
        self._unsent: list[Reminder] = []
        self._last_send_attempt = datetime.min

    # ---------- estado ----------
    @property
    def chat_id(self) -> int | None:
        with self._lock:
            return self._chat_id

    @property
    def telegram_status(self) -> str:
        with self._lock:
            if self._connecting:
                return STATUS_CONNECTING if not self._error else f"Telegram: {self._error} · reintentando…"
            if self._error:
                return f"Telegram: {self._error}"
            if self._bot is None:
                return STATUS_NO_TOKEN
            name = f"@{self._bot_username}" if self._bot_username else "tu bot"
            if self._chat_id is not None:
                return f"Telegram vinculado ✓ ({name})"
            if self._bot.pairing_locked:
                return "Demasiados intentos de vinculación. Espera 15 minutos."
            return f"En Telegram, envía «/vincular {self._bot.pairing_code}» a {name}"

    # ---------- recordatorios ----------
    def add_from_text(self, text: str, now: datetime) -> Reminder:
        """Crea un recordatorio desde texto libre. Lanza ReminderParseError o ValueError."""
        parsed = parse_reminder(text, now)
        return self.store.add(parsed.text, parsed.due, source="widget", now=now)

    def take_due(self, now: datetime) -> tuple[Reminder, ...]:
        """Devuelve los recordatorios vencidos y los envía por Telegram en segundo plano.

        Los envíos que fallan (sin conexión) se reintentan cada minuto.
        """
        due = self.store.take_due(now)
        with self._lock:
            bot = self._bot
            retry_now = bool(self._unsent) and now - self._last_send_attempt >= RESEND_EVERY
            if bot is None or self._chat_id is None or not (due or retry_now):
                return due
            batch, self._unsent = self._unsent + list(due), []
            self._last_send_attempt = now
        threading.Thread(target=self._send, args=(bot, batch), daemon=True).start()
        return due

    def _send(self, bot: TelegramBot, batch: list[Reminder]) -> None:
        failed = [r for r in batch if not bot.notify(r)]
        if failed:
            with self._lock:
                self._unsent = (failed + self._unsent)[:MAX_UNSENT]

    # ---------- Telegram ----------
    def _get_chat_id(self) -> int | None:
        return self.chat_id

    def _link_chat(self, chat_id: int | None) -> None:
        with self._lock:
            self._chat_id = chat_id
        self._on_linked(chat_id)

    def start_telegram(self, token: str | None = None) -> bool:
        """Conecta el bot en segundo plano. Devuelve False si no hay token guardado."""
        token = token or self._token_loader()
        if not token:
            return False
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._connecting, self._error = True, ""
        threading.Thread(target=self._connect, args=(token, generation), daemon=True).start()
        return True

    def _is_current(self, generation: int) -> bool:
        with self._lock:
            return generation == self._generation and not self._stopped.is_set()

    def _connect(self, token: str, generation: int) -> None:
        backoff = CONNECT_BACKOFF_START_S
        while self._is_current(generation):
            try:
                client = self._client_factory(token)
                me = client.get_me()
                break
            except (TelegramError, ValueError) as exc:
                retryable = isinstance(exc, TelegramError) and exc.retryable
                with self._lock:
                    if generation != self._generation:
                        return
                    self._error, self._connecting = str(exc), retryable
                log.warning("No se pudo conectar el bot de Telegram: %s", exc)
                if not retryable:
                    return
                self._stopped.wait(backoff)  # p. ej. el PC arrancó antes que el wifi
                backoff = min(backoff * 2, CONNECT_BACKOFF_MAX_S)
        else:
            return
        self._install_bot(client, me, generation)

    def _install_bot(self, client: TelegramClient, me: dict, generation: int) -> None:
        bot = TelegramBot(client, self.store, self._get_chat_id, self._link_chat,
                          initial_offset=self._state.load_offset(), save_offset=self._state.save_offset)
        with self._lock:
            if generation != self._generation:
                return  # se desvinculó o se reconectó mientras tanto
            old, self._bot = self._bot, bot
            self._bot_username = str((me or {}).get("username", ""))
            self._connecting, self._error = False, ""
        if old is not None:
            old.stop(wait_s=HTTP_TIMEOUT_S)  # que el sondeo anterior termine antes de empezar el nuevo
        bot.start()
        log.info("Bot de Telegram conectado")

    def unlink_telegram(self) -> None:
        """Olvida el token y el chat vinculado, y detiene el bot."""
        with self._lock:
            self._generation += 1
            bot, self._bot, self._bot_username = self._bot, None, ""
            self._error, self._connecting, self._unsent = "", False, []
        if bot is not None:
            bot.stop()
        self._token_deleter()
        self._state.clear()
        self._link_chat(None)

    def stop(self) -> None:
        self._stopped.set()
        with self._lock:
            bot = self._bot
        if bot is not None:
            bot.stop()


def state_path_for(store: ReminderStore) -> Path:
    """El estado de Telegram vive junto al archivo de recordatorios."""
    return store.path.with_name(STATE_FILE_NAME)

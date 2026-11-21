"""Bot de Telegram privado: envía los avisos y permite crear recordatorios desde el teléfono.

Usa la Bot API por HTTPS (sin librerías externas). Solo responde al chat vinculado; para
vincularlo hay que enviarle «/vincular <código>» con el código que muestra el widget.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Callable

from .dates import format_due
from .reminder_parser import ReminderParseError, parse_reminder
from .reminders import Reminder, ReminderStore

API_BASE = "https://api.telegram.org"
TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,60}$")
POLL_TIMEOUT_S = 25
HTTP_TIMEOUT_S = POLL_TIMEOUT_S + 10
SEND_TIMEOUT_S = 15
MAX_BACKOFF_S = 60
MAX_MESSAGE_LENGTH = 4000
MAX_LIST_ITEMS = 30
MAX_PAIRING_ATTEMPTS = 5
PAIRING_LOCK_MINUTES = 15
PAIRING_CODE_DIGITS = 6
MAX_MESSAGE_AGE_S = 2 * 24 * 3600  # Telegram guarda los mensajes no leídos hasta 24 h

HELP_TEXT = (
    "Escríbeme un recordatorio y te avisaré aquí y en el escritorio. Ejemplos:\n"
    "• hoy a las 22 imprimir moldes\n"
    "• mañana 9:30 llamar al banco\n"
    "• en 20 minutos sacar el pan\n"
    "• el viernes a las 18 cumpleaños de Ana\n\n"
    "Comandos:\n"
    "/lista – recordatorios pendientes\n"
    "/borrar N – borra el recordatorio número N\n"
    "/ayuda – esta ayuda"
)

Opener = Callable[..., Any]
log = logging.getLogger(__name__)


class TelegramError(Exception):
    """Error al hablar con la API de Telegram (el mensaje nunca incluye el token).

    `retryable` es False cuando reintentar no sirve (token rechazado, petición inválida).
    """

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


def validate_token(token: str) -> str:
    cleaned = (token or "").strip()
    if not TOKEN_RE.match(cleaned):
        raise ValueError("El token no tiene el formato de BotFather (números:letras)")
    return cleaned


def new_pairing_code() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(PAIRING_CODE_DIGITS))


class TelegramClient:
    def __init__(self, token: str, opener: Opener = urllib.request.urlopen) -> None:
        self._token = validate_token(token)
        self._opener = opener

    def _call(self, method: str, params: dict[str, Any], timeout: float) -> Any:
        url = f"{API_BASE}/bot{self._token}/{method}"
        data = urllib.parse.urlencode(params).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="POST")
        try:
            with self._opener(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise TelegramError("Telegram rechazó el token (¿está bien copiado?)", retryable=False) from None
            raise TelegramError(f"Telegram respondió con error {exc.code}", retryable=exc.code >= 409) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # from None: la excepción original puede contener la URL, que incluye el token
            raise TelegramError(f"Sin conexión con Telegram ({type(exc).__name__})") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise TelegramError("Respuesta de Telegram no válida") from None
        if not isinstance(payload, dict) or not payload.get("ok"):
            description = payload.get("description", "") if isinstance(payload, dict) else ""
            raise TelegramError(f"Telegram devolvió un error: {str(description)[:120]}", retryable=False)
        return payload.get("result")

    def get_me(self) -> dict[str, Any]:
        return self._call("getMe", {}, SEND_TIMEOUT_S)

    def send_message(self, chat_id: int, text: str) -> None:
        self._call("sendMessage", {"chat_id": chat_id, "text": text[:MAX_MESSAGE_LENGTH]}, SEND_TIMEOUT_S)

    def get_updates(self, offset: int | None, timeout: int = POLL_TIMEOUT_S) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout, "allowed_updates": json.dumps(["message"])}
        if offset is not None:
            params["offset"] = offset
        result = self._call("getUpdates", params, HTTP_TIMEOUT_S)
        return result if isinstance(result, list) else []


def format_alert(reminder: Reminder, now: datetime) -> str:
    when = format_due(reminder.due, now)
    late = (now - reminder.due).total_seconds() > 120
    return f"⏰ {reminder.text}\n({'era para ' if late else ''}{when})"


class TelegramBot:
    """Lógica del bot. `handle_update` es síncrono (fácil de probar); `start` lanza el sondeo."""

    def __init__(
        self,
        client: TelegramClient,
        store: ReminderStore,
        get_chat_id: Callable[[], int | None],
        link_chat: Callable[[int], None],
        now: Callable[[], datetime] = datetime.now,
        initial_offset: int | None = None,
        save_offset: Callable[[int], None] = lambda offset: None,
    ) -> None:
        self._client = client
        self._store = store
        self._get_chat_id = get_chat_id
        self._link_chat = link_chat
        self._now = now
        self._offset = initial_offset
        self._save_offset = save_offset
        self.pairing_code = new_pairing_code()
        self._pairing_failures = 0
        self._pairing_locked_until: datetime | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def pairing_locked(self) -> bool:
        if self._pairing_locked_until is None:
            return False
        if self._now() >= self._pairing_locked_until:
            self._pairing_locked_until, self._pairing_failures = None, 0
            self.pairing_code = new_pairing_code()  # código nuevo tras el bloqueo
            return False
        return True

    # ---------- ciclo de sondeo ----------
    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="telegram-bot", daemon=True)
            self._thread.start()

    def stop(self, wait_s: float = 0) -> None:
        """Detiene el sondeo. Con `wait_s`, espera a que termine la petición en curso."""
        self._stop.set()
        if wait_s and self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=wait_s)

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                updates = self._client.get_updates(self._offset)
                backoff = 1.0
            except TelegramError as exc:
                log.warning("Sondeo de Telegram falló: %s (reintento en %.0f s)", exc, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_S)
                continue
            if self._stop.is_set():
                return  # detenido durante la espera: no se procesan (los recibirá el bot nuevo)
            for update in updates:
                self._offset = int(update.get("update_id", 0)) + 1
                # se guarda antes de procesar: un reinicio no vuelve a ejecutar el mismo mensaje
                self._save_offset(self._offset)
                try:
                    self.handle_update(update)
                except Exception:  # noqa: BLE001 - un mensaje raro no debe parar el bot
                    log.exception("Error al procesar un mensaje de Telegram")

    # ---------- avisos ----------
    def notify(self, reminder: Reminder) -> bool:
        chat_id = self._get_chat_id()
        if chat_id is None:
            return False
        try:
            self._client.send_message(chat_id, format_alert(reminder, self._now()))
            return True
        except TelegramError as exc:
            log.warning("No se pudo enviar el aviso por Telegram: %s", exc)
            return False

    # ---------- mensajes entrantes ----------
    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat_id = message.get("chat", {}).get("id")
        text = str(message.get("text") or "").strip()
        if not isinstance(chat_id, int) or not text:
            return
        linked = self._get_chat_id()
        if linked is None:
            self._handle_pairing(chat_id, text)
        elif chat_id == linked:
            self._reply(chat_id, self._answer(text, self._sent_at(message)))
        else:
            log.info("Mensaje ignorado de un chat no vinculado")

    def _sent_at(self, message: dict[str, Any]) -> datetime:
        """Hora en que se escribió el mensaje: «en 10 minutos» enviado con el PC apagado
        se cuenta desde que se escribió, no desde que el widget lo leyó."""
        now = self._now()
        timestamp = message.get("date")
        if isinstance(timestamp, int) and not isinstance(timestamp, bool):
            try:
                sent = datetime.fromtimestamp(timestamp)
            except (OverflowError, OSError, ValueError):
                return now
            if now - timedelta(seconds=MAX_MESSAGE_AGE_S) <= sent <= now:
                return sent
        return now

    def _handle_pairing(self, chat_id: int, text: str) -> None:
        match = re.fullmatch(r"/vincular(?:@\w+)?\s+([0-9]{1,12})", text, re.ASCII)
        if not match:
            self._reply(chat_id, "Este bot es privado. Para usarlo, envía /vincular y el código que "
                                 "aparece en la pestaña de recordatorios del widget.")
            return
        if self.pairing_locked:
            self._reply(chat_id, "Demasiados intentos. Espera 15 minutos y usa el código nuevo del widget.")
            return
        if secrets.compare_digest(match.group(1), self.pairing_code):
            self._link_chat(chat_id)
            self._reply(chat_id, "✅ Listo, este chat quedó vinculado al widget.\n\n" + HELP_TEXT)
        else:
            self._pairing_failures += 1
            if self._pairing_failures >= MAX_PAIRING_ATTEMPTS:
                self._pairing_locked_until = self._now() + timedelta(minutes=PAIRING_LOCK_MINUTES)
                log.warning("Vinculación de Telegram bloqueada %s min por intentos fallidos", PAIRING_LOCK_MINUTES)
            self._reply(chat_id, "Código incorrecto. Revisa el que muestra el widget.")

    def _answer(self, text: str, sent_at: datetime | None = None) -> str:
        command, _, args = text.partition(" ")
        command = command.split("@")[0].lower()
        if command in ("/start", "/ayuda", "/help"):
            return HELP_TEXT
        if command == "/lista":
            return self._list()
        if command == "/borrar":
            return self._delete(args.strip())
        if command == "/recordar":
            return self._add(args, sent_at)
        if command.startswith("/"):
            return "No conozco ese comando.\n\n" + HELP_TEXT
        return self._add(text, sent_at)

    def _add(self, text: str, sent_at: datetime | None = None) -> str:
        now = self._now()
        try:
            parsed = parse_reminder(text, sent_at or now)
            reminder = self._store.add(parsed.text, parsed.due, source="telegram", now=now)
        except (ReminderParseError, ValueError) as exc:
            return f"No pude crear el recordatorio: {exc}"
        return f"✅ Te avisaré {format_due(reminder.due, now)}: {reminder.text} (#{reminder.id})"

    def _list(self) -> str:
        pending = self._store.pending()
        if not pending:
            return "No tienes recordatorios pendientes."
        now = self._now()
        lines = [f"#{r.id} · {format_due(r.due, now)} · {r.text}" for r in pending[:MAX_LIST_ITEMS]]
        if len(pending) > MAX_LIST_ITEMS:
            lines.append(f"… y {len(pending) - MAX_LIST_ITEMS} más")
        return "Pendientes:\n" + "\n".join(lines)

    def _delete(self, arg: str) -> str:
        number = arg.lstrip("#")
        if not (number.isascii() and number.isdigit() and len(number) <= 9):
            return "Indica el número, por ejemplo: /borrar 3 (míralo con /lista)"
        removed = self._store.remove(int(arg.lstrip("#")))
        return f"🗑️ Borrado: {removed.text}" if removed else f"No existe el recordatorio #{arg.lstrip('#')}"

    def _reply(self, chat_id: int, text: str) -> None:
        try:
            self._client.send_message(chat_id, text)
        except TelegramError as exc:
            log.warning("No se pudo responder en Telegram: %s", exc)

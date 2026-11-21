"""Almacén de recordatorios en JSON, seguro para usar desde varios hilos."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import app_dir

REMINDERS_FILE_NAME = "reminders.json"
KEEP_NOTIFIED_DAYS = 7
MAX_REMINDERS = 500

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reminder:
    id: int
    text: str
    due: datetime
    created: datetime
    source: str = "widget"  # "widget" o "telegram"
    notified: bool = False

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        return {**data, "due": self.due.isoformat(), "created": self.created.isoformat()}

    @staticmethod
    def from_json(raw: dict[str, Any]) -> Reminder:
        return Reminder(
            id=int(raw["id"]),
            text=str(raw["text"])[:500],
            due=datetime.fromisoformat(raw["due"]),
            created=datetime.fromisoformat(raw["created"]),
            source=str(raw.get("source", "widget")),
            notified=bool(raw.get("notified", False)),
        )


def reminders_path() -> Path:
    return app_dir() / REMINDERS_FILE_NAME


class ReminderStore:
    """Lista de recordatorios persistida en disco. Todas las operaciones son atómicas."""

    def __init__(self, path: Path | None = None, now: datetime | None = None) -> None:
        self._path = path or reminders_path()
        self._lock = threading.Lock()
        self._version = 0
        self._dirty = False  # hay cambios en memoria que no se pudieron escribir en disco
        items, self._next_id = self._load(now or datetime.now())
        self._items: tuple[Reminder, ...] = items

    @property
    def path(self) -> Path:
        return self._path

    @property
    def version(self) -> int:
        """Aumenta con cada cambio; la interfaz lo usa para saber cuándo redibujar."""
        return self._version

    def _load(self, now: datetime) -> tuple[tuple[Reminder, ...], int]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return (), 1
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning("No se pudieron leer los recordatorios (%s); se empieza con una lista vacía", exc)
            return (), 1
        # Formato actual: {"next_id": n, "items": [...]}; el antiguo era solo la lista.
        entries = raw.get("items", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
        stored_next = raw.get("next_id", 1) if isinstance(raw, dict) else 1
        items = []
        for entry in entries if isinstance(entries, list) else []:
            try:
                items.append(Reminder.from_json(entry))
            except (KeyError, TypeError, ValueError):
                log.warning("Recordatorio con formato inválido descartado")
        next_id = max([r.id + 1 for r in items] + [stored_next if isinstance(stored_next, int) else 1, 1])
        cutoff = now - timedelta(days=KEEP_NOTIFIED_DAYS)
        return tuple(r for r in items if not (r.notified and r.due < cutoff)), next_id

    def _save(self, items: tuple[Reminder, ...]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        payload = {"next_id": self._next_id, "items": [r.to_json() for r in items]}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    def _commit(self, items: tuple[Reminder, ...]) -> None:
        """Aplica el cambio en memoria y lo intenta guardar. Si el disco falla (p. ej. un
        bloqueo de OneDrive o del antivirus), el cambio no se pierde: se reintenta después."""
        self._items = items
        self._version += 1
        self._flush()

    def _flush(self) -> None:
        try:
            self._save(self._items)
        except OSError as exc:
            if not self._dirty:
                log.warning("No se pudieron guardar los recordatorios (%s); se reintentará", exc)
            self._dirty = True
            return
        if self._dirty:
            log.info("Recordatorios guardados tras un fallo anterior")
        self._dirty = False

    @property
    def has_unsaved_changes(self) -> bool:
        return self._dirty

    def retry_save(self) -> None:
        with self._lock:
            if self._dirty:
                self._flush()

    def pending(self) -> tuple[Reminder, ...]:
        """Recordatorios sin avisar, ordenados por fecha."""
        with self._lock:
            return tuple(sorted((r for r in self._items if not r.notified), key=lambda r: r.due))

    def add(self, text: str, due: datetime, source: str = "widget", now: datetime | None = None) -> Reminder:
        with self._lock:
            if sum(1 for r in self._items if not r.notified) >= MAX_REMINDERS:
                raise ValueError(f"Hay demasiados recordatorios pendientes (máximo {MAX_REMINDERS})")
            # los números nunca se reutilizan: un aviso viejo o un «/borrar N» no tocan otro recordatorio
            reminder = Reminder(self._next_id, text, due, now or datetime.now(), source)
            self._next_id += 1
            self._commit(self._items + (reminder,))
            return reminder

    def remove(self, reminder_id: int) -> Reminder | None:
        with self._lock:
            found = next((r for r in self._items if r.id == reminder_id), None)
            if found is not None:
                self._commit(tuple(r for r in self._items if r.id != reminder_id))
            return found

    def take_due(self, now: datetime) -> tuple[Reminder, ...]:
        """Marca como avisados los recordatorios vencidos y los devuelve (cada uno una sola vez)."""
        with self._lock:
            if self._dirty:
                self._flush()
            due = tuple(r for r in self._items if not r.notified and r.due <= now)
            if due:
                ids = {r.id for r in due}
                self._commit(tuple(replace(r, notified=True) if r.id in ids else r for r in self._items))
            return tuple(sorted(due, key=lambda r: r.due))

    def snooze(self, reminder_id: int, minutes: int, now: datetime) -> Reminder | None:
        with self._lock:
            found = next((r for r in self._items if r.id == reminder_id), None)
            if found is None:
                return None
            updated = replace(found, due=now + timedelta(minutes=minutes), notified=False)
            self._commit(tuple(updated if r.id == reminder_id else r for r in self._items))
            return updated

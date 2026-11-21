"""Ajustes del widget, guardados como JSON en la carpeta de datos del usuario."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

APP_DIR_NAME = "widget-reloj-clima"
CONFIG_FILE_NAME = "config.json"
MIN_REFRESH_MINUTES = 5
MAX_REFRESH_MINUTES = 180

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    city: str = "Concepción, Biobío, Chile"
    latitude: float = -36.8270
    longitude: float = -73.0503
    timezone: str = "America/Santiago"
    x: int | None = None
    y: int | None = None
    refresh_minutes: int = 20
    always_on_top: bool = True
    telegram_chat_id: int | None = None


DEFAULT_SETTINGS = Settings()


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_DIR_NAME


def config_path() -> Path:
    return app_dir() / CONFIG_FILE_NAME


def _validated(raw: dict[str, Any]) -> Settings:
    """Construye Settings a partir de datos externos, descartando valores inválidos."""
    defaults = asdict(DEFAULT_SETTINGS)
    known = {f.name for f in fields(Settings)}
    values = {k: v for k, v in raw.items() if k in known}
    merged = {**defaults, **values}

    city = merged["city"] if isinstance(merged["city"], str) and merged["city"].strip() else defaults["city"]
    try:
        lat = float(merged["latitude"])
        lon = float(merged["longitude"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError
    except (TypeError, ValueError):
        city, lat, lon = defaults["city"], defaults["latitude"], defaults["longitude"]

    tz = merged["timezone"] if isinstance(merged["timezone"], str) and merged["timezone"] else "auto"
    x = merged["x"] if isinstance(merged["x"], int) and not isinstance(merged["x"], bool) else None
    y = merged["y"] if isinstance(merged["y"], int) and not isinstance(merged["y"], bool) else None

    refresh = merged["refresh_minutes"]
    if not isinstance(refresh, int) or isinstance(refresh, bool):
        refresh = defaults["refresh_minutes"]
    refresh = max(MIN_REFRESH_MINUTES, min(MAX_REFRESH_MINUTES, refresh))

    on_top = merged["always_on_top"] if isinstance(merged["always_on_top"], bool) else True
    chat = merged["telegram_chat_id"]
    chat = chat if isinstance(chat, int) and not isinstance(chat, bool) else None

    return Settings(city, lat, lon, tz, x, y, refresh, on_top, chat)


def load_settings(path: Path | None = None) -> Settings:
    target = path or config_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DEFAULT_SETTINGS
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("No se pudo leer la configuración (%s); se usan valores por defecto", exc)
        return DEFAULT_SETTINGS
    if not isinstance(raw, dict):
        return DEFAULT_SETTINGS
    return _validated(raw)


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """Guarda de forma atómica (archivo temporal + reemplazo)."""
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)

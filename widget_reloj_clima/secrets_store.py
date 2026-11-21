"""Guarda el token del bot de Telegram en el Administrador de credenciales de Windows.

Nunca se escribe en archivos del proyecto ni en config.json. La variable de entorno
TELEGRAM_BOT_TOKEN tiene prioridad (útil para ejecutarlo más adelante en la nube).
"""

from __future__ import annotations

import logging
import os

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

KEYRING_SERVICE = "widget-reloj-clima"
KEYRING_USER = "telegram-bot-token"
TOKEN_ENV_VAR = "TELEGRAM_BOT_TOKEN"

log = logging.getLogger(__name__)


def load_token() -> str | None:
    env_token = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if env_token:
        return env_token
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    except KeyringError:
        log.exception("No se pudo leer el token del almacén de credenciales")
        return None


def save_token(token: str) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, token)


def delete_token() -> None:
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
    except PasswordDeleteError:
        pass  # no había token guardado

"""Configuración común de los tests."""

import time
import tkinter as tk

import pytest

TK_ATTEMPTS = 4
TK_RETRY_DELAY_S = 0.3


def create_tk(factory):
    """Crea una ventana de Tk reintentando.

    En este equipo Windows, a veces Tcl no consigue leer su init.tcl a la primera
    (lectura bloqueada un instante por el sistema). Se reintenta antes de saltar el test.
    """
    last_error = None
    for _ in range(TK_ATTEMPTS):
        try:
            return factory()
        except tk.TclError as exc:
            last_error = exc
            time.sleep(TK_RETRY_DELAY_S)
    pytest.skip(f"Tk no disponible: {last_error}")

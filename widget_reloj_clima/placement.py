"""Cálculo de posiciones en pantalla: escritorio virtual, área de trabajo y esquinas."""

from __future__ import annotations

import sys
from typing import NamedTuple

SPI_GETWORKAREA = 0x0030
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

CORNERS: dict[str, str] = {
    "top_left": "Arriba a la izquierda",
    "top_right": "Arriba a la derecha",
    "bottom_left": "Abajo a la izquierda",
    "bottom_right": "Abajo a la derecha",
}


class Rect(NamedTuple):
    x: int
    y: int
    width: int
    height: int


def virtual_screen(fallback: Rect) -> Rect:
    """Escritorio completo (todos los monitores) en Windows; `fallback` en otros sistemas."""
    if sys.platform != "win32":
        return fallback
    try:
        import ctypes

        metrics = ctypes.windll.user32.GetSystemMetrics
        rect = Rect(metrics(SM_XVIRTUALSCREEN), metrics(SM_YVIRTUALSCREEN),
                    metrics(SM_CXVIRTUALSCREEN), metrics(SM_CYVIRTUALSCREEN))
    except (AttributeError, OSError):
        return fallback
    return rect if rect.width > 0 and rect.height > 0 else fallback


def work_area(fallback: Rect) -> Rect:
    """Área útil del monitor principal (sin la barra de tareas) en Windows."""
    if sys.platform != "win32":
        return fallback
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        if not ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            return fallback
    except (AttributeError, OSError):
        return fallback
    area = Rect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    return area if area.width > 0 and area.height > 0 else fallback


def corner_position(corner: str, area: Rect, width: int, height: int, margin: int) -> tuple[int, int]:
    """Coordenadas (x, y) para colocar una ventana de `width`x`height` en una esquina de `area`."""
    if corner not in CORNERS:
        raise ValueError(f"Esquina desconocida: {corner}")
    left = area.x + margin
    top = area.y + margin
    right = area.x + area.width - width - margin
    bottom = area.y + area.height - height - margin
    x = left if corner.endswith("left") else right
    y = top if corner.startswith("top") else bottom
    return max(area.x, x), max(area.y, y)


def is_visible(x: int, y: int, screen: Rect, min_visible: int) -> bool:
    """True si al menos `min_visible` px de la esquina superior izquierda caen dentro de `screen`."""
    return (
        screen.x <= x <= screen.x + screen.width - min_visible
        and screen.y <= y <= screen.y + screen.height - min_visible
    )

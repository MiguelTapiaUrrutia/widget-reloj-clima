"""Iconos del clima dibujados con formas simples sobre un Canvas de Tk."""

from __future__ import annotations

import math
import tkinter as tk

SUN = "#f6b93b"
MOON = "#dfe4f2"
CLOUD = "#cfd8e6"
CLOUD_DARK = "#8592ab"
RAIN = "#5fa8ff"
SNOW = "#ffffff"
BOLT = "#ffd43b"
FOG = "#9aa6ba"
RAY_COUNT = 8


def _circle(canvas: tk.Canvas, cx: float, cy: float, r: float, color: str) -> None:
    canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="")


def _sun(canvas: tk.Canvas, cx: float, cy: float, r: float, rays: bool) -> None:
    _circle(canvas, cx, cy, r, SUN)
    if not rays:
        return
    width = max(2, r / 5)
    for i in range(RAY_COUNT):
        angle = i * 2 * math.pi / RAY_COUNT
        x1, y1 = cx + math.cos(angle) * r * 1.4, cy + math.sin(angle) * r * 1.4
        x2, y2 = cx + math.cos(angle) * r * 1.8, cy + math.sin(angle) * r * 1.8
        canvas.create_line(x1, y1, x2, y2, fill=SUN, width=width, capstyle=tk.ROUND)


def _moon(canvas: tk.Canvas, cx: float, cy: float, r: float, bg: str) -> None:
    _circle(canvas, cx, cy, r, MOON)
    _circle(canvas, cx + r * 0.45, cy - r * 0.35, r * 0.85, bg)


def _sky_body(canvas: tk.Canvas, cx: float, cy: float, r: float, is_day: bool, bg: str, rays: bool) -> None:
    if is_day:
        _sun(canvas, cx, cy, r, rays)
    else:
        _moon(canvas, cx, cy, r, bg)


def _cloud(canvas: tk.Canvas, cx: float, cy: float, w: float, color: str) -> None:
    """Nube centrada en (cx, cy) con semiancho w."""
    canvas.create_oval(cx - w, cy - w * 0.28, cx + w, cy + w * 0.42, fill=color, outline="")
    _circle(canvas, cx - w * 0.38, cy - w * 0.05, w * 0.38, color)
    _circle(canvas, cx + w * 0.18, cy - w * 0.22, w * 0.5, color)


def _drops(canvas: tk.Canvas, s: float, color: str, length: float) -> None:
    for i, x in enumerate((0.32, 0.5, 0.68)):
        y = s * (0.74 + (i % 2) * 0.06)
        canvas.create_line(
            s * x, y, s * x - s * 0.04, y + s * length,
            fill=color, width=max(2, s / 22), capstyle=tk.ROUND,
        )


def _flakes(canvas: tk.Canvas, s: float) -> None:
    for i, x in enumerate((0.32, 0.5, 0.68)):
        y = s * (0.8 + (i % 2) * 0.07)
        _circle(canvas, s * x, y, max(1.5, s / 22), SNOW)


def draw_tab_icon(canvas: tk.Canvas, kind: str, color: str, width: int, height: int) -> None:
    """Iconos de línea de la barra de vistas: 'clock', 'calendar' o 'weather'."""
    cx, cy = width / 2, height / 2
    r = min(width, height) * 0.3
    line = max(1.5, r / 5)
    if kind == "clock":
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=line)
        canvas.create_line(cx, cy, cx, cy - r * 0.6, fill=color, width=line, capstyle=tk.ROUND)
        canvas.create_line(cx, cy, cx + r * 0.45, cy + r * 0.2, fill=color, width=line, capstyle=tk.ROUND)
    elif kind == "calendar":
        x1, y1, x2, y2 = cx - r * 1.05, cy - r * 0.9, cx + r * 1.05, cy + r
        canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=line)
        canvas.create_line(x1, y1 + r * 0.55, x2, y1 + r * 0.55, fill=color, width=line)
        for dx in (-0.5, 0.0, 0.5):
            _circle(canvas, cx + dx * r * 1.1, cy + r * 0.45, max(1.2, r / 7), color)
        for dx in (-0.45, 0.45):
            canvas.create_line(cx + dx * r, y1 - r * 0.25, cx + dx * r, y1 + r * 0.15,
                               fill=color, width=line, capstyle=tk.ROUND)
    else:  # "weather": sol detrás de una nube, en línea
        sx, sy, sr = cx + r * 0.35, cy - r * 0.35, r * 0.5
        canvas.create_oval(sx - sr, sy - sr, sx + sr, sy + sr, outline=color, width=line)
        base = cy + r * 0.75
        canvas.create_arc(cx - r * 1.1, cy - r * 0.15, cx - r * 0.1, base + r * 0.05,
                          start=90, extent=180, style=tk.ARC, outline=color, width=line)
        canvas.create_arc(cx - r * 0.55, cy - r * 0.6, cx + r * 0.6, cy + r * 0.55,
                          start=0, extent=180, style=tk.ARC, outline=color, width=line)
        canvas.create_arc(cx + r * 0.15, cy - r * 0.05, cx + r * 1.15, base + r * 0.05,
                          start=270, extent=180, style=tk.ARC, outline=color, width=line)
        canvas.create_line(cx - r * 0.6, base, cx + r * 0.65, base, fill=color, width=line, capstyle=tk.ROUND)


def draw_icon(canvas: tk.Canvas, kind: str, is_day: bool, size: int, bg: str) -> None:
    """Dibuja el icono `kind` (ver weather.WEATHER_CODES) ocupando un cuadrado de `size`."""
    canvas.delete("all")
    s = float(size)
    if kind == "clear":
        _sky_body(canvas, s * 0.5, s * 0.5, s * 0.24, is_day, bg, rays=True)
    elif kind == "partly":
        _sky_body(canvas, s * 0.62, s * 0.36, s * 0.2, is_day, bg, rays=False)
        _cloud(canvas, s * 0.44, s * 0.62, s * 0.32, CLOUD)
    elif kind == "fog":
        _cloud(canvas, s * 0.5, s * 0.42, s * 0.32, FOG)
        for i, y in enumerate((0.7, 0.8, 0.9)):
            off = 0.06 * (i % 2)
            canvas.create_line(
                s * (0.2 + off), s * y, s * (0.8 + off), s * y,
                fill=FOG, width=max(2, s / 22), capstyle=tk.ROUND,
            )
    elif kind in ("drizzle", "rain"):
        _cloud(canvas, s * 0.5, s * 0.46, s * 0.34, CLOUD_DARK if kind == "rain" else CLOUD)
        _drops(canvas, s, RAIN, 0.1 if kind == "drizzle" else 0.16)
    elif kind == "snow":
        _cloud(canvas, s * 0.5, s * 0.46, s * 0.34, CLOUD)
        _flakes(canvas, s)
    elif kind == "storm":
        _cloud(canvas, s * 0.5, s * 0.42, s * 0.34, CLOUD_DARK)
        canvas.create_polygon(
            s * 0.52, s * 0.58, s * 0.4, s * 0.8, s * 0.5, s * 0.8,
            s * 0.44, s * 0.98, s * 0.62, s * 0.72, s * 0.52, s * 0.72,
            fill=BOLT, outline="",
        )
    else:  # "cloudy" y cualquier tipo desconocido
        _cloud(canvas, s * 0.62, s * 0.4, s * 0.24, CLOUD_DARK)
        _cloud(canvas, s * 0.45, s * 0.58, s * 0.34, CLOUD)

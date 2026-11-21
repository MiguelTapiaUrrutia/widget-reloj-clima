"""Utilidades de fecha en español, independientes del idioma del sistema."""

from __future__ import annotations

import calendar
from datetime import date, datetime

WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
WEEKDAY_INITIALS = ("L", "M", "M", "J", "V", "S", "D")
MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)

_CALENDAR = calendar.Calendar(firstweekday=calendar.MONDAY)


def format_time(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def format_seconds(moment: datetime) -> str:
    return moment.strftime("%S")


def format_long_date(day: date) -> str:
    """Ej.: 'sábado, 21 de noviembre de 2026'."""
    return f"{WEEKDAYS[day.weekday()]}, {day.day} de {MONTHS[day.month - 1]} de {day.year}"


def format_month_title(year: int, month: int) -> str:
    return f"{MONTHS[month - 1].capitalize()} {year}"


def short_weekday(day: date, today: date) -> str:
    """'Hoy', 'Mañana' o abreviatura de 3 letras del día."""
    delta = (day - today).days
    if delta == 0:
        return "Hoy"
    if delta == 1:
        return "Mañana"
    return WEEKDAYS[day.weekday()][:3].capitalize()


def month_grid(year: int, month: int) -> tuple[tuple[int, ...], ...]:
    """Semanas del mes empezando en lunes; 0 indica una celda vacía."""
    return tuple(tuple(week) for week in _CALENDAR.monthdayscalendar(year, month))


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1

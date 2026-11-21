"""Secciones visuales del widget: reloj, calendario y clima."""

from __future__ import annotations

import tkinter as tk
from datetime import date, datetime

import customtkinter as ctk

from . import dates, theme
from .icons import draw_icon
from .weather import Forecast, describe

GRID_ROWS = 6
GRID_COLS = 7
WEEKEND_COLS = (5, 6)
MAIN_ICON_SIZE = 64
SMALL_ICON_SIZE = 30


def _font(size: int, weight: str = "normal", family: str = theme.FONT_UI) -> ctk.CTkFont:
    return ctk.CTkFont(family=family, size=size, weight=weight)


class ClockView(ctk.CTkFrame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, fg_color="transparent")
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(anchor="w")
        self._time = ctk.CTkLabel(row, text="--:--", text_color=theme.TEXT,
                                  font=_font(60, family=theme.FONT_CLOCK))
        self._time.pack(side="left")
        self._seconds = ctk.CTkLabel(row, text="--", text_color=theme.ACCENT, font=_font(20, "bold"))
        self._seconds.pack(side="left", anchor="s", padx=(6, 0), pady=(0, 14))
        self._date = ctk.CTkLabel(self, text="", text_color=theme.MUTED, font=_font(14))
        self._date.pack(anchor="w", pady=(0, 2))

    def show(self, now: datetime) -> None:
        self._time.configure(text=dates.format_time(now))
        self._seconds.configure(text=dates.format_seconds(now))
        self._date.configure(text=dates.format_long_date(now.date()).capitalize())


class CalendarView(ctk.CTkFrame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, fg_color=theme.CARD, corner_radius=14)
        self._today = date.today()
        self._year, self._month = self._today.year, self._today.month
        self._build_header()
        self._build_grid()
        self._render()

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(8, 2))
        nav = {"width": 28, "height": 24, "fg_color": "transparent", "hover_color": theme.ACCENT_HOVER,
               "text_color": theme.MUTED, "font": _font(16, "bold")}
        ctk.CTkButton(header, text="‹", command=lambda: self._shift(-1), **nav).pack(side="left")
        ctk.CTkButton(header, text="›", command=lambda: self._shift(1), **nav).pack(side="right")
        self._title = ctk.CTkButton(header, text="", command=self.go_to_today, fg_color="transparent",
                                    hover_color=theme.ACCENT_HOVER, text_color=theme.TEXT,
                                    font=_font(14, "bold"), height=24)
        self._title.pack(side="top")

    def _build_grid(self) -> None:
        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.pack(padx=10, pady=(0, 10))
        for col, initial in enumerate(dates.WEEKDAY_INITIALS):
            color = theme.DIM if col in WEEKEND_COLS else theme.MUTED
            ctk.CTkLabel(grid, text=initial, width=36, height=20, text_color=color,
                         font=_font(11, "bold")).grid(row=0, column=col)
        self._cells = [
            [ctk.CTkLabel(grid, text="", width=34, height=24, corner_radius=12, font=_font(12))
             for _ in range(GRID_COLS)]
            for _ in range(GRID_ROWS)
        ]
        for r, row in enumerate(self._cells, start=1):
            for c, cell in enumerate(row):
                cell.grid(row=r, column=c, padx=1, pady=1)

    def _shift(self, delta: int) -> None:
        self._year, self._month = dates.shift_month(self._year, self._month, delta)
        self._render()

    def go_to_today(self) -> None:
        self._year, self._month = self._today.year, self._today.month
        self._render()

    def set_today(self, today: date) -> None:
        self._today = today
        self.go_to_today()

    def _render(self) -> None:
        self._title.configure(text=dates.format_month_title(self._year, self._month))
        weeks = dates.month_grid(self._year, self._month)
        is_current = (self._year, self._month) == (self._today.year, self._today.month)
        for r, row in enumerate(self._cells):
            week = weeks[r] if r < len(weeks) else (0,) * GRID_COLS
            for c, cell in enumerate(row):
                day = week[c]
                if is_current and day == self._today.day:
                    cell.configure(text=str(day), fg_color=theme.ACCENT, text_color=theme.BG,
                                   font=_font(12, "bold"))
                else:
                    color = theme.DIM if c in WEEKEND_COLS else theme.TEXT
                    cell.configure(text=str(day) if day else "", fg_color="transparent",
                                   text_color=color, font=_font(12))


class WeatherView(ctk.CTkFrame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, fg_color=theme.CARD, corner_radius=14)
        self._build_current()
        self._build_forecast()
        self._status = ctk.CTkLabel(self, text="", text_color=theme.MUTED, font=_font(11),
                                    wraplength=theme.WIDTH - 60, justify="left")
        self._status.pack(anchor="w", padx=14, pady=(0, 10))

    def _build_current(self) -> None:
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(12, 4))
        self._icon = tk.Canvas(top, width=MAIN_ICON_SIZE, height=MAIN_ICON_SIZE, bg=theme.CARD,
                               highlightthickness=0)
        self._icon.pack(side="left")
        info = ctk.CTkFrame(top, fg_color="transparent")
        info.pack(side="left", padx=(10, 0), fill="x", expand=True)
        self._temp = ctk.CTkLabel(info, text="--°", text_color=theme.TEXT, font=_font(34, "bold"))
        self._temp.pack(anchor="w")
        self._desc = ctk.CTkLabel(info, text="Cargando clima…", text_color=theme.MUTED, font=_font(13))
        self._desc.pack(anchor="w")
        self._details = ctk.CTkLabel(self, text="", text_color=theme.MUTED, font=_font(12))
        self._details.pack(anchor="w", padx=14)

    def _build_forecast(self) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(8, 6))
        self._days = []
        for col in range(5):
            row.grid_columnconfigure(col, weight=1)
            name = ctk.CTkLabel(row, text="", text_color=theme.MUTED, font=_font(11, "bold"), height=16)
            icon = tk.Canvas(row, width=SMALL_ICON_SIZE, height=SMALL_ICON_SIZE, bg=theme.CARD,
                             highlightthickness=0)
            temps = ctk.CTkLabel(row, text="", text_color=theme.TEXT, font=_font(11), height=16)
            name.grid(row=0, column=col)
            icon.grid(row=1, column=col, pady=2)
            temps.grid(row=2, column=col)
            self._days.append((name, icon, temps))

    def show_loading(self, city: str) -> None:
        self._status.configure(text=f"{city} · actualizando…", text_color=theme.MUTED)

    def show_forecast(self, forecast: Forecast, city: str, updated: datetime) -> None:
        cur = forecast.current
        text, kind = describe(cur.code)
        draw_icon(self._icon, kind, cur.is_day, MAIN_ICON_SIZE, theme.CARD)
        self._temp.configure(text=f"{cur.temperature:.0f}°")
        self._desc.configure(text=text)
        self._details.configure(
            text=f"Sensación {cur.apparent_temperature:.0f}°  ·  Humedad {cur.humidity}%"
                 f"  ·  Viento {cur.wind_speed:.0f} km/h"
        )
        today = updated.date()
        for (name, icon, temps), day in zip(self._days, forecast.daily):
            name.configure(text=dates.short_weekday(day.day, today))
            draw_icon(icon, describe(day.code)[1], True, SMALL_ICON_SIZE, theme.CARD)
            temps.configure(text=f"{day.temp_max:.0f}° {day.temp_min:.0f}°")
        self._status.configure(text=f"{city} · actualizado {updated:%H:%M}", text_color=theme.MUTED)

    def clear(self) -> None:
        """Borra los datos mostrados (p. ej. al cambiar de ciudad)."""
        self._icon.delete("all")
        self._temp.configure(text="--°")
        self._desc.configure(text="Cargando clima…")
        self._details.configure(text="")
        for name, icon, temps in self._days:
            name.configure(text="")
            icon.delete("all")
            temps.configure(text="")

    def show_error(self, message: str, city: str, retrying: bool = True) -> None:
        suffix = ". Reintentando pronto…" if retrying else ""
        self._status.configure(text=f"{city} · {message}{suffix}", text_color=theme.ERROR)

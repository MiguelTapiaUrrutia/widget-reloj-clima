"""Pestaña de recordatorios: campo para escribirlos, lista de pendientes y estado de Telegram."""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from typing import Callable

import customtkinter as ctk

from . import theme
from .dates import format_due
from .reminders import Reminder

MAX_VISIBLE = 5
MAX_TEXT_CHARS = 24
WHEN_WIDTH = 92
PLACEHOLDER = "hoy a las 22 imprimir moldes"
OK_COLOR = "#7fd6a0"


def _font(size: int, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=theme.FONT_UI, size=size, weight=weight)


def _shorten(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class RemindersView(ctk.CTkFrame):
    def __init__(self, master: tk.Misc, on_add: Callable[[str], None], on_delete: Callable[[int], None]) -> None:
        super().__init__(master, fg_color=theme.CARD, corner_radius=14)
        self._on_add = on_add
        self._on_delete = on_delete
        self._row_ids: list[int | None] = [None] * MAX_VISIBLE
        self._build_input()
        self._build_list()
        self._telegram = ctk.CTkLabel(self, text="", text_color=theme.DIM, font=_font(10),
                                      wraplength=theme.CONTENT_WIDTH - 28, justify="left")
        self._telegram.pack(anchor="w", padx=14, pady=(4, 10))

    def _build_input(self) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(12, 2))
        self.entry = ctk.CTkEntry(row, placeholder_text=PLACEHOLDER, height=30, font=_font(12),
                                  fg_color=theme.BG, border_color=theme.BORDER, text_color=theme.TEXT)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda _e: self.submit())
        self.add_button = ctk.CTkButton(row, text="+", width=30, height=30, fg_color=theme.ACCENT,
                                        hover_color="#d99a33", text_color=theme.BG, font=_font(16, "bold"),
                                        command=self.submit)
        self.add_button.pack(side="left", padx=(6, 0))
        self._feedback = ctk.CTkLabel(self, text="Escribe cuándo y qué. Ej.: «mañana 9:30 llamar al banco»",
                                      text_color=theme.MUTED, font=_font(11),
                                      wraplength=theme.CONTENT_WIDTH - 28, justify="left")
        self._feedback.pack(anchor="w", padx=14, pady=(2, 6))

    def _build_list(self) -> None:
        self._list = ctk.CTkFrame(self, fg_color="transparent")
        self._list.pack(fill="x", padx=12)
        self._list.grid_columnconfigure(1, weight=1)
        self._empty = ctk.CTkLabel(self._list, text="Sin recordatorios pendientes", text_color=theme.DIM,
                                   font=_font(12))
        self._rows = []
        for i in range(MAX_VISIBLE):
            when = ctk.CTkLabel(self._list, text="", text_color=theme.ACCENT, font=_font(11, "bold"),
                                width=WHEN_WIDTH, anchor="w")
            text = ctk.CTkLabel(self._list, text="", text_color=theme.TEXT, font=_font(12), anchor="w")
            delete = ctk.CTkButton(self._list, text="✕", width=22, height=22, fg_color="transparent",
                                   hover_color=theme.ACCENT_HOVER, text_color=theme.DIM, font=_font(11),
                                   command=lambda i=i: self._delete_row(i))
            self._rows.append((when, text, delete))
        self._more = ctk.CTkLabel(self._list, text="", text_color=theme.DIM, font=_font(11))

    def submit(self) -> None:
        text = self.entry.get().strip()
        if text:
            self._on_add(text)

    def clear_entry(self) -> None:
        self.entry.delete(0, "end")

    def _delete_row(self, index: int) -> None:
        reminder_id = self._row_ids[index]
        if reminder_id is not None:
            self._on_delete(reminder_id)

    def show_feedback(self, text: str, kind: str = "info") -> None:
        color = {"error": theme.ERROR, "ok": OK_COLOR}.get(kind, theme.MUTED)
        self._feedback.configure(text=text, text_color=color)

    def set_telegram_status(self, text: str) -> None:
        self._telegram.configure(text=text)

    def set_reminders(self, reminders: tuple[Reminder, ...], now: datetime) -> None:
        """Muestra los próximos recordatorios. Las filas se reutilizan (se ocultan con grid_forget)."""
        visible = reminders[:MAX_VISIBLE]
        if visible:
            self._empty.grid_forget()
        else:
            self._empty.grid(row=0, column=0, columnspan=3, sticky="w", pady=4)
        for i, (when, text, delete) in enumerate(self._rows):
            if i < len(visible):
                reminder = visible[i]
                self._row_ids[i] = reminder.id
                when.configure(text=format_due(reminder.due, now))
                text.configure(text=_shorten(reminder.text))
                when.grid(row=i, column=0, sticky="w")
                text.grid(row=i, column=1, sticky="w")
                delete.grid(row=i, column=2, sticky="e")
            else:
                self._row_ids[i] = None
                for widget in (when, text, delete):
                    widget.grid_forget()
        extra = len(reminders) - len(visible)
        if extra > 0:
            self._more.configure(text=f"+{extra} más")
            self._more.grid(row=MAX_VISIBLE, column=0, columnspan=3, sticky="w")
        else:
            self._more.grid_forget()

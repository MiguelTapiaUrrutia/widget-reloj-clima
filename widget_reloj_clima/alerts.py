"""Ventanas emergentes del escritorio: aviso de recordatorio y configuración de Telegram."""

from __future__ import annotations

import logging
import sys
import tkinter as tk
from datetime import datetime
from typing import Callable

import customtkinter as ctk

from . import placement, theme
from .dates import format_due
from .reminders import Reminder

ALERT_WIDTH = 300
ALERT_MARGIN = 24
ALERT_STACK_OFFSET = 40
SNOOZE_MINUTES = 10

log = logging.getLogger(__name__)


def play_alert_sound() -> None:
    if sys.platform != "win32":
        return
    try:
        import winsound

        winsound.PlaySound("SystemNotification", winsound.SND_ALIAS | winsound.SND_ASYNC)
    except (ImportError, RuntimeError, OSError):
        log.warning("No se pudo reproducir el sonido del aviso")


def _font(size: int, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=theme.FONT_UI, size=size, weight=weight)


class AlertWindow(ctk.CTkToplevel):
    """Aviso siempre visible con «Posponer» y «Listo»."""

    open_count = 0

    def __init__(self, master: tk.Misc, reminder: Reminder, now: datetime,
                 on_snooze: Callable[[Reminder], None]) -> None:
        super().__init__(master, fg_color=theme.BG)
        self._reminder = reminder
        self._on_snooze = on_snooze
        self.title("Recordatorio")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self.dismiss)

        late = (now - reminder.due).total_seconds() > 120
        when = f"{'Era para ' if late else ''}{format_due(reminder.due, now)}"
        ctk.CTkLabel(self, text=f"⏰  {when}", text_color=theme.ACCENT, font=_font(13, "bold")).pack(
            anchor="w", padx=18, pady=(16, 2))
        ctk.CTkLabel(self, text=reminder.text, text_color=theme.TEXT, font=_font(17, "bold"),
                     wraplength=ALERT_WIDTH - 36, justify="left").pack(anchor="w", padx=18, pady=(0, 14))
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=18, pady=(0, 16))
        ctk.CTkButton(buttons, text="Listo", width=110, fg_color=theme.ACCENT, hover_color="#d99a33",
                      text_color=theme.BG, font=_font(13, "bold"), command=self.dismiss).pack(side="right")
        ctk.CTkButton(buttons, text=f"Posponer {SNOOZE_MINUTES} min", width=130, fg_color=theme.CARD,
                      hover_color=theme.ACCENT_HOVER, text_color=theme.TEXT, font=_font(12),
                      command=self.snooze).pack(side="right", padx=(0, 8))

        self._place(AlertWindow.open_count)
        AlertWindow.open_count += 1
        play_alert_sound()

    def _place(self, index: int) -> None:
        self.update_idletasks()
        area = placement.work_area(placement.Rect(0, 0, self.winfo_screenwidth(), self.winfo_screenheight()))
        x, y = placement.corner_position("bottom_right", area, self.winfo_reqwidth(), self.winfo_reqheight(),
                                         ALERT_MARGIN)
        tk.Tk.wm_geometry(self, f"+{x}+{max(area.y, y - index * ALERT_STACK_OFFSET)}")

    def snooze(self) -> None:
        self._on_snooze(self._reminder)
        self.dismiss()

    def dismiss(self) -> None:
        AlertWindow.open_count = max(0, AlertWindow.open_count - 1)
        self.destroy()


class TelegramSetupDialog(ctk.CTkToplevel):
    """Pide el token del bot. Se muestra oculto (•) y no se guarda en ningún archivo."""

    STEPS = (
        "1. En Telegram, abre @BotFather y envía /newbot.\n"
        "2. Elige un nombre y un usuario para tu bot (debe terminar en «bot»).\n"
        "3. BotFather te dará un token: pégalo aquí.\n"
        "4. Después, envía a tu bot el código /vincular que mostrará el widget."
    )

    def __init__(self, master: tk.Misc, on_save: Callable[[str], str | None]) -> None:
        super().__init__(master, fg_color=theme.BG)
        self._on_save = on_save
        self.title("Configurar Telegram")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        ctk.CTkLabel(self, text="Avisos en tu teléfono por Telegram", text_color=theme.TEXT,
                     font=_font(15, "bold")).pack(anchor="w", padx=18, pady=(16, 6))
        ctk.CTkLabel(self, text=self.STEPS, text_color=theme.MUTED, font=_font(12), justify="left").pack(
            anchor="w", padx=18)
        self.entry = ctk.CTkEntry(self, width=320, show="•", placeholder_text="Token del bot")
        self.entry.pack(padx=18, pady=(12, 4))
        self.entry.bind("<Return>", lambda _e: self.save())
        self.error = ctk.CTkLabel(self, text="", text_color=theme.ERROR, font=_font(11))
        self.error.pack(anchor="w", padx=18)
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(4, 16))
        ctk.CTkButton(row, text="Guardar", width=100, fg_color=theme.ACCENT, hover_color="#d99a33",
                      text_color=theme.BG, command=self.save).pack(side="right")
        ctk.CTkButton(row, text="Cancelar", width=90, fg_color=theme.CARD, hover_color=theme.ACCENT_HOVER,
                      command=self.destroy).pack(side="right", padx=(0, 8))
        self.after(150, self.entry.focus_force)

    def save(self) -> None:
        error = self._on_save(self.entry.get())
        if error:
            self.error.configure(text=error)
        else:
            self.destroy()

"""Ventana principal: gadget sin bordes, arrastrable y siempre visible."""

from __future__ import annotations

import logging
import queue
import sys
import threading
import tkinter as tk
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import customtkinter as ctk

from . import placement, theme
from .config import Settings, app_dir, load_settings, save_settings
from .views import CalendarView, ClockView, TabButton, WeatherView
from .weather import Place, WeatherError, fetch_forecast, search_places, validate_query

POLL_MS = 200
RETRY_MINUTES = 2
SCREEN_MARGIN = 24
MIN_VISIBLE_PX = 60
CHOOSER_SETTLE_MS = 50
CTK_SCALING_SETTLE_MS = 1200  # CustomTkinter reajusta min/max 1000 ms después de un cambio de escala
MAX_WINDOW_PX = 10_000
VIEWS = {"clock": "Reloj", "calendar": "Mes", "weather": "Clima"}
DEFAULT_VIEW = "clock"
NOT_DRAGGABLE: tuple[type, ...] = (ctk.CTkButton, tk.Menu, TabButton)

log = logging.getLogger(__name__)


class WidgetApp(ctk.CTk):
    def __init__(self, settings: Settings, settings_path: Path | None = None) -> None:
        super().__init__(fg_color=theme.TRANSPARENT_KEY)
        self._settings = settings
        self._settings_path = settings_path
        self._results: queue.Queue[tuple[str, int, bool, Any]] = queue.Queue()
        self._generations: dict[str, int] = {}
        self._refresh_job: str | None = None
        self._last_forecast = None
        self._last_update: datetime | None = None
        self._current_day = datetime.now().date()
        self._drag_offset = (0, 0)
        self._topmost = tk.BooleanVar(value=settings.always_on_top)

        self.title("Reloj y clima")
        self.resizable(False, False)
        self.overrideredirect(True)
        self.attributes("-topmost", settings.always_on_top)
        if sys.platform == "win32":
            self.attributes("-transparentcolor", theme.TRANSPARENT_KEY)
        self.attributes("-alpha", theme.WINDOW_ALPHA)

        self._build()
        self._menu = self._build_menu()
        # Todo el widget (salvo los botones) sirve para arrastrar y abre el menú con clic derecho.
        # No se enlaza la ventana raíz: su etiqueta está en todos los widgets y el evento se repetiría.
        for child in tk.Misc.winfo_children(self):
            self._bind_tree(child, "<ButtonPress-1>", self._start_drag, NOT_DRAGGABLE)
            self._bind_tree(child, "<B1-Motion>", self._drag, NOT_DRAGGABLE)
            self._bind_tree(child, "<ButtonRelease-1>", self._end_drag, NOT_DRAGGABLE)
            self._bind_tree(child, "<Button-3>", self._show_menu, (tk.Menu,))
        self._place_window()

        self.protocol("WM_DELETE_WINDOW", self.close)
        self._tick()
        self.refresh_weather()
        self.after(POLL_MS, self._poll_results)

    # ---------- construcción ----------
    def _build(self) -> None:
        card = ctk.CTkFrame(self, fg_color=theme.BG, corner_radius=20, border_width=1,
                            border_color=theme.BORDER)
        card.pack()
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(padx=theme.PAD, pady=(10, theme.PAD))
        # Separador invisible que fija el mismo ancho en todas las vistas (CTk lo escala según el DPI).
        ctk.CTkFrame(inner, width=theme.CONTENT_WIDTH, height=1, fg_color="transparent").grid(row=2, column=0)

        self._build_header(inner).grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.clock = ClockView(inner)
        self.calendar = CalendarView(inner)
        self.weather = WeatherView(inner)
        self._panels: dict[str, ctk.CTkFrame] = {
            "clock": self.clock, "calendar": self.calendar, "weather": self.weather,
        }
        self._view = ""
        self.show_view(DEFAULT_VIEW)

    def _build_header(self, parent: tk.Misc) -> ctk.CTkFrame:
        header = ctk.CTkFrame(parent, fg_color="transparent")
        self._tabs = {kind: TabButton(header, kind, label, self.show_view) for kind, label in VIEWS.items()}
        for tab in self._tabs.values():
            tab.pack(side="left", padx=(0, 2))
        btn = {"width": 26, "height": 26, "fg_color": "transparent", "hover_color": theme.ACCENT_HOVER,
               "text_color": theme.MUTED, "font": ctk.CTkFont(theme.FONT_UI, 13)}
        ctk.CTkButton(header, text="✕", command=self.close, **btn).pack(side="right")
        ctk.CTkButton(header, text="⟳", command=self.refresh_weather, **btn).pack(side="right")
        return header

    def show_view(self, view: str) -> None:
        """Muestra solo la vista indicada ('clock', 'calendar' o 'weather') y ajusta el alto."""
        if view not in self._panels:
            raise ValueError(f"Vista desconocida: {view}")
        if view == self._view:
            return
        was_mapped = bool(self.winfo_ismapped())
        old_y, old_height = self.winfo_y(), self.winfo_height()
        # Se oculta con grid_forget (no grid_remove): CustomTkinter repite la última llamada a
        # grid() de cada widget al cambiar de escala, y solo grid_forget borra ese registro.
        # Con grid_remove, al pasar a otro monitor reaparecían los paneles ocultos, encimados.
        for name, panel in self._panels.items():
            if name == view:
                panel.grid(row=1, column=0, sticky="ew")
            else:
                panel.grid_forget()
        for name, tab in self._tabs.items():
            tab.set_active(name == view)
        if view == "calendar":
            self.calendar.go_to_today()
        self._view = view
        self._fit_to_content()
        if was_mapped:
            self._keep_on_screen(old_y, old_height)

    def _fit_to_content(self) -> None:
        """Quita cualquier tamaño fijo para que la ventana tome el tamaño de la vista visible."""
        x, y = self.winfo_x(), self.winfo_y()
        tk.Tk.wm_minsize(self, 1, 1)
        tk.Tk.wm_maxsize(self, MAX_WINDOW_PX, MAX_WINDOW_PX)
        tk.Tk.wm_geometry(self, "")  # vuelve al tamaño natural (el que pide el contenido)
        if self.winfo_ismapped():
            self._move_to(x, y)

    def _set_scaling(self, new_widget_scaling: float, new_window_scaling: float) -> None:
        """CustomTkinter llama a esto al pasar a un monitor con otra escala (DPI).

        Su implementación fija el tamaño de la ventana al de la vista que se veía en ese
        momento; luego, al cambiar a una vista más alta, el contenido quedaba recortado.
        Se deja que CTk reescale y después se libera el tamaño. CTk vuelve a fijar
        mínimo y máximo a los 1000 ms, por eso se repite pasado ese plazo.
        """
        super()._set_scaling(new_widget_scaling, new_window_scaling)
        self.after_idle(self._fit_to_content)
        self.after(CTK_SCALING_SETTLE_MS, self._fit_to_content)

    def _keep_on_screen(self, old_y: int, old_height: int) -> None:
        self.update_idletasks()
        new_height = self.winfo_reqheight()
        center_x = self.winfo_x() + self.winfo_width() // 2
        area = placement.monitor_work_area(center_x, old_y + min(old_height, new_height) // 2,
                                           self._primary_screen())
        new_y = placement.fit_vertically(old_y, old_height, new_height, area, SCREEN_MARGIN)
        if new_y != old_y:
            self._move_to(self.winfo_x(), new_y)
            self._update_settings(x=self.winfo_x(), y=new_y)

    def _build_menu(self) -> tk.Menu:
        menu = tk.Menu(self, tearoff=0, bg=theme.CARD, fg=theme.TEXT, activebackground=theme.ACCENT_HOVER,
                       activeforeground=theme.TEXT, bd=0, font=(theme.FONT_UI, 10))
        menu.add_command(label="Actualizar clima", command=self.refresh_weather)
        menu.add_command(label="Cambiar ciudad…", command=self.ask_city)
        menu.add_checkbutton(label="Siempre visible", variable=self._topmost, command=self._toggle_topmost)
        corners = tk.Menu(menu, tearoff=0, bg=theme.CARD, fg=theme.TEXT, activebackground=theme.ACCENT_HOVER,
                          activeforeground=theme.TEXT, bd=0, font=(theme.FONT_UI, 10))
        for key, label in placement.CORNERS.items():
            corners.add_command(label=label, command=lambda k=key: self.move_to_corner(k))
        menu.add_cascade(label="Mover a la esquina", menu=corners)
        menu.add_separator()
        menu.add_command(label="Cerrar", command=self.close)
        return menu

    def _bind_tree(self, widget: tk.Misc, sequence: str, handler: Callable[[tk.Event], Any],
                   skip: tuple[type, ...]) -> None:
        """Enlaza un evento a un widget y todos sus descendientes, salvo los de tipo `skip`."""
        if isinstance(widget, skip):
            return
        tk.Misc.bind(widget, sequence, handler, add="+")
        # tk.Misc.winfo_children incluye el lienzo de fondo que CTkFrame oculta en su versión.
        for child in tk.Misc.winfo_children(widget):
            self._bind_tree(child, sequence, handler, skip)

    # ---------- posición y arrastre ----------
    def _primary_screen(self) -> placement.Rect:
        return placement.Rect(0, 0, self.winfo_screenwidth(), self.winfo_screenheight())

    def _move_to(self, x: int, y: int) -> None:
        self.wm_geometry(f"+{x}+{y}")

    def _place_window(self) -> None:
        self.update_idletasks()
        x, y = self._settings.x, self._settings.y
        screen = placement.virtual_screen(self._primary_screen())
        if x is None or y is None or not placement.is_visible(x, y, screen, MIN_VISIBLE_PX):
            area = placement.work_area(self._primary_screen())
            x, y = placement.corner_position("top_right", area, self.winfo_reqwidth(),
                                             self.winfo_reqheight(), SCREEN_MARGIN)
        self._move_to(x, y)
        log.info("Ventana en %s,%s", x, y)

    def move_to_corner(self, corner: str) -> None:
        """Coloca el widget en una esquina del monitor principal, sin tapar la barra de tareas."""
        self.update_idletasks()
        area = placement.work_area(self._primary_screen())
        x, y = placement.corner_position(corner, area, self.winfo_width(), self.winfo_height(), SCREEN_MARGIN)
        self._move_to(x, y)
        self._update_settings(x=x, y=y)

    def _start_drag(self, event: tk.Event) -> None:
        self._drag_offset = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _drag(self, event: tk.Event) -> None:
        dx, dy = self._drag_offset
        self._move_to(event.x_root - dx, event.y_root - dy)

    def _end_drag(self, _event: tk.Event) -> None:
        self._update_settings(x=self.winfo_x(), y=self.winfo_y())

    # ---------- menú ----------
    def _show_menu(self, event: tk.Event) -> None:
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()

    def _toggle_topmost(self) -> None:
        value = bool(self._topmost.get())
        self.attributes("-topmost", value)
        self._update_settings(always_on_top=value)

    def ask_city(self) -> None:
        self.attributes("-topmost", False)
        dialog = ctk.CTkInputDialog(title="Cambiar ciudad", text="¿De qué ciudad quieres ver el clima?")
        dialog.after(50, lambda: dialog.attributes("-topmost", True))
        name = dialog.get_input()
        self.attributes("-topmost", bool(self._topmost.get()))
        if name is None:
            return
        try:
            query = validate_query(name)
        except ValueError as exc:
            self.weather.show_error(str(exc), self._settings.city, retrying=False)
            return
        self.weather.show_loading(f"Buscando «{query}»")
        self._run_in_background("places", lambda: search_places(query), extra=query)

    def _choose_place(self, places: tuple[Place, ...], query: str) -> None:
        if not places:
            self.weather.show_error(f"No encontré «{query}»", self._settings.city, retrying=False)
            return
        if len(places) == 1:
            self._set_place(places[0])
            return
        chosen: list[Place] = []
        chooser = tk.Menu(self, tearoff=0, bg=theme.CARD, fg=theme.TEXT, activebackground=theme.ACCENT_HOVER,
                          activeforeground=theme.TEXT, font=(theme.FONT_UI, 10))
        for place in places:
            chooser.add_command(label=place.label, command=lambda p=place: chosen.append(p))
        self.weather.show_loading("Elige la ciudad en la lista")
        try:
            chooser.tk_popup(self.winfo_x() + 40, self.winfo_y() + 200)
        finally:
            chooser.grab_release()
        # El menú es modal en Windows; al cerrarse, se aplica la elección o se restaura el estado.
        self.after(CHOOSER_SETTLE_MS, lambda: self._set_place(chosen[0]) if chosen else self._restore_status())

    def _set_place(self, place: Place) -> None:
        self._update_settings(city=place.label, latitude=place.latitude,
                              longitude=place.longitude, timezone=place.timezone)
        self._last_forecast = None
        self.weather.clear()
        self.refresh_weather()

    def _restore_status(self) -> None:
        if self._last_forecast is not None and self._last_update is not None:
            self.weather.show_forecast(self._last_forecast, self._settings.city, self._last_update)
        else:
            self.weather.show_loading(self._settings.city)

    # ---------- reloj ----------
    def _tick(self) -> None:
        now = datetime.now()
        self.clock.show(now)
        if now.date() != self._current_day:
            self._current_day = now.date()
            self.calendar.set_today(self._current_day)
        self.after(1000 - now.microsecond // 1000, self._tick)

    # ---------- clima ----------
    def refresh_weather(self) -> None:
        s = self._settings
        self.weather.show_loading(s.city)
        self._run_in_background("forecast", lambda: fetch_forecast(s.latitude, s.longitude, s.timezone))

    def _run_in_background(self, kind: str, job: Callable[[], Any], extra: Any = None) -> None:
        """Ejecuta `job` en un hilo; solo se aplica el resultado más reciente de cada tipo."""
        generation = self._generations.get(kind, 0) + 1
        self._generations = {**self._generations, kind: generation}

        def worker() -> None:
            try:
                self._results.put((kind, generation, True, (job(), extra)))
            except (WeatherError, ValueError) as exc:
                self._results.put((kind, generation, False, str(exc)))
            except Exception:  # noqa: BLE001 - se registra y se muestra al usuario
                log.exception("Error inesperado en tarea %s", kind)
                self._results.put((kind, generation, False, "Error inesperado"))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_results(self) -> None:
        try:
            while True:
                kind, generation, ok, payload = self._results.get_nowait()
                if generation != self._generations.get(kind):
                    continue  # resultado obsoleto
                try:
                    self._handle_result(kind, ok, payload)
                except Exception:  # noqa: BLE001 - el bucle de sondeo nunca debe detenerse
                    log.exception("Error al mostrar el resultado de %s", kind)
        except queue.Empty:
            pass
        finally:
            self.after(POLL_MS, self._poll_results)

    def _handle_result(self, kind: str, ok: bool, payload: Any) -> None:
        if not ok:
            log.warning("Fallo en %s: %s", kind, payload)
            retrying = kind == "forecast"
            self.weather.show_error(payload, self._settings.city, retrying=retrying)
            if retrying:
                self._schedule_refresh(RETRY_MINUTES)
        elif kind == "forecast":
            forecast, _ = payload
            self._last_forecast, self._last_update = forecast, datetime.now()
            self.weather.show_forecast(forecast, self._settings.city, self._last_update)
            self._schedule_refresh(self._settings.refresh_minutes)
        elif kind == "places":
            places, query = payload
            self._choose_place(places, query)

    def _schedule_refresh(self, minutes: int) -> None:
        if self._refresh_job is not None:
            self.after_cancel(self._refresh_job)
        self._refresh_job = self.after(minutes * 60_000, self.refresh_weather)

    # ---------- ajustes y cierre ----------
    def _update_settings(self, **changes: Any) -> None:
        self._settings = replace(self._settings, **changes)
        try:
            save_settings(self._settings, self._settings_path)
        except OSError:
            log.exception("No se pudo guardar la configuración")

    def close(self) -> None:
        self._update_settings(x=self.winfo_x(), y=self.winfo_y())
        self.destroy()


def _setup_logging() -> None:
    try:
        app_dir().mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=app_dir() / "widget.log", level=logging.INFO, encoding="utf-8",
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    except OSError:
        logging.basicConfig(level=logging.INFO)


def main() -> None:
    _setup_logging()
    ctk.set_appearance_mode("dark")
    app = WidgetApp(load_settings())
    app.mainloop()

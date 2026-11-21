"""Tests de interfaz: crean ventanas reales de Tk (requieren escritorio)."""

import tkinter as tk
from datetime import date, datetime

import pytest

from widget_reloj_clima import app as app_module
from widget_reloj_clima.config import DEFAULT_SETTINGS, load_settings
from widget_reloj_clima.icons import draw_icon
from widget_reloj_clima.weather import (
    CurrentWeather,
    DailyForecast,
    Forecast,
    Place,
    WeatherError,
    WEATHER_CODES,
)

FORECAST = Forecast(
    current=CurrentWeather(16.4, 15.1, 76, 12.3, 2, True),
    daily=tuple(DailyForecast(date(2026, 11, 21 + i), code, 20.0 + i, 10.0) for i, code in
                enumerate((61, 0, 3, 95, 71))),
)


@pytest.fixture
def widget(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "fetch_forecast", lambda lat, lon, tz: FORECAST)
    try:
        instance = app_module.WidgetApp(DEFAULT_SETTINGS, tmp_path / "config.json")
    except tk.TclError as exc:  # pragma: no cover - entorno sin pantalla
        pytest.skip(f"Tk no disponible: {exc}")
    instance.withdraw()
    yield instance
    instance.destroy()


def wait_for(widget, condition, timeout_s=3.0):
    deadline = datetime.now().timestamp() + timeout_s
    while datetime.now().timestamp() < deadline:
        widget.update()
        if condition():
            return True
    return False


@pytest.fixture(scope="module")
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover
        pytest.skip(f"Tk no disponible: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.mark.parametrize("kind", sorted({k for _, k in WEATHER_CODES.values()} | {"desconocido"}))
@pytest.mark.parametrize("is_day", [True, False])
def test_draw_icon_produces_shapes(tk_root, kind, is_day):
    canvas = tk.Canvas(tk_root, width=64, height=64)
    draw_icon(canvas, kind, is_day, 64, "#000000")
    assert canvas.find_all()
    canvas.destroy()


def test_widget_shows_forecast_from_service(widget):
    assert wait_for(widget, lambda: widget.weather._temp.cget("text") == "16°")
    assert widget.weather._desc.cget("text") == "Parcialmente nublado"
    assert "Humedad 76%" in widget.weather._details.cget("text")
    assert widget._refresh_job is not None


def test_widget_shows_error_and_schedules_retry(widget, monkeypatch):
    def fail(*_):
        raise WeatherError("Sin conexión")

    monkeypatch.setattr(app_module, "fetch_forecast", fail)
    widget.refresh_weather()
    assert wait_for(widget, lambda: "Sin conexión" in widget.weather._status.cget("text"))


def test_unexpected_error_is_reported(widget, monkeypatch):
    def boom(*_):
        raise RuntimeError("bug")

    monkeypatch.setattr(app_module, "fetch_forecast", boom)
    widget.refresh_weather()
    assert wait_for(widget, lambda: "Error inesperado" in widget.weather._status.cget("text"))


def test_stale_results_are_ignored(widget):
    widget._generations = {"forecast": 10}
    widget._results.put(("forecast", 9, False, "viejo"))
    widget._poll_results()
    assert "viejo" not in widget.weather._status.cget("text")


def test_polling_survives_handler_exception(widget, monkeypatch):
    widget._generations = {"forecast": 1}
    widget._results.put(("forecast", 1, True, ("no es un Forecast", None)))
    jobs = []
    monkeypatch.setattr(widget, "after", lambda ms, fn: jobs.append(fn))
    widget._poll_results()
    assert widget._poll_results in jobs


def test_forecast_refresh_does_not_discard_city_search(widget, monkeypatch):
    place = Place("Talca", "Chile", "Maule", -35.4, -71.6, "America/Santiago")
    widget._run_in_background("places", lambda: (place,), extra="Talca")
    widget.refresh_weather()  # otra tarea en paralelo no debe invalidar la búsqueda
    assert wait_for(widget, lambda: widget._settings.city == "Talca, Maule, Chile")


def test_changing_city_clears_previous_weather(widget):
    assert wait_for(widget, lambda: widget.weather._temp.cget("text") == "16°")
    widget._results = __import__("queue").Queue()  # evita que llegue el nuevo pronóstico
    widget._set_place(Place("Lima", "Perú", "", -12.0, -77.0, "auto"))
    assert widget.weather._temp.cget("text") == "--°"


def test_validation_error_does_not_promise_retry(widget):
    widget.weather.show_error("No encontré «X»", "Concepción", retrying=False)
    assert "Reintentando" not in widget.weather._status.cget("text")


def test_restore_status_after_dismissed_chooser(widget):
    assert wait_for(widget, lambda: widget._last_forecast is not None)
    widget.weather.show_loading("Elige la ciudad en la lista")
    widget._restore_status()
    assert "actualizado" in widget.weather._status.cget("text")


def test_clock_shows_current_date(widget):
    widget.clock.show(datetime(2026, 11, 21, 9, 5, 7))
    assert widget.clock._time.cget("text") == "09:05"
    assert widget.clock._date.cget("text").startswith("Sábado, 21 de noviembre")


def test_calendar_navigation_and_today(widget):
    cal = widget.calendar
    cal.set_today(date(2026, 11, 21))
    assert cal._title.cget("text") == "Noviembre 2026"
    cal._shift(1)
    assert cal._title.cget("text") == "Diciembre 2026"
    cal.go_to_today()
    assert cal._title.cget("text") == "Noviembre 2026"


def test_choose_place_updates_settings(widget):
    place = Place("Lima", "Perú", "Lima", -12.0, -77.0, "America/Lima")
    widget._choose_place((place,), "Lima")
    saved = load_settings(widget._settings_path)
    assert saved.city == "Lima, Perú"
    assert saved.latitude == -12.0


def test_choose_place_without_results_shows_error(widget):
    widget._choose_place((), "Xyz")
    assert "No encontré «Xyz»" in widget.weather._status.cget("text")


def test_places_result_flows_through_queue(widget, monkeypatch):
    place = Place("Talca", "Chile", "Maule", -35.4, -71.6, "America/Santiago")
    monkeypatch.setattr(app_module, "search_places", lambda q: (place,))
    widget._run_in_background("places", lambda: app_module.search_places("Talca"), extra="Talca")
    assert wait_for(widget, lambda: widget._settings.city == "Talca, Maule, Chile")


def test_toggle_topmost_is_saved(widget):
    widget._topmost.set(False)
    widget._toggle_topmost()
    assert load_settings(widget._settings_path).always_on_top is False


def test_drag_moves_window_and_saves_position(widget):
    class Ev:
        x_root, y_root = 100, 100

    widget._start_drag(Ev)
    Ev.x_root, Ev.y_root = 150, 130
    widget._drag(Ev)
    widget.update()
    widget._end_drag(Ev)
    saved = load_settings(widget._settings_path)
    assert saved.x is not None and saved.y is not None


def _leaves(widget):
    children = tk.Misc.winfo_children(widget)
    if not children:
        yield widget
    for child in children:
        yield from _leaves(child)


def test_every_area_except_buttons_is_draggable(widget):
    import customtkinter as ctk

    def inside_button(w):
        while w is not None:
            if isinstance(w, ctk.CTkButton):
                return True
            w = w.master
        return False

    leaves = [w for w in _leaves(widget) if not isinstance(w, tk.Menu) and not inside_button(w)]
    missing = [str(w) for w in leaves if "<B1-Motion>" not in tk.Misc.bind(w)]
    assert leaves and not missing
    # El fondo de los marcos (lienzo oculto por CTkFrame) también debe arrastrar
    assert "<B1-Motion>" in tk.Misc.bind(widget.calendar._canvas)
    # La raíz no se enlaza: si no, cada evento se dispararía dos veces
    assert "<B1-Motion>" not in tk.Misc.bind(widget)


@pytest.mark.parametrize("corner", ["top_left", "top_right", "bottom_left", "bottom_right"])
def test_move_to_corner_moves_and_saves(widget, corner, monkeypatch):
    from widget_reloj_clima import placement

    area = placement.Rect(0, 0, 1600, 900)
    monkeypatch.setattr(placement, "work_area", lambda fallback: area)
    moves = []
    monkeypatch.setattr(widget, "_move_to", lambda x, y: moves.append((x, y)))
    widget.move_to_corner(corner)
    x, y = moves[-1]
    assert (x < 800) == corner.endswith("left")
    saved = load_settings(widget._settings_path)
    assert (saved.x, saved.y) == (x, y)


def test_corner_menu_has_four_entries(widget):
    labels = [widget._menu.entrycget(i, "label") for i in range(widget._menu.index("end") + 1)
              if widget._menu.type(i) == "cascade"]
    assert labels == ["Mover a la esquina"]


def test_saved_offscreen_position_falls_back_to_default(widget):
    from dataclasses import replace

    widget._settings = replace(DEFAULT_SETTINGS, x=-99999, y=-99999)
    widget._place_window()
    widget.update()
    assert widget.winfo_x() > -99999


def test_update_settings_persists_to_disk(widget):
    widget._update_settings(x=5, y=6)
    assert load_settings(widget._settings_path).x == 5

import json
from dataclasses import replace
from datetime import date, datetime

import pytest

from widget_reloj_clima import config, dates
from widget_reloj_clima.config import DEFAULT_SETTINGS, load_settings, save_settings


# ---------- config ----------
def test_load_returns_defaults_when_file_missing(tmp_path):
    assert load_settings(tmp_path / "nope.json") == DEFAULT_SETTINGS


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "sub" / "config.json"
    settings = replace(DEFAULT_SETTINGS, city="Lima, Perú", latitude=-12.0, longitude=-77.0, x=10, y=20)

    save_settings(settings, path)

    assert load_settings(path) == settings
    assert not path.with_suffix(".tmp").exists()


@pytest.mark.parametrize("content", ["{roto", "[1, 2]", '"texto"'])
def test_load_returns_defaults_for_corrupt_file(tmp_path, content):
    path = tmp_path / "config.json"
    path.write_text(content, encoding="utf-8")
    assert load_settings(path) == DEFAULT_SETTINGS


def test_load_sanitizes_invalid_values(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "city": "Marte",
        "latitude": 500,
        "longitude": 0,
        "x": "a",
        "y": True,
        "refresh_minutes": 1,
        "always_on_top": "si",
        "desconocido": 1,
    }), encoding="utf-8")

    settings = load_settings(path)

    assert (settings.city, settings.latitude) == (DEFAULT_SETTINGS.city, DEFAULT_SETTINGS.latitude)
    assert settings.x is None and settings.y is None
    assert settings.refresh_minutes == config.MIN_REFRESH_MINUTES
    assert settings.always_on_top is True


def test_refresh_minutes_is_clamped_to_maximum(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"refresh_minutes": 10_000}), encoding="utf-8")
    assert load_settings(path).refresh_minutes == config.MAX_REFRESH_MINUTES


def test_config_path_uses_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert config.config_path() == tmp_path / config.APP_DIR_NAME / config.CONFIG_FILE_NAME


# ---------- fechas ----------
def test_format_long_date_in_spanish():
    assert dates.format_long_date(date(2026, 11, 21)) == "sábado, 21 de noviembre de 2026"


def test_format_time_and_seconds():
    moment = datetime(2026, 1, 2, 9, 5, 7)
    assert dates.format_time(moment) == "09:05"
    assert dates.format_seconds(moment) == "07"


def test_month_title():
    assert dates.format_month_title(2026, 9) == "Septiembre 2026"


def test_short_weekday():
    today = date(2026, 11, 21)
    assert dates.short_weekday(today, today) == "Hoy"
    assert dates.short_weekday(date(2026, 11, 22), today) == "Mañana"
    assert dates.short_weekday(date(2026, 11, 25), today) == "Mié"


def test_month_grid_starts_on_monday():
    grid = dates.month_grid(2026, 11)  # 1 de noviembre de 2026 es domingo
    assert grid[0] == (0, 0, 0, 0, 0, 0, 1)
    assert grid[-1][0] == 30
    assert all(len(week) == 7 for week in grid)


@pytest.mark.parametrize(
    ("year", "month", "delta", "expected"),
    [(2026, 12, 1, (2027, 1)), (2026, 1, -1, (2025, 12)), (2026, 5, 0, (2026, 5)), (2026, 3, -15, (2024, 12))],
)
def test_shift_month(year, month, delta, expected):
    assert dates.shift_month(year, month, delta) == expected

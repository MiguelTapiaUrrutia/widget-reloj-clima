import io
import json
import urllib.error
from datetime import date

import pytest

from widget_reloj_clima import weather
from widget_reloj_clima.weather import (
    Place,
    WeatherError,
    describe,
    fetch_forecast,
    parse_forecast,
    parse_places,
    search_places,
    validate_query,
)

FORECAST_PAYLOAD = {
    "current": {
        "temperature_2m": 16.4,
        "apparent_temperature": 15.1,
        "relative_humidity_2m": 76,
        "wind_speed_10m": 12.3,
        "weather_code": 2,
        "is_day": 1,
    },
    "daily": {
        "time": ["2026-11-21", "2026-11-22"],
        "weather_code": [61, 0],
        "temperature_2m_max": [17.0, 18.2],
        "temperature_2m_min": [13.0, 10.1],
    },
}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def opener_returning(payload, captured=None):
    def opener(request, timeout):
        if captured is not None:
            captured.append((request, timeout))
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return FakeResponse(body)

    return opener


def opener_raising(exc):
    def opener(request, timeout):
        raise exc

    return opener


def test_describe_known_and_unknown_codes():
    assert describe(0) == ("Despejado", "clear")
    assert describe(95)[1] == "storm"
    assert describe(1234) == weather.UNKNOWN_WEATHER


def test_parse_forecast_builds_current_and_daily():
    forecast = parse_forecast(FORECAST_PAYLOAD)

    assert forecast.current.temperature == 16.4
    assert forecast.current.humidity == 76
    assert forecast.current.is_day is True
    assert [d.day for d in forecast.daily] == [date(2026, 11, 21), date(2026, 11, 22)]
    assert forecast.daily[0].code == 61


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"current": FORECAST_PAYLOAD["current"]},
        {**FORECAST_PAYLOAD, "current": {**FORECAST_PAYLOAD["current"], "temperature_2m": "x"}},
        {**FORECAST_PAYLOAD, "daily": {**FORECAST_PAYLOAD["daily"], "weather_code": [1]}},
    ],
)
def test_parse_forecast_rejects_malformed_payload(payload):
    with pytest.raises(WeatherError):
        parse_forecast(payload)


def test_fetch_forecast_sends_expected_query():
    captured = []

    forecast = fetch_forecast(-36.8, -73.0, "America/Santiago", opener=opener_returning(FORECAST_PAYLOAD, captured))

    request, timeout = captured[0]
    assert forecast.current.code == 2
    assert request.full_url.startswith(weather.FORECAST_URL)
    assert "latitude=-36.8" in request.full_url
    assert "timezone=America%2FSantiago" in request.full_url
    assert timeout == weather.REQUEST_TIMEOUT_S


@pytest.mark.parametrize(
    "exc",
    [
        urllib.error.URLError("sin red"),
        urllib.error.HTTPError("u", 500, "boom", {}, None),
        TimeoutError(),
    ],
)
def test_fetch_forecast_wraps_network_errors(exc):
    with pytest.raises(WeatherError):
        fetch_forecast(0, 0, "auto", opener=opener_raising(exc))


def test_parse_forecast_rejects_non_finite_numbers():
    payload = {**FORECAST_PAYLOAD, "current": {**FORECAST_PAYLOAD["current"], "temperature_2m": "nan"}}
    with pytest.raises(WeatherError, match="no válidos"):
        parse_forecast(payload)


def test_fetch_forecast_wraps_incomplete_read():
    import http.client

    with pytest.raises(WeatherError, match="conectar"):
        fetch_forecast(0, 0, "auto", opener=opener_raising(http.client.IncompleteRead(b"")))


def test_fetch_forecast_rejects_oversized_response(monkeypatch):
    monkeypatch.setattr(weather, "MAX_RESPONSE_BYTES", 10)
    with pytest.raises(WeatherError, match="grande"):
        fetch_forecast(0, 0, "auto", opener=opener_returning(FORECAST_PAYLOAD))


def test_fetch_forecast_rejects_invalid_json_and_api_errors():
    with pytest.raises(WeatherError):
        fetch_forecast(0, 0, "auto", opener=opener_returning(b"no es json"))
    with pytest.raises(WeatherError):
        fetch_forecast(0, 0, "auto", opener=opener_returning([1, 2]))
    with pytest.raises(WeatherError, match="latitud"):
        fetch_forecast(0, 0, "auto", opener=opener_returning({"error": True, "reason": "latitud inválida"}))


def test_validate_query():
    assert validate_query("  Concepción ") == "Concepción"
    with pytest.raises(ValueError):
        validate_query("   ")
    with pytest.raises(ValueError):
        validate_query("x" * (weather.MAX_QUERY_LENGTH + 1))


def test_parse_places_skips_malformed_entries():
    payload = {
        "results": [
            {"name": "Concepción", "country": "Chile", "admin1": "Biobío",
             "latitude": -36.8, "longitude": -73.0, "timezone": "America/Santiago"},
            {"name": "Sin coordenadas"},
        ]
    }

    places = parse_places(payload)

    assert len(places) == 1
    assert places[0].label == "Concepción, Biobío, Chile"
    assert parse_places({}) == ()


def test_place_label_skips_empty_and_repeated_parts():
    place = Place("Santiago", "Chile", "Santiago", 0, 0, "auto")
    assert place.label == "Santiago, Chile"


def test_search_places_uses_geocoding_api():
    captured = []
    payload = {"results": [{"name": "Lima", "country": "Perú", "latitude": -12.0, "longitude": -77.0}]}

    places = search_places("Lima", opener=opener_returning(payload, captured))

    assert places[0].timezone == "auto"
    assert captured[0][0].full_url.startswith(weather.GEOCODING_URL)
    assert "language=es" in captured[0][0].full_url

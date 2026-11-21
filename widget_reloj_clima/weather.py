"""Cliente de Open-Meteo (gratuito, sin clave) y modelos de datos del clima."""

from __future__ import annotations

import http.client
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
REQUEST_TIMEOUT_S = 10
FORECAST_DAYS = 5
MAX_PLACE_RESULTS = 5
MAX_QUERY_LENGTH = 100
USER_AGENT = "widget-reloj-clima/1.0"
MAX_RESPONSE_BYTES = 1_000_000

CURRENT_FIELDS = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "wind_speed_10m",
    "weather_code",
    "is_day",
)
DAILY_FIELDS = ("weather_code", "temperature_2m_max", "temperature_2m_min")

# Códigos WMO usados por Open-Meteo -> (descripción, tipo de icono)
WEATHER_CODES: dict[int, tuple[str, str]] = {
    0: ("Despejado", "clear"),
    1: ("Mayormente despejado", "clear"),
    2: ("Parcialmente nublado", "partly"),
    3: ("Nublado", "cloudy"),
    45: ("Niebla", "fog"),
    48: ("Niebla con escarcha", "fog"),
    51: ("Llovizna ligera", "drizzle"),
    53: ("Llovizna", "drizzle"),
    55: ("Llovizna intensa", "drizzle"),
    56: ("Llovizna helada", "drizzle"),
    57: ("Llovizna helada intensa", "drizzle"),
    61: ("Lluvia ligera", "rain"),
    63: ("Lluvia", "rain"),
    65: ("Lluvia intensa", "rain"),
    66: ("Lluvia helada", "rain"),
    67: ("Lluvia helada intensa", "rain"),
    71: ("Nieve ligera", "snow"),
    73: ("Nieve", "snow"),
    75: ("Nieve intensa", "snow"),
    77: ("Granizo fino", "snow"),
    80: ("Chubascos ligeros", "rain"),
    81: ("Chubascos", "rain"),
    82: ("Chubascos fuertes", "rain"),
    85: ("Chubascos de nieve", "snow"),
    86: ("Chubascos de nieve fuertes", "snow"),
    95: ("Tormenta", "storm"),
    96: ("Tormenta con granizo", "storm"),
    99: ("Tormenta con granizo fuerte", "storm"),
}
UNKNOWN_WEATHER = ("Sin datos", "cloudy")

Opener = Callable[..., Any]


class WeatherError(Exception):
    """Error al obtener o interpretar datos del servicio de clima."""


@dataclass(frozen=True)
class CurrentWeather:
    temperature: float
    apparent_temperature: float
    humidity: int
    wind_speed: float
    code: int
    is_day: bool


@dataclass(frozen=True)
class DailyForecast:
    day: date
    code: int
    temp_max: float
    temp_min: float


@dataclass(frozen=True)
class Forecast:
    current: CurrentWeather
    daily: tuple[DailyForecast, ...]


@dataclass(frozen=True)
class Place:
    name: str
    country: str
    admin: str
    latitude: float
    longitude: float
    timezone: str

    @property
    def label(self) -> str:
        parts = [self.name, self.admin, self.country]
        unique = [p for i, p in enumerate(parts) if p and p not in parts[:i]]
        return ", ".join(unique)


def describe(code: int) -> tuple[str, str]:
    """Devuelve (descripción en español, tipo de icono) para un código WMO."""
    return WEATHER_CODES.get(code, UNKNOWN_WEATHER)


def _get_json(base_url: str, params: dict[str, Any], opener: Opener) -> dict[str, Any]:
    url = f"{base_url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=REQUEST_TIMEOUT_S) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise WeatherError("Respuesta del servicio demasiado grande")
        payload = json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise WeatherError(f"El servicio respondió con error {exc.code}") from exc
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
        raise WeatherError("No se pudo conectar con el servicio de clima") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WeatherError("Respuesta del servicio no válida") from exc
    if not isinstance(payload, dict):
        raise WeatherError("Respuesta del servicio no válida")
    if payload.get("error"):
        raise WeatherError(str(payload.get("reason", "Error del servicio de clima")))
    return payload


def forecast_params(latitude: float, longitude: float, timezone: str) -> dict[str, Any]:
    return {
        "latitude": latitude,
        "longitude": longitude,
        "current": ",".join(CURRENT_FIELDS),
        "daily": ",".join(DAILY_FIELDS),
        "timezone": timezone or "auto",
        "forecast_days": FORECAST_DAYS,
        "wind_speed_unit": "kmh",
    }


def parse_forecast(payload: dict[str, Any]) -> Forecast:
    """Convierte la respuesta JSON de Open-Meteo en un Forecast validado."""
    try:
        cur = payload["current"]
        current = CurrentWeather(
            temperature=float(cur["temperature_2m"]),
            apparent_temperature=float(cur["apparent_temperature"]),
            humidity=int(round(float(cur["relative_humidity_2m"]))),
            wind_speed=float(cur["wind_speed_10m"]),
            code=int(cur["weather_code"]),
            is_day=bool(int(cur["is_day"])),
        )
        daily_raw = payload["daily"]
        rows = zip(
            daily_raw["time"],
            daily_raw["weather_code"],
            daily_raw["temperature_2m_max"],
            daily_raw["temperature_2m_min"],
            strict=True,
        )
        daily = tuple(
            DailyForecast(
                day=date.fromisoformat(day),
                code=int(code),
                temp_max=float(t_max),
                temp_min=float(t_min),
            )
            for day, code, t_max, t_min in rows
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherError("Datos de clima incompletos o con formato inesperado") from exc
    numbers = [current.temperature, current.apparent_temperature, current.wind_speed]
    numbers += [t for d in daily for t in (d.temp_max, d.temp_min)]
    if not all(math.isfinite(n) for n in numbers):
        raise WeatherError("Datos de clima con valores no válidos")
    return Forecast(current=current, daily=daily)


def fetch_forecast(
    latitude: float,
    longitude: float,
    timezone: str,
    opener: Opener = urllib.request.urlopen,
) -> Forecast:
    payload = _get_json(FORECAST_URL, forecast_params(latitude, longitude, timezone), opener)
    return parse_forecast(payload)


def validate_query(name: str) -> str:
    query = (name or "").strip()
    if not query:
        raise ValueError("Escribe el nombre de una ciudad")
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError("El nombre de la ciudad es demasiado largo")
    return query


def parse_places(payload: dict[str, Any]) -> tuple[Place, ...]:
    places = []
    for item in payload.get("results") or []:
        try:
            places.append(
                Place(
                    name=str(item["name"]),
                    country=str(item.get("country", "")),
                    admin=str(item.get("admin1", "")),
                    latitude=float(item["latitude"]),
                    longitude=float(item["longitude"]),
                    timezone=str(item.get("timezone", "auto")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue  # se ignoran resultados mal formados
    return tuple(places)


def search_places(name: str, opener: Opener = urllib.request.urlopen) -> tuple[Place, ...]:
    """Busca ciudades por nombre usando el geocodificador de Open-Meteo."""
    params = {
        "name": validate_query(name),
        "count": MAX_PLACE_RESULTS,
        "language": "es",
        "format": "json",
    }
    return parse_places(_get_json(GEOCODING_URL, params, opener))

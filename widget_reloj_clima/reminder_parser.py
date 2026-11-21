"""Interpreta recordatorios escritos en español: «hoy a las 22 imprimir moldes»."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

MAX_TEXT_LENGTH = 200
MIN_LEAD_SECONDS = 30  # un recordatorio debe quedar al menos este tiempo en el futuro

_ACCENTS = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
_WEEKDAYS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
_MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
           "septiembre", "octubre", "noviembre", "diciembre")
_NUMBER_WORDS = {"un": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "diez": 10,
                 "quince": 15, "veinte": 20, "treinta": 30}

# re.ASCII: \d y \b solo con cifras y letras ASCII (el texto ya va sin tildes)
_RELATIVE_RE = re.compile(
    r"\ben\s+(?P<n>\d{1,4}|un|una|dos|tres|cuatro|cinco|diez|quince|veinte|treinta|media)\s*"
    r"(?P<unit>minutos?|mins?|horas?|hrs?|h|dias?)\b",
    re.ASCII,
)
# «… y 15 minutos» / «… y media», solo detrás de una cantidad en horas
_EXTRA_MINUTES_RE = re.compile(r"\s+y\s+(?:(?P<n2>\d{1,2})\s*(?:minutos?|mins?)\b|(?P<half>media)\b)", re.ASCII)
_NUMERIC_DATE_RE = re.compile(r"\b(?:el\s+)?(?P<d>\d{1,2})[/-](?P<m>\d{1,2})(?:[/-](?P<y>\d{2,4}))?\b",
                              re.ASCII)
_WORD_DATE_RE = re.compile(
    r"\b(?:el\s+)?(?P<d>\d{1,2})\s+de\s+(?P<month>" + "|".join(_MONTHS) + r")(?:\s+(?:de\s+)?(?P<y>\d{4}))?\b",
    re.ASCII,
)
# «mañana» como día, pero no en «de la mañana» ni «esta mañana» (eso es una parte del día)
_DAY_WORD_RE = re.compile(r"(?<!\bla\s)(?<!esta\s)\b(?P<word>pasado\s+manana|manana|hoy)\b", re.ASCII)
_WEEKDAY_RE = re.compile(r"\b(?:el\s+)?(?:proximo\s+)?(?P<wd>" + "|".join(_WEEKDAYS) + r")(?:\s+que\s+viene)?\b",
                         re.ASCII)
_THIS_PERIOD_RE = re.compile(r"\besta\s+(?P<period>manana|tarde|noche)\b", re.ASCII)
_NAMED_TIME_RE = re.compile(r"\b(?:a\s+)?(?:al\s+|a\s+la\s+|la\s+)?(?P<name>mediodia|medianoche)\b", re.ASCII)
_TIME_RE = re.compile(
    r"(?P<prefix>\b(?:a\s+)?(?:las|la)\s+)?"
    r"\b(?P<h>\d{1,2})(?:\s*[:.h]\s*(?P<m>\d{2}))?"
    r"(?:\s*(?P<ampm>am|pm|a\.m\.|p\.m\.))?"
    r"(?P<suffix>\s*(?:hrs|horas|hs|h)\b)?"
    r"(?:\s+(?:de\s+la|en\s+la|por\s+la)\s+(?P<period>manana|tarde|noche|madrugada))?",
    re.ASCII,
)
MAX_RELATIVE = timedelta(days=3650)
LEAP_YEAR_SEARCH = 8
_LEADING_FILLER_RE = re.compile(
    r"^(?:(?:recuerdame|recordarme|recordar|avisame|avisarme|que|para|de|a|y)\b\s*|[,:;-]\s*)+",
    re.IGNORECASE,
)
_PERIOD_DEFAULT_HOUR = {"manana": 9, "tarde": 16, "noche": 21}


class ReminderParseError(ValueError):
    """El texto no se pudo convertir en un recordatorio válido."""


@dataclass(frozen=True)
class ParsedReminder:
    text: str
    due: datetime


@dataclass(frozen=True)
class _Found:
    day: date | None = None
    day_kind: str | None = None  # "date", "word" o "weekday"
    hour: int | None = None
    minute: int = 0
    period: str | None = None  # manana / tarde / noche / madrugada / am / pm
    relative: timedelta | None = None


def _normalize(text: str) -> str:
    """Minúsculas y sin tildes, con la misma longitud que el original (los índices coinciden).

    Se convierte carácter a carácter: algunos («İ») se vuelven dos al pasar a minúscula,
    y en ese caso se dejan tal cual para no desalinear los índices.
    """
    out = []
    for char in text.translate(_ACCENTS):
        lower = char.lower()
        out.append(lower if len(lower) == 1 else char)
    return "".join(out)


def _blank(norm: str, match: re.Match[str], spans: list[tuple[int, int]]) -> str:
    start, end = match.span()
    spans.append((start, end))
    return norm[:start] + " " * (end - start) + norm[end:]


def _number(word: str) -> float:
    if word == "media":
        return 0.5
    return float(_NUMBER_WORDS.get(word, word))


def _relative(norm: str, spans: list) -> tuple[str, timedelta | None]:
    match = _RELATIVE_RE.search(norm)
    if not match:
        return norm, None
    n, unit = _number(match["n"]), match["unit"]
    start, end = match.span()
    if unit.startswith("d"):
        delta = timedelta(days=n)
    elif unit.startswith("h"):
        delta = timedelta(hours=n)
        extra = _EXTRA_MINUTES_RE.match(norm, end)
        if extra:
            delta += timedelta(minutes=30 if extra["half"] else int(extra["n2"]))
            end = extra.end()
    else:
        delta = timedelta(minutes=n)
    if delta <= timedelta(0):
        raise ReminderParseError("El tiempo indicado debe ser mayor que cero")
    if delta > MAX_RELATIVE:
        raise ReminderParseError("Ese plazo es demasiado largo (máximo 10 años)")
    spans.append((start, end))
    return norm[:start] + " " * (end - start) + norm[end:], delta


def _safe_date(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ReminderParseError(f"La fecha {day:02d}/{month:02d}/{year} no existe") from exc


def _date(norm: str, spans: list, today: date) -> tuple[str, date | None, str | None]:
    match = _WORD_DATE_RE.search(norm)
    if match:
        month = _MONTHS.index(match["month"]) + 1
        return _blank(norm, match, spans), _infer_year(int(match["d"]), month, match["y"], today), "date"
    match = _NUMERIC_DATE_RE.search(norm)
    if match:
        day = _infer_year(int(match["d"]), int(match["m"]), match["y"], today)
        return _blank(norm, match, spans), day, "date"
    match = _DAY_WORD_RE.search(norm)
    if match:
        offset = {"hoy": 0, "manana": 1}.get(match["word"], 2)
        return _blank(norm, match, spans), today + timedelta(days=offset), "word"
    match = _WEEKDAY_RE.search(norm)
    if match:
        days_ahead = (_WEEKDAYS.index(match["wd"]) - today.weekday()) % 7
        return _blank(norm, match, spans), today + timedelta(days=days_ahead), "weekday"
    return norm, None, None


def _infer_year(day: int, month: int, year_text: str | None, today: date) -> date:
    if year_text:
        year = int(year_text)
        year = year + 2000 if year < 100 else year
        return _safe_date(year, month, day)
    if month == 2 and day == 29:  # el próximo 29 de febrero que exista
        for year in range(today.year, today.year + LEAP_YEAR_SEARCH):
            if calendar.isleap(year) and date(year, 2, 29) >= today:
                return date(year, 2, 29)
    candidate = _safe_date(today.year, month, day)
    return candidate if candidate >= today else _safe_date(today.year + 1, month, day)


def _time(norm: str, spans: list) -> tuple[str, int | None, int, str | None]:
    match = _NAMED_TIME_RE.search(norm)
    if match:
        hour = 12 if match["name"] == "mediodia" else 0
        return _blank(norm, match, spans), hour, 0, "named"
    for match in _TIME_RE.finditer(norm):
        is_time = match["prefix"] or match["m"] or match["ampm"] or match["suffix"] or match["period"]
        if not is_time:
            continue  # un número suelto («imprimir 3 moldes») no es una hora
        hour, minute = int(match["h"]), int(match["m"] or 0)
        if hour > 23 or minute > 59:
            raise ReminderParseError(f"La hora {hour}:{minute:02d} no es válida")
        ampm = (match["ampm"] or "").replace(".", "") or None
        return _blank(norm, match, spans), hour, minute, match["period"] or ampm
    return norm, None, 0, None


def _apply_period(hour: int, period: str | None) -> int:
    if period in ("pm", "tarde", "noche") and hour < 12:
        return hour + 12
    if period in ("am", "manana", "madrugada") and hour == 12:
        return 0
    return hour


def _clean_text(original: str, spans: list[tuple[int, int]]) -> str:
    chars = list(original)
    for start, end in spans:
        for i in range(start, end):
            chars[i] = " "
    text = re.sub(r"\s+", " ", "".join(chars)).strip()
    normalized = _normalize(text)
    filler = _LEADING_FILLER_RE.match(normalized)
    if filler:
        text = text[filler.end():]
    return text.strip(" ,.:;-").strip()


def _resolve(found: _Found, now: datetime) -> datetime:
    if found.relative is not None:
        if found.hour is not None and found.relative >= timedelta(days=1):
            # «en 3 días a las 10»: ese día, a esa hora
            day = (now + found.relative).date()
            hour = _apply_period(found.hour, found.period)
            return datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=found.minute)
        return now + found.relative
    if found.hour is None:
        raise ReminderParseError("Indica la hora, por ejemplo «hoy a las 22» o «en 30 minutos»")
    hour = _apply_period(found.hour, found.period)
    day = found.day or now.date()
    if found.period == "noche" and found.hour == 12:
        hour, day = 0, day + timedelta(days=1)  # «12 de la noche» = medianoche, al final de ese día
    due = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=found.minute)
    if due > now:
        return due
    if found.period is None and found.hour <= 12 and day == now.date() and found.day_kind != "weekday":
        # «a las 9» cuando ya pasaron las 9 de la mañana: se entiende las 21 de hoy
        evening = due.replace(hour=(found.hour % 12) + 12)
        if evening > now:
            return evening
    if found.day_kind is None:
        return due + timedelta(days=1)  # sin día: la próxima vez que llegue esa hora
    if found.day_kind == "weekday":
        return due + timedelta(days=7)  # «el viernes» dicho un viernes ya pasada la hora
    return due  # fecha explícita en el pasado: se rechaza después


def parse_reminder(text: str, now: datetime) -> ParsedReminder:
    """Convierte «hoy a las 22 imprimir moldes» en (texto, fecha y hora).

    Cualquier problema con el texto se informa como ReminderParseError (nunca otra excepción).
    """
    try:
        return _parse(text, now)
    except (ArithmeticError, IndexError) as exc:
        raise ReminderParseError("No entendí la fecha de ese recordatorio") from exc


def _parse(text: str, now: datetime) -> ParsedReminder:
    original = (text or "").strip()
    if not original:
        raise ReminderParseError("Escribe un recordatorio, por ejemplo «hoy a las 22 imprimir moldes»")
    if len(original) > MAX_TEXT_LENGTH:
        raise ReminderParseError(f"El recordatorio no puede superar {MAX_TEXT_LENGTH} caracteres")

    spans: list[tuple[int, int]] = []
    norm = _normalize(original)
    norm, relative = _relative(norm, spans)
    norm, this_period = _this_period(norm, spans)
    norm, day, day_kind = _date(norm, spans, now.date())
    norm, hour, minute, period = _time(norm, spans)
    if this_period:
        day, day_kind = day or now.date(), day_kind or "word"
        if hour is None:
            hour, period = _PERIOD_DEFAULT_HOUR[this_period], "named"
        else:
            period = period or this_period

    found = _Found(day=day, day_kind=day_kind, hour=hour, minute=minute, period=period, relative=relative)
    due = _resolve(found, now).replace(second=0, microsecond=0)
    if due < now + timedelta(seconds=MIN_LEAD_SECONDS) and relative is None:
        raise ReminderParseError(f"Esa hora ya pasó ({due:%d/%m %H:%M})")

    message = _clean_text(original, spans)
    if not message:
        raise ReminderParseError("Falta el texto del recordatorio, por ejemplo «imprimir moldes»")
    return ParsedReminder(text=message[0].upper() + message[1:], due=due)


def _this_period(norm: str, spans: list) -> tuple[str, str | None]:
    match = _THIS_PERIOD_RE.search(norm)
    if not match:
        return norm, None
    return _blank(norm, match, spans), match["period"]

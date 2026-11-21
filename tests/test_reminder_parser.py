from datetime import datetime

import pytest

from widget_reloj_clima.reminder_parser import MAX_TEXT_LENGTH, ReminderParseError, parse_reminder

NOW = datetime(2026, 11, 21, 11, 58, 30)  # sábado


@pytest.mark.parametrize(
    ("text", "expected_text", "expected_due"),
    [
        ("hoy a las 22 imprimir moldes", "Imprimir moldes", datetime(2026, 11, 21, 22, 0)),
        ("Imprimir moldes hoy a las 22:00", "Imprimir moldes", datetime(2026, 11, 21, 22, 0)),
        ("mañana 9:30 llamar al banco", "Llamar al banco", datetime(2026, 11, 22, 9, 30)),
        ("mañana a las 9 de la mañana regar", "Regar", datetime(2026, 11, 22, 9, 0)),
        ("pasado mañana a las 18 h dentista", "Dentista", datetime(2026, 11, 23, 18, 0)),
        ("a las 7 de la tarde sacar la basura", "Sacar la basura", datetime(2026, 11, 21, 19, 0)),
        ("a las 8 pm cena", "Cena", datetime(2026, 11, 21, 20, 0)),
        ("a las 10 regar las plantas", "Regar las plantas", datetime(2026, 11, 21, 22, 0)),
        ("a las 11:00 de la mañana reunión", "Reunión", datetime(2026, 11, 22, 11, 0)),
        ("13:15 almorzar", "Almorzar", datetime(2026, 11, 21, 13, 15)),
        ("22h30 apagar impresora", "Apagar impresora", datetime(2026, 11, 21, 22, 30)),
        ("en 15 minutos sacar el pan", "Sacar el pan", datetime(2026, 11, 21, 12, 13)),
        ("en una hora revisar impresión", "Revisar impresión", datetime(2026, 11, 21, 12, 58)),
        ("en 2 horas y media cambiar filamento", "Cambiar filamento", datetime(2026, 11, 21, 14, 28)),
        ("en media hora tomar agua", "Tomar agua", datetime(2026, 11, 21, 12, 28)),
        ("en 3 días pagar la luz a las 10", "Pagar la luz", datetime(2026, 11, 24, 10, 0)),
        ("en 2 días recoger paquete", "Recoger paquete", datetime(2026, 11, 23, 11, 58)),
        ("el lunes a las 9 enviar informe", "Enviar informe", datetime(2026, 11, 23, 9, 0)),
        ("el sábado a las 10 feria", "Feria", datetime(2026, 11, 28, 10, 0)),
        ("viernes 18:00 cumpleaños de Ana", "Cumpleaños de Ana", datetime(2026, 11, 27, 18, 0)),
        ("25/12 a las 10 llamar a la familia", "Llamar a la familia", datetime(2026, 12, 25, 10, 0)),
        ("el 3 de marzo a las 8:30 renovar licencia", "Renovar licencia", datetime(2027, 3, 3, 8, 30)),
        ("10/01/2027 09:00 vacuna", "Vacuna", datetime(2027, 1, 10, 9, 0)),
        ("esta noche ver la película", "Ver la película", datetime(2026, 11, 21, 21, 0)),
        ("esta tarde a las 5 caminar", "Caminar", datetime(2026, 11, 21, 17, 0)),
        ("al mediodía almorzar con Pedro", "Almorzar con Pedro", datetime(2026, 11, 21, 12, 0)),
        ("a medianoche cerrar sesión", "Cerrar sesión", datetime(2026, 11, 22, 0, 0)),
        ("recuérdame mañana a las 8 sacar 3 moldes", "Sacar 3 moldes", datetime(2026, 11, 22, 8, 0)),
        ("HOY A LAS 22 IMPRIMIR MOLDES", "IMPRIMIR MOLDES", datetime(2026, 11, 21, 22, 0)),
    ],
)
def test_parse_valid_reminders(text, expected_text, expected_due):
    result = parse_reminder(text, NOW)
    assert (result.text, result.due) == (expected_text, expected_due)


def test_time_without_day_that_already_passed_moves_to_tomorrow():
    result = parse_reminder("a las 11:30 de la mañana café", NOW)
    assert result.due == datetime(2026, 11, 22, 11, 30)


def test_numbers_inside_text_are_not_taken_as_hours():
    result = parse_reminder("hoy a las 15 imprimir 4 piezas de 2 colores", NOW)
    assert result.text == "Imprimir 4 piezas de 2 colores"
    assert result.due == datetime(2026, 11, 21, 15, 0)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "Escribe un recordatorio"),
        ("   ", "Escribe un recordatorio"),
        ("imprimir moldes", "Indica la hora"),
        ("mañana imprimir moldes", "Indica la hora"),
        ("hoy a las 22", "Falta el texto"),
        ("a las 25 algo", "no es válida"),
        ("a las 10:75 algo", "no es válida"),
        ("31/02 a las 10 algo", "no existe"),
        ("01/01/2020 a las 10 algo", "ya pasó"),
        ("hoy a las 8 de la mañana algo", "ya pasó"),
        ("en 0 minutos algo", "mayor que cero"),
        ("x" * (MAX_TEXT_LENGTH + 1), "no puede superar"),
    ],
)
def test_parse_invalid_reminders(text, message):
    with pytest.raises(ReminderParseError, match=message):
        parse_reminder(text, NOW)


def test_past_date_without_year_goes_to_next_year():
    result = parse_reminder("el 1 de enero a las 0:30 brindar", NOW)
    assert result.due == datetime(2027, 1, 1, 0, 30)


@pytest.mark.parametrize(
    ("text", "expected_text", "expected_due"),
    [
        # «y N» solo cuenta como minutos detrás de horas y con la palabra «minutos»
        ("en 20 minutos y 5 moldes imprimir", "5 moldes imprimir", datetime(2026, 11, 21, 12, 18)),
        ("en 2 horas y 3 personas llegan", "3 personas llegan", datetime(2026, 11, 21, 13, 58)),
        ("en 1 hora y 15 minutos regar", "Regar", datetime(2026, 11, 21, 13, 13)),
        # «12 de la noche» es medianoche
        ("hoy a las 12 de la noche apagar", "Apagar", datetime(2026, 11, 22, 0, 0)),
        ("el 29 de febrero a las 10 bisiesto", "Bisiesto", datetime(2028, 2, 29, 10, 0)),
    ],
)
def test_review_regressions(text, expected_text, expected_due):
    result = parse_reminder(text, NOW)
    assert (result.text, result.due) == (expected_text, expected_due)


@pytest.mark.parametrize(
    "text",
    [
        "en 5000000 dias x",
        "en " + "9" * 50 + " horas x",
        "en 9999 dias x",
        "İİİİ mañana a las 22 x",
        "a las ١٢ algo",  # cifras árabes: no son una hora
    ],
)
def test_hostile_input_only_raises_parse_error(text):
    try:
        parse_reminder(text, NOW)
    except ReminderParseError:
        pass  # rechazarlo con un mensaje claro es correcto


def test_unicode_uppercase_keeps_text_aligned():
    result = parse_reminder("İİ mañana a las 22 x", NOW)
    assert result.due == datetime(2026, 11, 22, 22, 0)
    assert result.text == "İİ x"


def test_seconds_are_dropped():
    assert parse_reminder("en 10 minutos estirar", NOW).due.second == 0

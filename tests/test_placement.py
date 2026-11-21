import pytest

from widget_reloj_clima import placement
from widget_reloj_clima.placement import CORNERS, Rect, corner_position, is_visible

AREA = Rect(0, 0, 1920, 1040)  # 1920x1080 con barra de tareas de 40 px


@pytest.mark.parametrize(
    ("corner", "expected"),
    [
        ("top_left", (24, 24)),
        ("top_right", (1920 - 400 - 24, 24)),
        ("bottom_left", (24, 1040 - 700 - 24)),
        ("bottom_right", (1920 - 400 - 24, 1040 - 700 - 24)),
    ],
)
def test_corner_position(corner, expected):
    assert corner_position(corner, AREA, 400, 700, 24) == expected


def test_corner_position_respects_offset_work_area():
    area = Rect(-1920, 100, 1920, 1000)  # monitor a la izquierda, barra de tareas arriba
    assert corner_position("top_left", area, 300, 500, 10) == (-1910, 110)


def test_corner_position_never_goes_above_or_left_of_area():
    assert corner_position("bottom_right", Rect(0, 0, 300, 400), 400, 700, 24) == (0, 0)


def test_corner_position_rejects_unknown_corner():
    with pytest.raises(ValueError):
        corner_position("centro", AREA, 10, 10, 0)


def test_all_corners_have_spanish_labels():
    assert set(CORNERS) == {"top_left", "top_right", "bottom_left", "bottom_right"}
    assert all(label.startswith(("Arriba", "Abajo")) for label in CORNERS.values())


def test_is_visible():
    screen = Rect(0, 0, 1920, 1080)
    assert is_visible(100, 100, screen, 60)
    assert not is_visible(-500, 100, screen, 60)
    assert not is_visible(1900, 100, screen, 60)


def test_screen_helpers_return_fallback_off_windows(monkeypatch):
    fallback = Rect(0, 0, 800, 600)
    monkeypatch.setattr(placement.sys, "platform", "linux")
    assert placement.virtual_screen(fallback) == fallback
    assert placement.work_area(fallback) == fallback


@pytest.mark.skipif(placement.sys.platform != "win32", reason="solo Windows")
def test_screen_helpers_on_windows():
    fallback = Rect(0, 0, 1, 1)
    area = placement.work_area(fallback)
    screen = placement.virtual_screen(fallback)
    assert area.width > 1 and area.height > 1
    assert screen.width >= area.width

# tests/test_qr_tags.py
"""Тесты QR-бирок (v1.6.4): код бирки, QR-картинка, генерация PDF-A4."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QGuiApplication  # noqa: E402

from inventory.core.models import Device, Location  # noqa: E402
from inventory.services.qr_tags import (  # noqa: E402
    generate_tags_pdf,
    qr_image,
    tag_code,
)

_APP = None


def _app():
    """QGuiApplication один на сессию тестов (нужен QImage/QPdfWriter)."""
    global _APP
    if QGuiApplication.instance() is None:
        _APP = QGuiApplication([])
    return QGuiApplication.instance()


def _device(**overrides) -> Device:
    defaults = dict(
        id="dev-1", session_id="s1", display_id="CAM-001",
        device_type="camera", location_id="l1", status="active",
        created_at="2026-11-12", updated_at="2026-11-12",
        manufacturer="Hikvision", model="DS-2CD2143G0",
        serial="ABC12345", mac="AA:BB:CC:DD:EE:FF",
    )
    defaults.update(overrides)
    return Device(**defaults)


_LOCATION = Location(
    id="l1", session_id="s1", building="Корпус А", floor="2",
    room="204", rack="Шкаф 2", unit="3",
)


# ---------------------------------------------------------------- tag_code
def test_tag_code_full() -> None:
    assert tag_code(_device()) == "SN=ABC12345;MAC=AA:BB:CC:DD:EE:FF"


def test_tag_code_serial_only() -> None:
    assert tag_code(_device(mac=None)) == "SN=ABC12345"


def test_tag_code_mac_only() -> None:
    assert tag_code(_device(serial=None)) == "MAC=AA:BB:CC:DD:EE:FF"


def test_tag_code_nothing_to_tag() -> None:
    assert tag_code(_device(serial=None, mac=None)) is None
    assert tag_code(_device(serial="", mac="")) is None


# ---------------------------------------------------------------- qr_image
def test_qr_image_round_and_polarity() -> None:
    _app()
    img = qr_image("SN=ABC;MAC=AA:BB:CC", size_px=200)
    assert (img.width(), img.height()) == (200, 200)
    assert img.format() == img.Format.Format_Grayscale8


def test_tag_code_roundtrip_through_parser() -> None:
    """Смысл фичи: родной парсер приложения обязан понять содержимое бирки."""
    from inventory.core.parser import parse_code  # noqa: PLC0415

    parsed = parse_code(tag_code(_device()))
    assert parsed.code_type.value == "qr"
    assert parsed.serial == "ABC12345"
    assert parsed.mac == "AA:BB:CC:DD:EE:FF"


# --------------------------------------------------------------- PDF-A4
def test_generate_tags_pdf(tmp_path: Path) -> None:
    _app()
    devices = [
        _device(),                                    # полная пара
        _device(id="dev-2", display_id="SW-001",
                device_type="switch", serial="FDO2134X0ZZ",
                mac=None, location=_LOCATION),
        _device(id="dev-3", display_id="UPS-001",
                device_type="ups", serial=None, mac=None),  # пропущен
    ]
    out = tmp_path / "birki.pdf"
    printed, skipped = generate_tags_pdf(devices, out)
    assert (printed, skipped) == (2, 1)
    assert out.exists()
    assert out.stat().st_size > 2000  # заголовок + два QR + текст
    head = out.read_bytes()[:8]
    assert head.startswith(b"%PDF")


def test_generate_tags_pdf_nothing_printable(tmp_path: Path) -> None:
    _app()
    out = tmp_path / "empty.pdf"
    printed, skipped = generate_tags_pdf(
        [_device(serial=None, mac=None)], out)
    assert (printed, skipped) == (0, 1)
    assert not out.exists()  # файл не создаётся ради пустого листа


def test_generate_tags_pdf_pagination(tmp_path: Path) -> None:
    """25 бирок = 2 страницы A4 (24 на лист)."""
    _app()
    devices = [
        _device(id=f"dev-{i}", display_id=f"DEV-{i:03d}",
                serial=f"S{i:05d}", mac=f"AA:BB:CC:DD:{i // 256:02X}:{i % 256:02X}",
                manufacturer="M", model="M1")
        for i in range(25)
    ]
    out = tmp_path / "many.pdf"
    printed, skipped = generate_tags_pdf(devices, out)
    assert (printed, skipped) == (25, 0)
    data = out.read_bytes()
    assert data.count(b"/Type /Page") >= 2 or data.count(b"/Type/Page") >= 2

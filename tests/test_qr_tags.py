# tests/test_qr_tags.py
"""Тесты QR-бирок: код бирки, QR-картинка, скан бирки по фото, PDF-A4, реестр ИНВ."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from inventory.core.db import Database  # noqa: E402
from inventory.core.duplicates import check_duplicates  # noqa: E402
from inventory.core.models import Device, Location  # noqa: E402
from inventory.core.parser import parse_code  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.services.qr_tags import (  # noqa: E402
    assign_tag_ids,
    detect_qr_text,
    generate_tags_pdf,
    qr_image,
    render_label_png,
    tag_code,
)

_APP = None


def _app():
    """QApplication один на сессию тестов (QImage, QPdfWriter и виджеты)."""
    global _APP
    if QApplication.instance() is None:
        _APP = QApplication([])
    return QApplication.instance()


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


def test_tag_code_inv_fallback() -> None:
    """Безымянное устройство с присвоенным ИНВ кодируется как «INV=…»."""
    assert tag_code(_device(serial=None, mac=None, asset_tag="INV-0007")) \
        == "INV=INV-0007"
    # S/N важнее: при нём ИНВ в QR не нужен
    assert tag_code(_device(asset_tag="INV-0007")) \
        == "SN=ABC12345;MAC=AA:BB:CC:DD:EE:FF"


# ---------------------------------------------------------------- qr_image
def test_qr_image_round_and_polarity() -> None:
    _app()
    img = qr_image("SN=ABC;MAC=AA:BB:CC", size_px=200)
    assert (img.width(), img.height()) == (200, 200)
    assert img.format() == img.Format.Format_Grayscale8


def test_tag_code_roundtrip_through_parser() -> None:
    """Смысл фичи: родной парсер приложения обязан понять содержимое бирки."""
    parsed = parse_code(tag_code(_device()))
    assert parsed.code_type.value == "qr"
    assert parsed.serial == "ABC12345"
    assert parsed.mac == "AA:BB:CC:DD:EE:FF"


def test_parse_inv_roundtrip_through_parser() -> None:
    """Бирка безымянного устройства: парсер понимает «INV=…» → asset_tag."""
    parsed = parse_code(tag_code(_device(serial=None, mac=None,
                                         asset_tag="INV-0007")))
    assert parsed.code_type.value == "qr"
    assert parsed.fields.get("asset_tag") == "INV-0007"


# ------------------------------------------------- скан бирки по фото
def test_detect_qr_reads_tag_file_roundtrip(tmp_path: Path) -> None:
    """Фича v1.6.7: QR с файла бирки → текст → родной парсер понимает."""
    _app()
    text = tag_code(_device())
    path = tmp_path / "бирка_CAM-001.png"  # кириллица в имени — осознанно
    assert qr_image(text, size_px=240).save(str(path))
    detected = detect_qr_text(path)
    assert detected == text
    parsed = parse_code(detected)
    assert parsed.code_type.value == "qr"
    assert parsed.serial == "ABC12345"


def test_detect_qr_small_frame_still_decodes(tmp_path: Path) -> None:
    """Мелкий/далёкий кадр: контракт — текст возвращается (помогает повтор ×2)."""
    _app()
    path = tmp_path / "мелкий.png"
    assert qr_image("INV=INV-0007", size_px=64).save(str(path))
    assert detect_qr_text(path) == "INV=INV-0007"


def test_detect_qr_no_qr_returns_none(tmp_path: Path) -> None:
    _app()
    import cv2
    import numpy as np

    path = tmp_path / "белый_лист.png"
    blank = np.full((200, 300), 255, dtype=np.uint8)
    cv2.imencode(".png", blank)[1].tofile(str(path))  # не imread: кириллица
    assert detect_qr_text(path) is None


def test_detect_qr_missing_file_and_garbage(tmp_path: Path) -> None:
    """Не читается / не картинка → None, никакого исключения наружу."""
    assert detect_qr_text(tmp_path / "нет_такого.png") is None
    junk = tmp_path / "мусор.png"
    junk.write_bytes(b"\x00\x01not an image")
    assert detect_qr_text(junk) is None


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


# ------------------------------------------------------------------ PNG
def test_render_label_png(tmp_path: Path) -> None:
    _app()
    out = render_label_png(_device(), tmp_path / "birka.png")
    assert out.exists()
    assert out.stat().st_size > 2000


# ------------------------------------------------- реестр ИНВ + дубли
def _repo(tmp_path: Path):
    db = Database(tmp_path / "qr.db")
    repo = InventoryRepository(db)
    session = repo.create_session("Объект", "Инспектор")
    location = repo.create_location(session.id, "1", "10", "", "")
    return repo, session, location


def test_assign_tag_ids_sequential_and_persisted(tmp_path: Path) -> None:
    """Безымянным — номера по порядку в БД; у именных ничего не трогаем."""
    repo, session, location = _repo(tmp_path)
    unlabeled1 = repo.create_device(session.id, "camera", location.id)
    unlabeled2 = repo.create_device(session.id, "camera", location.id)
    labeled = repo.create_device(session.id, "camera", location.id,
                                 serial="AAA111")
    assigned = assign_tag_ids([unlabeled1, unlabeled2, labeled], repo)
    assert assigned == 2
    assert repo.get_device(unlabeled1.id).asset_tag == "INV-0001"
    assert repo.get_device(unlabeled2.id).asset_tag == "INV-0002"
    assert repo.get_device(labeled.id).asset_tag is None
    # повторный вызов не плодит новые номера (уже есть asset_tag)
    assert assign_tag_ids([unlabeled1, unlabeled2, labeled], repo) == 0


def test_next_asset_tag_number_global_across_sessions(tmp_path: Path) -> None:
    """Реестр глобальный: max по ВСЕМ сессиям + 1."""
    repo, session, location = _repo(tmp_path)
    repo.create_device(session.id, "camera", location.id, asset_tag="INV-0005")
    repo.finish_session(session.id)  # вторая активная сессия запрещена
    other = repo.create_session("Объект 2", "Инспектор")
    loc2 = repo.create_location(other.id, "1", "10", "", "")
    repo.create_device(other.id, "camera", loc2.id, asset_tag="INV-0012")
    assert repo.next_asset_tag_number() == 13
    # чтение ничего не меняет
    assert repo.next_asset_tag_number() == 13


def test_check_duplicates_by_asset_tag(tmp_path: Path) -> None:
    """Скан бирки «INV=…» находит существующую карточку по инв. номеру."""
    from inventory.core.duplicates import check_duplicates  # noqa: PLC0415

    repo, session, location = _repo(tmp_path)
    device = repo.create_device(session.id, "camera", location.id,
                                asset_tag="INV-0001")
    repo.create_device(session.id, "camera", location.id, serial="AAA111")
    parsed = parse_code("INV=INV-0001")
    matches = check_duplicates(repo, session.id, parsed=parsed)
    assert len(matches) == 1
    assert matches[0].device.id == device.id
    assert matches[0].matched_by == ["asset_tag"]
    assert "Инв. номер" in matches[0].message()


def test_check_duplicates_inv_ignores_other_devices(tmp_path: Path) -> None:
    repo, session, location = _repo(tmp_path)
    repo.create_device(session.id, "camera", location.id, asset_tag="INV-0001")
    repo.create_device(session.id, "camera", location.id, asset_tag="INV-0002")
    parsed = parse_code("INV=INV-0001")
    matches = check_duplicates(repo, session.id, parsed=parsed)
    assert len(matches) == 1

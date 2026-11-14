# tests/test_report_pdf.py
"""Тесты печатных документов: акт инвентаризации и чек-лист докрутки."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from inventory.core.db import Database  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.services.report_pdf import (  # noqa: E402
    followup_items,
    generate_act_pdf,
    generate_followup_pdf,
)


def _repo(tmp_path: Path):
    db = Database(tmp_path / "act.db")
    repo = InventoryRepository(db)
    session = repo.create_session("Объект", "Инспектор")
    repo.set_setting("report_org", "ООО «Сеть»")
    repo.set_setting("report_contract", "Договор 7-ИИ")
    return repo, session


def _pdf_sane(path: Path) -> None:
    assert path.exists()
    data = path.read_bytes()
    assert data.startswith(b"%PDF")
    assert len(data) > 2500  # не пустой лист: шапка + таблица


def test_generate_act_pdf(tmp_path: Path) -> None:
    repo, session = _repo(tmp_path)
    location = repo.create_location(session.id, "2", "204", "Корпус А", "Ш2")
    for i in range(1, 61):  # 60 позиций → перенос страниц (по 24-30 строк)
        repo.create_device(
            session.id, "switch" if i % 2 else "camera", location.id,
            serial=f"SN{i:05d}", mac=f"AA:BB:CC:DD:EE:{i:02X}",
            model=f"Модель-{i}", manufacturer="D-Link",
        )
    devices = repo.list_devices(session.id)
    out = generate_act_pdf(repo.get_session(session.id), devices,
                           tmp_path / "act.pdf", repo=repo)
    _pdf_sane(out)
    data = out.read_bytes()
    assert data.count(b"/Type /Page") >= 3 or data.count(b"/Type/Page") >= 3


def test_generate_act_pdf_empty_session(tmp_path: Path) -> None:
    repo, session = _repo(tmp_path)
    out = generate_act_pdf(repo.get_session(session.id), [],
                           tmp_path / "empty.pdf", repo=repo)
    _pdf_sane(out)  # документ с шапкой и «Всего позиций: 0»


def test_followup_items_and_checklist_pdf(tmp_path: Path) -> None:
    repo, session = _repo(tmp_path)
    location = repo.create_location(session.id, "1", "10")
    complete = repo.create_device(
        session.id, "switch", location.id, serial="SN1", mac="AA:BB:CC:DD:EE:01",
        is_verified=True)
    photo = tmp_path / "p0.jpg"
    photo.write_bytes(b"\xff\xd8\xffstub")
    repo.add_photo(complete.id, str(photo))
    repo.update_device(complete.id, photo_path=str(photo))
    rough = repo.create_device(session.id, "ups", location.id)  # без всего
    camera = repo.create_device(
        session.id, "camera", location.id, serial="SN2", is_verified=True)
    # камера без связи тоже в очередь
    devices = repo.list_devices(session.id)
    items = dict((d.id, todo) for d, todo in followup_items(devices))
    assert complete.id not in items  # полный — не в чек-листе
    assert "S/N" in items[rough.id] and "MAC" in items[rough.id]
    assert "связь" in items[camera.id]
    out = generate_followup_pdf(
        repo.get_session(session.id), devices, tmp_path / "check.pdf")
    _pdf_sane(out)


def test_followup_photo_missing(tmp_path: Path) -> None:
    repo, session = _repo(tmp_path)
    location = repo.create_location(session.id, "1", "10")
    device = repo.create_device(
        session.id, "switch", location.id, serial="SN1", mac="AA:BB:CC:DD:EE:01",
        is_verified=True)
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"\xff\xd8\xffstub")
    repo.add_photo(device.id, str(photo))
    repo.update_device(device.id, photo_path=str(photo))
    devices = repo.list_devices(session.id)
    assert followup_items(devices) == []  # всё добрано — очереди нет

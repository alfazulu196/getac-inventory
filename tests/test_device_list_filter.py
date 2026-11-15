# tests/test_device_list_filter.py
"""FR-63 (v1.6.9): фильтр по локации в списке устройств."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from inventory.core.db import Database  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.ui.device_list import DeviceListDialog  # noqa: E402

_APP: QApplication | None = None


def _qapp() -> QApplication:
    global _APP
    if QApplication.instance() is None:
        _APP = QApplication([])
    return QApplication.instance()


def _repo(tmp_path: Path) -> tuple[InventoryRepository, str, str, str]:
    """Сессия с тремя локациями; CAM в первой, SW/UPS во второй."""
    repo = InventoryRepository(Database(tmp_path / "filter.db"))
    session = repo.create_session("Объект", "Инспектор")
    loc1 = repo.create_location(session.id, "2", "204", "Корпус А", "Ш1")
    loc2 = repo.create_location(session.id, "2", "205", "Корпус А", "Ш2")
    repo.create_location(session.id, "3", "210", "Корпус Б", "")
    repo.create_device(
        session.id, "camera", loc1.id, model="DS-2CD2143G0", ip="192.168.4.1")
    repo.create_device(session.id, "switch", loc2.id, model="DES-1210")
    repo.create_device(session.id, "ups", loc2.id, model="AP7921")
    return repo, session.id, loc1.id, loc2.id


def _dialog(tmp_path: Path) -> tuple[DeviceListDialog, str, str, str]:
    _qapp()  # QDialog без QApplication убивает процесс молча
    repo, session_id, loc1, loc2 = _repo(tmp_path)
    dialog = DeviceListDialog(
        repo, session_id,
        {"photos": tmp_path / "photos", "logs": tmp_path / "logs"},
    )
    return dialog, session_id, loc1, loc2


def test_location_combo_lists_all_locations(tmp_path: Path) -> None:
    dialog, _s, _l1, _l2 = _dialog(tmp_path)
    assert dialog.location_combo.count() == 4  # «Все локации» + 3 локации
    assert dialog.location_combo.currentData() is None


def test_location_filter_narrows_devices(tmp_path: Path) -> None:
    dialog, _s, _l1, _l2 = _dialog(tmp_path)
    assert len(dialog._devices) == 3  # без фильтра видны все
    assert "Показано: 3" in dialog.count_label.text()

    dialog.location_combo.setCurrentIndex(1)  # первая локация (Корпус А, Ш1)
    assert dialog.location_combo.currentData() is not None
    assert len(dialog._devices) == 1
    assert dialog._devices[0].device_type == "camera"
    assert "Показано: 1" in dialog.count_label.text()

    dialog.location_combo.setCurrentIndex(2)  # вторая локация (Ш2)
    assert len(dialog._devices) == 2
    assert {d.device_type for d in dialog._devices} == {"switch", "ups"}

    dialog.location_combo.setCurrentIndex(0)  # снова все
    assert len(dialog._devices) == 3


def test_location_filter_combines_with_type(tmp_path: Path) -> None:
    dialog, _s, _l1, _l2 = _dialog(tmp_path)
    dialog.location_combo.setCurrentIndex(2)  # Ш2: switch + ups
    type_index = dialog.type_combo.findData("ups")
    dialog.type_combo.setCurrentIndex(type_index)
    assert len(dialog._devices) == 1
    assert dialog._devices[0].model == "AP7921"

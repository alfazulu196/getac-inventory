# tests/test_rack_merge.py
"""Слияние мульти-кадров стойки: дубли по нормализованной позиции/названию."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

import inventory.ui.rack_dialog as rack_dialog  # noqa: E402
from inventory.core.db import Database  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.services.rack_ai import (  # noqa: E402
    RackAnalysis,
    RackDevice,
    normalize_position,
    normalize_title,
)
from inventory.ui.rack_dialog import RackDialog  # noqa: E402

_APP: QApplication | None = None


def _qapp() -> QApplication:
    global _APP
    if QApplication.instance() is None:
        _APP = QApplication([])
    return QApplication.instance()


def _dialog(tmp_path: Path, monkeypatch) -> RackDialog:
    _qapp()
    repo = InventoryRepository(Database(tmp_path / "merge.db"))
    session = repo.create_session("Объект", "Инспектор")
    location = repo.create_location(session.id, "2", "204", "Корпус А", "Ш2")
    monkeypatch.setattr(rack_dialog, "ollama_status", lambda: (False, "нет ИИ"))
    monkeypatch.setattr(
        rack_dialog.QMessageBox, "information", lambda *a, **k: None)
    return RackDialog(
        repo, session.id,
        {"photos": tmp_path / "photos", "logs": tmp_path / "logs"},
        location=location,
    )


def _analysis(*devices: RackDevice) -> RackAnalysis:
    return RackAnalysis(devices=list(devices), model_used="test", ok=True)


def _text(dialog: RackDialog, row: int, col: int) -> str:
    item = dialog.table.item(row, col)
    return item.text().strip() if item else ""


# ------------------------------------------------------- нормализаторы
def test_normalize_position_variants() -> None:
    assert normalize_position("U5") == "U5"
    assert normalize_position("5") == "U5"
    assert normalize_position("u 5") == "U5"
    assert normalize_position("U05") == "U5"
    assert normalize_position("U5-U8") == "U5-U8"
    assert normalize_position("5-8") == "U5-U8"
    assert normalize_position("5–U8") == "U5-U8"   # типографское тире
    assert normalize_position("U8-U5") == "U5-U8"  # модель перепутала границы
    assert normalize_position("юнит 5") == "юнит 5"
    assert normalize_position("") == ""


def test_normalize_title_variants() -> None:
    assert normalize_title("D-Link  DES-1210") == normalize_title("d-link des-1210")
    assert normalize_title("APC – Smart-UPS") == normalize_title("APC - Smart-UPS")
    assert normalize_title("Ёмкость") == "емкость"


# ------------------------------------------------------------- слияние
def test_same_position_different_titles_merge(tmp_path, monkeypatch) -> None:
    """Кадры 1 и 2 видят один юнит с разными названиями → ОДНА строка."""
    dialog = _dialog(tmp_path, monkeypatch)
    dialog._append_row(RackDevice(
        type_code="switch", brand="D-Link", model="DES-1210",
        position="U5", detail="панель портов"))
    dialog._on_analysis(_analysis(RackDevice(
        type_code="switch", brand="D-Link", model="DES-1210-10/ME",
        position="U5", detail="")), "кадр2.jpg")
    assert dialog.table.rowCount() == 1
    # Первое название сохранено, пустые поля второго кадра ничего не затёрли.
    assert _text(dialog, 0, rack_dialog._COL_NAME) == "D-Link DES-1210"
    assert _text(dialog, 0, rack_dialog._COL_NOTE) == "панель портов"
    assert "объединено" in dialog.status_label.text()


def test_position_written_differently_still_merges(tmp_path, monkeypatch) -> None:
    """«U5» и «5» — один юнит: модель пишет позицию по-разному на кадрах."""
    dialog = _dialog(tmp_path, monkeypatch)
    dialog._append_row(RackDevice(
        type_code="ups", brand="APC", model="AP7921", position="U5", detail=""))
    dialog._on_analysis(_analysis(RackDevice(
        type_code="ups", brand="APC", model="AP7921",
        position="5", detail="индикаторы")), "кадр2.jpg")
    assert dialog.table.rowCount() == 1
    assert _text(dialog, 0, rack_dialog._COL_NOTE) == "индикаторы"


def test_different_positions_are_separate_units(tmp_path, monkeypatch) -> None:
    """U5 и U6 одной модели — законные соседи, их нельзя сливать."""
    dialog = _dialog(tmp_path, monkeypatch)
    dialog._append_row(RackDevice(
        type_code="switch", brand="D-Link", model="DES-1210", position="U5"))
    dialog._on_analysis(_analysis(RackDevice(
        type_code="switch", brand="D-Link", model="DES-1210",
        position="U6")), "кадр2.jpg")
    assert dialog.table.rowCount() == 2


def test_title_merge_without_positions_fills_detail(tmp_path, monkeypatch) -> None:
    dialog = _dialog(tmp_path, monkeypatch)
    dialog._append_row(RackDevice(
        type_code="ups", brand="APC", model="Smart-UPS", position="", detail=""))
    dialog._on_analysis(_analysis(RackDevice(
        type_code="ups", brand="APC", model="Smart-UPS",
        position="", detail="ИБП")), "кадр2.jpg")
    assert dialog.table.rowCount() == 1
    assert _text(dialog, 0, rack_dialog._COL_NOTE) == "ИБП"
    assert _text(dialog, 0, rack_dialog._COL_POS) == ""  # позицию не выдумываем


def test_title_match_fills_missing_position(tmp_path, monkeypatch) -> None:
    dialog = _dialog(tmp_path, monkeypatch)
    dialog._append_row(RackDevice(
        type_code="ups", brand="APC", model="AP7921", position="", detail=""))
    dialog._on_analysis(_analysis(RackDevice(
        type_code="ups", brand="APC", model="AP7921",
        position="U5", detail="")), "кадр2.jpg")
    assert dialog.table.rowCount() == 1
    assert _text(dialog, 0, rack_dialog._COL_POS) == "U5"

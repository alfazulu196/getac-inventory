# tests/test_rack_context_merge.py
"""v1.6.12: ручное объединение дубликатов и удаление строки (контекстное меню)."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

import inventory.ui.rack_dialog as rack_mod  # noqa: E402
from inventory.core.db import Database  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.services.rack_ai import RackDevice  # noqa: E402
from inventory.ui.rack_dialog import DRAFT_KEY, RackDialog, _COL_NAME  # noqa: E402

_APP: QApplication | None = None


def _qapp() -> QApplication:
    global _APP
    if QApplication.instance() is None:
        _APP = QApplication([])
    return QApplication.instance()


def _dialog(tmp_path: Path, monkeypatch) -> RackDialog:
    _qapp()
    rack_mod.RackDialog._start_warmup = lambda self: None
    rack_mod.RackDialog._another_shot = lambda self: None
    # Без ollama (CI) _begin() показывает МОДАЛЬНЫЙ information — в offscreen
    # он вешает прогон навсегда (грабли test_rack_duplicates). Патчим оба:
    monkeypatch.setattr(rack_mod, "ollama_status", lambda: (False, "нет ИИ"))
    monkeypatch.setattr(
        rack_mod.QMessageBox, "information", lambda *a, **k: None)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    repo = InventoryRepository(Database(tmp_path / "rack.db"))
    session = repo.create_session("Объект", "Инспектор")
    dialog = rack_mod.RackDialog(
        repo, session.id, {"photos": work, "logs": work / "logs"})
    dialog.show()
    QApplication.instance().processEvents()
    return dialog


def _answer(monkeypatch, yes: bool) -> None:
    answer = (
        rack_mod.QMessageBox.StandardButton.Yes if yes
        else rack_mod.QMessageBox.StandardButton.No
    )
    monkeypatch.setattr(
        rack_mod.QMessageBox, "question", lambda *a, **k: answer)


def _rows(dialog: RackDialog) -> list[dict]:
    return dialog._rows_snapshot()


# ------------------------------------------------------------- подписи строк
def test_row_label(tmp_path: Path, monkeypatch) -> None:
    dialog = _dialog(tmp_path, monkeypatch)
    dialog._append_row(RackDevice(
        type_code="switch", brand="TP-LINK", model="TL-SG1016", position="U7"))
    dialog._append_row(None)
    assert dialog._row_label(0) == "№1 «TP-LINK TL-SG1016» (U7)"
    assert dialog._row_label(1) == "№2 (пустая строка)"
    dialog.reject()


# --------------------------------------------------------------------- меню
def test_menu_lists_duplicates_first(tmp_path: Path, monkeypatch) -> None:
    """В подменю слияния дубли идут первыми и помечены «≈ дубль»."""
    dialog = _dialog(tmp_path, monkeypatch)
    dialog._append_row(RackDevice(
        type_code="switch", brand="TP-LINK", model="TL-SG1016", position="U7"))
    dialog._append_row(RackDevice(
        type_code="switch", brand="Cisco", model="WS-C2960", position="U10"))
    dialog._append_row(RackDevice(
        type_code="switch", brand="TP-LINK", model="TL-SG1016", position="U8"))

    menu = dialog._build_row_menu(0)  # клик по TP-LINK U7
    merge_menu = menu.actions()[0].menu()
    labels = [a.text() for a in merge_menu.actions() if a.text()]
    assert labels[0].startswith("≈ дубль") and "U8" in labels[0]
    assert any("Cisco" in label for label in labels)

    menu_cisco = dialog._build_row_menu(1)  # клик по Cisco: дублей нет
    merge_menu_cisco = menu_cisco.actions()[0].menu()
    labels_cisco = [a.text() for a in merge_menu_cisco.actions() if a.text()]
    assert all(not label.startswith("≈") for label in labels_cisco)
    assert len(labels_cisco) == 2
    dialog.reject()


def test_menu_merge_disabled_for_single_row(tmp_path: Path, monkeypatch) -> None:
    dialog = _dialog(tmp_path, monkeypatch)
    dialog._append_row(RackDevice(
        type_code="switch", brand="Cisco", model="WS-C2960", position="U10"))
    menu = dialog._build_row_menu(0)
    merge_menu = menu.actions()[0].menu()
    assert not merge_menu.isEnabled()
    dialog.reject()


# ------------------------------------------------------------------ слияние
def test_merge_fills_empty_fields_and_removes_source(tmp_path: Path, monkeypatch) -> None:
    """Правый клик по ЛИШНЕЙ строке: её данные дополняют приёмник."""
    dialog = _dialog(tmp_path, monkeypatch)
    dialog._append_row(RackDevice(
        type_code="switch", brand="TP-LINK", model="TL-SG1016",
        position="U7", detail=""))
    dialog._append_row(RackDevice(
        type_code="switch", brand="TP-LINK", model="TL-SG1016",
        position="U8", detail="видно порты"))
    dialog._merge_rows(1, 0)  # строку 2 объединяем в строку 1

    assert dialog.table.rowCount() == 1
    rd = _rows(dialog)[0]
    assert rd["pos"] == "U7"      # заполненный юнит приёмника сохранён
    assert rd["note"] == "видно порты"  # пустое поле взяли из источника
    assert rd["name"] == "TP-LINK TL-SG1016"
    assert "объединены" in dialog.status_label.text()
    dialog.reject()


def test_merge_keeps_filled_target_values(tmp_path: Path, monkeypatch) -> None:
    dialog = _dialog(tmp_path, monkeypatch)
    dialog._append_row(RackDevice(
        type_code="switch", brand="TP-LINK", model="TL-SG1016",
        position="U7", detail="видно вентиляторы"))
    dialog._append_row(RackDevice(
        type_code="switch", brand="TP-LINK", model="TL-SG1016",
        position="U8", detail="видно порты"))
    dialog._merge_rows(1, 0)

    rd = _rows(dialog)[0]
    assert rd["note"] == "видно вентиляторы"  # приёмник не перетёрли
    assert rd["pos"] == "U7"
    dialog.reject()


def test_merge_takes_type_from_source_when_target_other(tmp_path: Path, monkeypatch) -> None:
    """Ручная строка («other») + распознанный дубль → тип от распознанной."""
    dialog = _dialog(tmp_path, monkeypatch)
    dialog._append_row(RackDevice(
        type_code="psu", brand="APC", model="AP7921", position="U3"))
    dialog._append_row(None)  # ручная строка: other, без имени
    dialog._merge_rows(0, 1)  # распознанную объединили в ручную

    rd = _rows(dialog)[0]
    assert rd["type"] == "psu"
    assert rd["name"] == "APC AP7921"
    assert rd["pos"] == "U3"
    assert dialog.table.rowCount() == 1
    dialog.reject()


def test_merge_updates_draft_and_duplicate_marks(tmp_path: Path, monkeypatch) -> None:
    dialog = _dialog(tmp_path, monkeypatch)
    dialog._append_row(RackDevice(
        type_code="psu", brand="APC", model="AP7921", position="U5"))
    dialog._append_row(RackDevice(
        type_code="psu", brand="APC", model="AP7921", position="U3"))
    dialog._merge_rows(1, 0)

    marked = [
        r for r in range(dialog.table.rowCount())
        if "дубл" in (dialog.table.item(r, _COL_NAME).toolTip() or "").lower()
    ]
    assert not marked  # после слияния дубликатов нет
    draft = dialog.repo.get_setting(DRAFT_KEY)
    assert draft is not None and len(draft["rows"]) == 1
    dialog.reject()


def test_ask_merge_confirm_yes(tmp_path: Path, monkeypatch) -> None:
    dialog = _dialog(tmp_path, monkeypatch)
    dialog._append_row(RackDevice(
        type_code="psu", brand="APC", model="AP7921", position="U5"))
    dialog._append_row(RackDevice(
        type_code="psu", brand="APC", model="AP7921", position="U3"))
    _answer(monkeypatch, yes=True)
    dialog._ask_merge(1, 0)
    assert dialog.table.rowCount() == 1
    dialog.reject()


def test_ask_merge_confirm_no(tmp_path: Path, monkeypatch) -> None:
    dialog = _dialog(tmp_path, monkeypatch)
    dialog._append_row(RackDevice(
        type_code="psu", brand="APC", model="AP7921", position="U5"))
    dialog._append_row(RackDevice(
        type_code="psu", brand="APC", model="AP7921", position="U3"))
    _answer(monkeypatch, yes=False)
    dialog._ask_merge(1, 0)
    assert dialog.table.rowCount() == 2
    dialog.reject()


# ---------------------------------------------------------------- удаление
def test_delete_row_keeps_rest(tmp_path: Path, monkeypatch) -> None:
    dialog = _dialog(tmp_path, monkeypatch)
    dialog._append_row(RackDevice(
        type_code="switch", brand="Cisco", model="WS-C2960", position="U10"))
    dialog._append_row(RackDevice(
        type_code="patch", brand="", model="", position="U9",
        detail="Patch Panel 24"))
    _answer(monkeypatch, yes=True)
    dialog._ask_delete_row(0)

    assert dialog.table.rowCount() == 1
    rd = _rows(dialog)[0]
    assert rd["pos"] == "U9" and rd["note"] == "Patch Panel 24"
    draft = dialog.repo.get_setting(DRAFT_KEY)
    assert draft is not None and len(draft["rows"]) == 1
    dialog.reject()


def test_delete_last_row_clears_draft(tmp_path: Path, monkeypatch) -> None:
    dialog = _dialog(tmp_path, monkeypatch)
    dialog._append_row(RackDevice(
        type_code="switch", brand="Cisco", model="WS-C2960", position="U10"))
    _answer(monkeypatch, yes=True)
    dialog._ask_delete_row(0)

    assert dialog.table.rowCount() == 0
    assert not dialog.create_btn.isEnabled()
    draft = dialog.repo.get_setting(DRAFT_KEY)
    assert not draft  # черновик пустой таблицы не resurrectится
    dialog.reject()


def test_delete_row_confirm_no(tmp_path: Path, monkeypatch) -> None:
    dialog = _dialog(tmp_path, monkeypatch)
    dialog._append_row(RackDevice(
        type_code="switch", brand="Cisco", model="WS-C2960", position="U10"))
    _answer(monkeypatch, yes=False)
    dialog._ask_delete_row(0)
    assert dialog.table.rowCount() == 1
    dialog.reject()

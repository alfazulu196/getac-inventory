# tests/test_rack_duplicates.py
"""v1.6.11: подсветка возможных дублей в таблице стойки."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from inventory.core.db import Database  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.services.rack_ai import RackAnalysis, RackDevice  # noqa: E402
import inventory.ui.rack_dialog as rack_mod  # noqa: E402

_APP: QApplication | None = None


def _qapp() -> QApplication:
    global _APP
    if QApplication.instance() is None:
        _APP = QApplication([])
    return QApplication.instance()


def _dialog(tmp_path: Path) -> rack_mod.RackDialog:
    """Диалог без камеры/warmup — строки кормим напрямую (_on_analysis)."""
    _qapp()
    rack_mod.RackDialog._start_warmup = lambda self: None
    rack_mod.RackDialog._another_shot = lambda self: None
    rack_mod.QMessageBox.information = lambda *a, **k: None
    rack_mod.QMessageBox.question = (
        lambda *a, **k: rack_mod.QMessageBox.StandardButton.No)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    repo = InventoryRepository(Database(tmp_path / "rack.db"))
    session = repo.create_session("Объект", "Инспектор")
    dialog = rack_mod.RackDialog(
        repo, session.id, {"photos": work, "logs": work / "logs"})
    dialog.show()
    QApplication.instance().processEvents()
    return dialog


def _feed(dialog: rack_mod.RackDialog, devices: list[RackDevice]) -> None:
    analysis = RackAnalysis(devices=devices, model_used="bench", ok=True,
                            elapsed_s=1.0)
    dialog._on_analysis(analysis, "frame.jpg")
    QApplication.instance().processEvents()


def _row_titles(dialog: rack_mod.RackDialog) -> list[str]:
    out = []
    for row in range(dialog.table.rowCount()):
        item = dialog.table.item(row, rack_mod._COL_NAME)
        out.append(item.text() if item else "")
    return out


def _marked_rows(dialog: rack_mod.RackDialog) -> set[int]:
    """Строки с дубликатной подсветкой (tooltip на имени)."""
    marked = set()
    for row in range(dialog.table.rowCount()):
        item = dialog.table.item(row, rack_mod._COL_NAME)
        if item and "дубл" in (item.toolTip() or "").lower():
            marked.add(row)
    return marked


def test_duplicates_adjacent_and_far_are_marked(tmp_path: Path) -> None:
    """Сценарий bench: TP-LINK на U7+U8 (соседние), AP7921 на U5+U3 (далеко)."""
    dialog = _dialog(tmp_path)
    _feed(dialog, [
        RackDevice("switch", "Cisco", "WS-C2960+24TC-L", "U10", ""),
        RackDevice("patch", "", "", "U9", "Patch Panel 24"),
        RackDevice("switch", "TP-LINK", "TL-SG1016", "U7", ""),
        RackDevice("switch", "TP-LINK", "TL-SG1016", "U8", ""),
        RackDevice("psu", "APC", "AP7921", "U5", ""),
        RackDevice("psu", "APC", "AP7921", "U3", ""),
    ])
    titles = _row_titles(dialog)
    # Строки упорядочены по позиции: U10, U9, U8, U7, U5, U3.
    assert titles.count("TP-LINK TL-SG1016") == 2
    marked = _marked_rows(dialog)
    assert len(marked) == 4  # обе TP-LINK + обе AP7921
    # Cisco и Patch Panel чисты.
    cisco_row = next(r for r, t in enumerate(titles) if "Cisco" in t)
    assert cisco_row not in marked
    # Сводка называет виновников.
    text = dialog.status_label.text()
    assert "TP-LINK TL-SG1016" in text and "AP7921" in text
    assert "дубл" in text.lower()
    dialog.reject()


def test_unique_titles_not_marked(tmp_path: Path) -> None:
    dialog = _dialog(tmp_path)
    _feed(dialog, [
        RackDevice("switch", "Cisco", "WS-C2960+24TC-L", "U10", ""),
        RackDevice("patch", "", "", "U9", "Patch Panel 24"),
        RackDevice("switch", "TP-LINK", "TL-SG1016", "U7", ""),
        RackDevice("dvr", "HIKVISION", "DS-7608NI-K2", "U6", ""),
    ])
    assert not _marked_rows(dialog)
    assert "дубл" not in dialog.status_label.text().lower()
    dialog.reject()


def test_marks_cleared_after_manual_fix(tmp_path: Path) -> None:
    """Ручная правка строки (имя/позиция) пересчитывает подсветку."""
    dialog = _dialog(tmp_path)
    _feed(dialog, [
        RackDevice("switch", "TP-LINK", "TL-SG1016", "U7", ""),
        RackDevice("switch", "TP-LINK", "TL-SG1016", "U8", ""),
    ])
    assert len(_marked_rows(dialog)) == 2

    # Человек правит вторую строку: это патч-панель, не второй TP-LINK.
    item = dialog.table.item(1, rack_mod._COL_NAME)
    item.setText("Patch Panel 24")
    dialog._mark_duplicates()  # как после срабатывания debounce черновика

    assert not _marked_rows(dialog)
    assert "дубл" not in dialog.status_label.text().lower()
    dialog.reject()


def test_empty_and_blank_rows_ignored(tmp_path: Path) -> None:
    """Пустые строки и «Юнит без описания» не считаются дублями друг друга."""
    dialog = _dialog(tmp_path)
    _feed(dialog, [
        RackDevice("other", "", "", "", ""),
        RackDevice("other", "", "", "", ""),
    ])
    assert not _marked_rows(dialog)
    dialog.reject()

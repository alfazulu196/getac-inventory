# tests/test_rack_draft.py
"""Черновик стойки: автосохранение при закрытии, восстановление, очистка."""
from __future__ import annotations

import os
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QMessageBox,
)

import inventory.ui.rack_dialog as rack_dialog  # noqa: E402
from inventory.core.db import Database  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.ui.rack_dialog import (  # noqa: E402
    DRAFT_KEY,
    RackDialog,
)

_APP: QApplication | None = None


def _qapp() -> QApplication:
    global _APP
    app = QApplication.instance()
    if app is None:
        _APP = QApplication([])
    return QApplication.instance()


def _repo_and_session(tmp_path: Path):
    repo = InventoryRepository(Database(tmp_path / "draft.db"))
    session = repo.create_session("Объект", "Инспектор")
    location = repo.create_location(session.id, "2", "204", "Корпус А", "Ш2")
    return repo, session, location


def _paths(tmp_path: Path) -> dict[str, Path]:
    return {"photos": tmp_path / "photos", "logs": tmp_path / "logs"}


def _silence_dialogs(monkeypatch, answer) -> None:
    monkeypatch.setattr(
        rack_dialog.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(
        rack_dialog.QMessageBox, "question", lambda *a, **k: answer)
    monkeypatch.setattr(
        rack_dialog.QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(rack_dialog, "ollama_status", lambda: (False, "нет ИИ"))


def test_draft_survives_reject_and_restores(tmp_path, monkeypatch) -> None:
    _qapp()
    repo, session, _location = _repo_and_session(tmp_path)
    _silence_dialogs(monkeypatch, None)
    dialog = RackDialog(repo, session.id, _paths(tmp_path))
    dialog._append_row(None)
    dialog.table.item(0, rack_dialog._COL_NAME).setText("D-Link DES-1210")
    dialog.table.item(0, rack_dialog._COL_POS).setText("U5")
    dialog.reject()

    draft = repo.get_setting(DRAFT_KEY)
    assert isinstance(draft, dict)
    assert draft["session_id"] == session.id
    assert len(draft["rows"]) == 1
    assert draft["rows"][0]["pos"] == "U5"

    # Новый диалог той же сессии предлагает черновик; соглашаемся.
    _silence_dialogs(
        monkeypatch, QMessageBox.StandardButton.Yes)
    second = RackDialog(repo, session.id, _paths(tmp_path))
    assert second.table.rowCount() == 1
    assert second.table.item(0, rack_dialog._COL_NAME).text() == "D-Link DES-1210"
    assert second.create_btn.isEnabled()


def test_declined_draft_is_cleared(tmp_path, monkeypatch) -> None:
    _qapp()
    repo, session, _location = _repo_and_session(tmp_path)
    repo.set_setting(
        DRAFT_KEY,
        {"session_id": session.id, "rows": [{"type": "switch", "name": "x"}]},
    )
    _silence_dialogs(monkeypatch, QMessageBox.StandardButton.No)
    dialog = RackDialog(repo, session.id, _paths(tmp_path))
    assert dialog.table.rowCount() == 0  # черновик не поднимали
    assert repo.get_setting(DRAFT_KEY) is None  # и вычищен


def test_create_devices_clears_draft(tmp_path, monkeypatch) -> None:
    _qapp()
    repo, session, location = _repo_and_session(tmp_path)
    repo.set_setting(
        DRAFT_KEY,
        {"session_id": session.id, "rows": [{"type": "switch", "name": "x"}]},
    )
    _silence_dialogs(monkeypatch, QMessageBox.StandardButton.Yes)
    dialog = RackDialog(repo, session.id, _paths(tmp_path), location=location)
    assert dialog.table.rowCount() == 1  # восстановлен preset-черновик
    dialog._create_selected()
    assert dialog.created == 1
    assert repo.get_setting(DRAFT_KEY) is None  # черновик исполнен
    devices = repo.list_devices(session.id)
    assert len(devices) == 1
    assert devices[0].model == "x" or devices[0].note  # строка попала в устройство


def test_draft_other_session_ignored(tmp_path, monkeypatch) -> None:
    _qapp()
    repo, session, _location = _repo_and_session(tmp_path)
    repo.set_setting(
        DRAFT_KEY,
        {"session_id": "другая-сессия", "rows": [{"type": "ups", "name": "y"}]},
    )
    _silence_dialogs(monkeypatch, None)
    dialog = RackDialog(repo, session.id, _paths(tmp_path))
    assert dialog.table.rowCount() == 0  # чужой черновик не предлагаем

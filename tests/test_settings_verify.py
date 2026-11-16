# tests/test_settings_verify.py
"""v1.6.11: «🔍 Проверить копии» в Настройках → Данные (verify_manifest в UI)."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from inventory.config import AppConfig  # noqa: E402
from inventory.core.db import Database  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.services.backup import backup_database  # noqa: E402
from inventory.ui.settings_dialog import SettingsDialog  # noqa: E402

_APP: QApplication | None = None


def _qapp() -> QApplication:
    global _APP
    if QApplication.instance() is None:
        _APP = QApplication([])
    return QApplication.instance()


def _paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "data": tmp_path / "data",
        "db": tmp_path / "data" / "inventory.db",
        "photos": tmp_path / "photos",
        "exports": tmp_path / "exports",
        "backups": tmp_path / "data" / "backups",
        "logs": tmp_path / "logs",
    }


def _repo(db_path: Path) -> InventoryRepository:
    repo = InventoryRepository(Database(db_path))
    session = repo.create_session("Объект", "Инспектор")
    location = repo.create_location(session.id, "2", "204", "Корпус А", "Ш2")
    repo.create_device(session.id, "camera", location.id, model="DS-2CD2143G0")
    return repo


def test_verify_all_ok(tmp_path: Path, monkeypatch) -> None:
    _qapp()
    import inventory.ui.settings_dialog as settings_mod

    paths = _paths(tmp_path)
    repo = _repo(paths["db"])
    backup_database(paths["db"], paths["backups"])
    backup_database(paths["db"], paths["backups"])

    shown: list[str] = []
    monkeypatch.setattr(
        settings_mod.QMessageBox, "information",
        lambda *a, **k: shown.append(str(a[2])),
    )
    dialog = SettingsDialog(AppConfig(), paths, repo=repo)
    dialog.verify_btn.click()
    assert shown and "в порядке" in shown[0]
    assert "2" in shown[0]  # количество копий в сводке


def test_verify_tampered_copy_reported(tmp_path: Path, monkeypatch) -> None:
    _qapp()
    import inventory.ui.settings_dialog as settings_mod

    paths = _paths(tmp_path)
    repo = _repo(paths["db"])
    good = backup_database(paths["db"], paths["backups"])
    bad = backup_database(paths["db"], paths["backups"])
    assert good and bad
    bad.write_bytes(b"tampered garbage")  # хеш больше не совпадёт

    shown: list[str] = []
    monkeypatch.setattr(
        settings_mod.QMessageBox, "information",
        lambda *a, **k: shown.append(str(a[2])),
    )
    monkeypatch.setattr(
        settings_mod.QMessageBox, "warning",
        lambda *a, **k: shown.append(str(a[2])),
    )
    dialog = SettingsDialog(AppConfig(), paths, repo=repo)
    dialog.verify_btn.click()
    assert shown and "SHA-256" in shown[0]
    assert bad.name in shown[0]
    assert good.name in shown[0]  # хорошая копия упомянута как ок


def test_verify_no_backups_at_all(tmp_path: Path, monkeypatch) -> None:
    _qapp()
    import inventory.ui.settings_dialog as settings_mod

    paths = _paths(tmp_path)
    repo = _repo(paths["db"])

    shown: list[str] = []
    monkeypatch.setattr(
        settings_mod.QMessageBox, "information",
        lambda *a, **k: shown.append(str(a[2])),
    )
    dialog = SettingsDialog(AppConfig(), paths, repo=repo)
    dialog.verify_btn.click()
    assert shown and ("ещё нет" in shown[0] or "нет" in shown[0])

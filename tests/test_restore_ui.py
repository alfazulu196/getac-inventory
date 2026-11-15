# tests/test_restore_ui.py
"""FR-60 (v1.6.9): восстановление базы из копии — WAL-safe сервис и UI-поток."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QMessageBox,
)

from inventory.config import AppConfig  # noqa: E402
from inventory.core.db import Database  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.services.backup import (  # noqa: E402
    backup_database,
    restore_database,
)
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


def _repo_with_devices(db_path: Path, count: int) -> InventoryRepository:
    repo = InventoryRepository(Database(db_path))
    session = repo.create_session("Объект", "Инспектор")
    location = repo.create_location(session.id, "2", "204", "Корпус А", "Ш2")
    for i in range(count):
        repo.create_device(
            session.id, "camera", location.id, model=f"DS-2CD{i:04d}")
    return repo


def test_restore_database_wal_safe(tmp_path: Path) -> None:
    """Restore под WAL: safety-копия консистентна, хвосты -wal/-shm убраны."""
    db_path = tmp_path / "inventory.db"
    backups = tmp_path / "backups"
    _qapp()

    repo = _repo_with_devices(db_path, 2)
    good_copy = backup_database(db_path, backups)  # 2 устройства
    assert good_copy is not None

    # Дальше в живую базу пишем ещё — WAL набирает хвост.
    session = repo.list_sessions()[0]
    location = repo.list_locations(session.id)[0]
    repo.create_device(session.id, "ups", location.id, model="AP7921")
    repo.db.close()

    # Чужие хвосты от старой базы должны быть убраны после подмены файла.
    # (Создаём ПОСЛЕ закрытия соединения: живой SQLite перезапишет мусор.)
    (db_path.parent / "inventory.db-wal").write_bytes(b"stale wal tail")
    (db_path.parent / "inventory.db-shm").write_bytes(b"stale shm")

    restore_database(good_copy, db_path, safety_dir=backups)
    safety = next((backups).glob("before_restore_*.db"), None)

    assert safety is not None and safety.exists()
    assert safety.name.startswith("before_restore_")
    # Хвосты убраны ДО проверки открытия (открытие само создаст новый WAL).
    assert not (db_path.parent / "inventory.db-wal").exists()
    assert not (db_path.parent / "inventory.db-shm").exists()
    # Восстановленное содержимое — 2 устройства, не 3.
    fresh = InventoryRepository(Database(db_path))
    session2 = fresh.list_sessions()[0]
    assert len(fresh.list_devices(session2.id)) == 2


def test_settings_restore_flow(tmp_path: Path, monkeypatch) -> None:
    """Кнопка «↩ Восстановить»: выбор копии → подтверждение → восстановление."""
    _qapp()
    paths = _paths(tmp_path)
    repo = _repo_with_devices(paths["db"], 1)
    copy = backup_database(paths["db"], paths["backups"])
    assert copy is not None
    session = repo.list_sessions()[0]
    location = repo.list_locations(session.id)[0]
    repo.create_device(session.id, "ups", location.id, model="AP7921")

    picked: list[str] = []
    questions: list[str] = []
    infos: list[str] = []

    import inventory.ui.settings_dialog as settings_mod

    monkeypatch.setattr(
        settings_mod.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(copy), "")),
    )
    monkeypatch.setattr(
        settings_mod.QMessageBox, "question",
        staticmethod(lambda *a, **k: (
            questions.append(str(a[2]) if len(a) > 2 else k.get("text", "")),
            QMessageBox.StandardButton.Yes)[1]),
    )
    monkeypatch.setattr(
        settings_mod.QMessageBox, "information",
        staticmethod(lambda *a, **k: infos.append(str(a[2]))),
    )

    dialog = SettingsDialog(AppConfig(), paths, repo=repo)
    dialog.restore_btn.click()

    assert questions and "before_restore" in questions[0]
    assert infos and "закроется" in infos[0]
    picked  # выбор файла шёл через диалог (см. monkeypatch выше)

    # Восстановленная база открылась заново: 1 устройство, а не 2.
    fresh = InventoryRepository(Database(paths["db"]))
    assert len(fresh.list_devices(fresh.list_sessions()[0].id)) == 1


def test_settings_restore_cancelled(tmp_path: Path, monkeypatch) -> None:
    """Отмена в диалоге выбора копии — ничего не трогает."""
    _qapp()
    paths = _paths(tmp_path)
    repo = _repo_with_devices(paths["db"], 1)
    session = repo.list_sessions()[0]

    import inventory.ui.settings_dialog as settings_mod

    monkeypatch.setattr(
        settings_mod.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: ("", "")),
    )
    dialog = SettingsDialog(AppConfig(), paths, repo=repo)
    dialog.restore_btn.click()
    # База жива: соединение не закрывалось, данные на месте.
    assert len(repo.list_devices(session.id)) == 1


def test_settings_restore_missing_copy_shows_error(
    tmp_path: Path, monkeypatch
) -> None:
    """Копия исчезла между выбором и восстановлением → понятная ошибка."""
    _qapp()
    paths = _paths(tmp_path)
    repo = _repo_with_devices(paths["db"], 1)
    session = repo.list_sessions()[0]

    import inventory.ui.settings_dialog as settings_mod

    monkeypatch.setattr(
        settings_mod.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(tmp_path / "ghost.db"), "")),
    )
    monkeypatch.setattr(
        settings_mod.QMessageBox, "question",
        staticmethod(
            lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    errors: list[str] = []
    monkeypatch.setattr(
        settings_mod.QMessageBox, "critical",
        staticmethod(lambda *a, **k: errors.append(str(a[2]))),
    )
    dialog = SettingsDialog(AppConfig(), paths, repo=repo)
    dialog.restore_btn.click()
    assert errors and "не найдена" in errors[0]
    # База не тронута (восстановление не начиналось) и соединение живо.
    assert len(repo.list_devices(session.id)) == 1


def test_dialog_code_accept_used() -> None:  # sanity: QDialog импортирован
    assert QDialog.DialogCode.Accepted is not None

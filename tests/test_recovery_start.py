# tests/test_recovery_start.py
"""v1.6.10: автовосстановление при битой базе на старте приложения."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from inventory.app import open_database_with_recovery  # noqa: E402
from inventory.core.db import Database  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.services.backup import backup_database  # noqa: E402

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


def _make_good_base(db_path: Path, devices: int = 1) -> None:
    repo = InventoryRepository(Database(db_path))
    session = repo.create_session("Объект", "Инспектор")
    location = repo.create_location(session.id, "2", "204", "Корпус А", "Ш2")
    for i in range(devices):
        repo.create_device(
            session.id, "camera", location.id, model=f"DS-2CD{i:04d}")
    repo.db.close()


def test_recovery_restores_from_copy(tmp_path: Path, monkeypatch) -> None:
    """Битая база + копия на диске: выбор → восстановление → база открывается."""
    _qapp()
    import inventory.app as app_mod

    paths = _paths(tmp_path)
    _make_good_base(paths["db"])
    copy = backup_database(paths["db"], paths["backups"])
    assert copy is not None
    paths["db"].write_bytes(b"broken garbage")  # база побита

    monkeypatch.setattr(
        app_mod.QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(
        app_mod.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(copy), "")),
    )
    db = open_database_with_recovery(paths)
    assert db is not None
    repo = InventoryRepository(db)
    assert len(repo.list_devices(repo.list_sessions()[0].id)) == 1
    db.close()


def test_recovery_declined_returns_none(tmp_path: Path, monkeypatch) -> None:
    _qapp()
    import inventory.app as app_mod

    paths = _paths(tmp_path)
    _make_good_base(paths["db"])
    backup_database(paths["db"], paths["backups"])
    paths["db"].write_bytes(b"broken garbage")

    monkeypatch.setattr(
        app_mod.QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    assert open_database_with_recovery(paths) is None


def test_recovery_no_backups_shows_error(tmp_path: Path, monkeypatch) -> None:
    _qapp()
    import inventory.app as app_mod

    paths = _paths(tmp_path)
    paths["db"].parent.mkdir(parents=True)
    paths["db"].write_bytes(b"broken garbage")  # копий нет вовсе

    errors: list[str] = []
    monkeypatch.setattr(
        app_mod.QMessageBox, "critical",
        staticmethod(lambda *a, **k: errors.append(str(a[2]))),
    )
    assert open_database_with_recovery(paths) is None
    assert errors and "восстановление невозможно" in errors[0]


def test_recovery_second_copy_after_corrupt_choice(
    tmp_path: Path, monkeypatch
) -> None:
    """Первая выбранная копия тоже битая → цикл даёт выбрать другую."""
    _qapp()
    import inventory.app as app_mod

    paths = _paths(tmp_path)
    _make_good_base(paths["db"])
    good_copy = backup_database(paths["db"], paths["backups"])
    # Вторая (более свежая по имени) копия — мусор.
    bad_copy = paths["backups"] / "inventory_99999999_999999_999999.db"
    bad_copy.write_bytes(b"also broken")
    paths["db"].write_bytes(b"broken garbage")

    monkeypatch.setattr(
        app_mod.QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    picks = [str(bad_copy), str(good_copy)]
    monkeypatch.setattr(
        app_mod.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (picks.pop(0), "")),
    )
    db = open_database_with_recovery(paths)
    assert db is not None  # вторая попытка удалась
    repo = InventoryRepository(db)
    assert len(repo.list_devices(repo.list_sessions()[0].id)) == 1
    db.close()


def test_recovery_broken_file_saved_as_safety(
    tmp_path: Path, monkeypatch
) -> None:
    """Битый файл не исчезает: он сохраняется как before_restore_*."""
    _qapp()
    import inventory.app as app_mod

    paths = _paths(tmp_path)
    _make_good_base(paths["db"])
    copy = backup_database(paths["db"], paths["backups"])
    paths["db"].write_bytes(b"precious broken bytes")

    monkeypatch.setattr(
        app_mod.QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(
        app_mod.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(copy), "")),
    )
    db = open_database_with_recovery(paths)
    assert db is not None
    db.close()

    safeties = list(paths["backups"].glob("before_restore_*.db"))
    assert safeties, "битый файл должен сохраниться как safety-копия"
    assert safeties[0].read_bytes() == b"precious broken bytes"

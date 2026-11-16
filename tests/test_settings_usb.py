# tests/test_settings_usb.py
"""v1.6.12: «💾 Копии на флешку» — сервис + кнопка в Настройках → Данные."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from inventory.config import AppConfig  # noqa: E402
from inventory.core.db import Database  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.services.backup import backup_database  # noqa: E402
import inventory.ui.settings_dialog as settings_mod  # noqa: E402
from inventory.ui.settings_dialog import SettingsDialog, _removable_drives  # noqa: E402

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
    repo.create_location(session.id, "2", "204", "Корпус А", "Ш2")
    return repo


def _dialog(paths: dict[str, Path], repo: InventoryRepository) -> SettingsDialog:
    _qapp()
    return SettingsDialog(AppConfig(), paths, repo=repo)


def _patch_prompts(monkeypatch, drive: str | None) -> list[dict]:
    """QInputDialog.getItem → выбранная строка; getExistingDirectory — tmp."""
    captured: list[dict] = []

    def fake_item(*args, **kwargs):
        if drive is None:
            return ("", False)
        return (drive, True)

    def fake_dir(*args, **kwargs):
        return str(kwargs.get("_tmp", "")) if kwargs.get("_tmp") else "C:\\fake_dir"

    monkeypatch.setattr(settings_mod.QInputDialog, "getItem", fake_item)
    monkeypatch.setattr(settings_mod.QFileDialog, "getExistingDirectory", fake_dir)
    return captured


# ------------------------------------------------------------------ сервис
def test_service_copies_and_verifies(tmp_path: Path) -> None:
    from inventory.services.backup import copy_backups_to_target

    db_path = tmp_path / "inventory.db"
    repo = _repo(db_path)
    repo.db.close()
    backups = tmp_path / "backups"
    backup_database(db_path, backups)
    backup_database(db_path, backups)

    result = copy_backups_to_target(backups, tmp_path / "usb" / "копии")
    assert len(result["copied"]) == 2
    assert not result["skipped"]
    assert not result["warnings"]
    assert result["bytes"] > 0
    dest = tmp_path / "usb" / "копии"
    names = sorted(p.name for p in dest.glob("*.db"))
    assert names == sorted(result["copied"])
    assert (dest / "manifest.json").exists()
    # Манифест на флешке совпадает с источником.
    assert (
        (dest / "manifest.json").read_text("utf-8")
        == (backups / "manifest.json").read_text("utf-8")
    )


def test_service_skips_tampered_source(tmp_path: Path) -> None:
    from inventory.services.backup import copy_backups_to_target

    db_path = tmp_path / "inventory.db"
    _repo(db_path).db.close()
    backups = tmp_path / "backups"
    good = backup_database(db_path, backups)
    bad = backup_database(db_path, backups)
    assert good and bad
    bad.write_bytes(b"tampered garbage")

    result = copy_backups_to_target(backups, tmp_path / "usb")
    assert result["copied"] == [good.name]
    assert len(result["skipped"]) == 1 and bad.name in result["skipped"][0]
    assert not (tmp_path / "usb" / bad.name).exists()  # побитая не уехала
    assert (tmp_path / "usb" / good.name).exists()


def test_service_copies_unlisted_with_warning(tmp_path: Path) -> None:
    from inventory.services.backup import copy_backups_to_target

    db_path = tmp_path / "inventory.db"
    _repo(db_path).db.close()
    backups = tmp_path / "backups"
    listed = backup_database(db_path, backups)
    assert listed
    # Копия вне манифеста (сделана до манифеста или подложена).
    unlisted = backups / "inventory_old.db"
    unlisted.write_bytes((backups / listed.name).read_bytes())

    result = copy_backups_to_target(backups, tmp_path / "usb")
    assert set(result["copied"]) == {listed.name, "inventory_old.db"}
    assert len(result["warnings"]) == 1 and "нет записи" in result["warnings"][0]


def test_service_no_backups(tmp_path: Path) -> None:
    from inventory.services.backup import copy_backups_to_target

    result = copy_backups_to_target(tmp_path / "backups", tmp_path / "usb")
    assert result["copied"] == []
    assert any("нечего" in w for w in result["warnings"])


def test_removable_drives_shape() -> None:
    """На Windows — подмножество дисков формата X:\\; без креша в любом случае."""
    drives = _removable_drives()
    assert isinstance(drives, list)
    for drive in drives:
        assert len(drive) == 3 and drive[1:3] == ":\\"


# --------------------------------------------------------------------- UI
def test_ui_copy_to_drive_ok(tmp_path: Path, monkeypatch) -> None:
    import inventory.services.backup as backup_mod

    paths = _paths(tmp_path)
    repo = _repo(paths["db"])
    backup_database(paths["db"], paths["backups"])

    monkeypatch.setattr(settings_mod, "_removable_drives", lambda: ["E:\\"])
    monkeypatch.setattr(
        settings_mod.QInputDialog, "getItem",
        lambda *a, **k: ("E:\\", True))

    captured: dict = {}

    def fake_copy(backups_dir, target_dir):
        captured["backups"] = str(backups_dir)
        captured["target"] = str(target_dir)
        return {
            "target": str(target_dir), "copied": ["inventory_x.db"],
            "warnings": [], "skipped": [], "bytes": 1024 * 1024,
        }

    monkeypatch.setattr(backup_mod, "copy_backups_to_target", fake_copy)

    shown: list[str] = []
    monkeypatch.setattr(
        settings_mod.QMessageBox, "information",
        lambda *a, **k: shown.append(str(a[2])))

    dialog = _dialog(paths, repo)
    dialog.usb_btn.click()
    assert captured["backups"] == str(paths["backups"])
    assert captured["target"].startswith("E:\\")
    assert "Инвентаризация_копии" in captured["target"]
    assert "inventory_backup_" in captured["target"]
    assert shown and "Скопировано" in shown[0] and "E:" in shown[0]
    # Кнопка вернулась в рабочее состояние.
    assert dialog.usb_btn.isEnabled()


def test_ui_copy_reports_warnings(tmp_path: Path, monkeypatch) -> None:
    import inventory.services.backup as backup_mod

    paths = _paths(tmp_path)
    repo = _repo(paths["db"])
    backup_database(paths["db"], paths["backups"])

    monkeypatch.setattr(settings_mod, "_removable_drives", lambda: ["F:\\"])
    monkeypatch.setattr(
        settings_mod.QInputDialog, "getItem",
        lambda *a, **k: ("F:\\", True))

    def fake_copy(backups_dir, target_dir):
        return {
            "target": str(target_dir), "copied": ["a.db"],
            "warnings": ["b.db: нет записи в манифесте — целостность"
                         " сверена копия-с-копией"],
            "skipped": ["c.db: SHA-256 не совпал — копия побита в источнике,"
                        " пропущена"],
            "bytes": 10,
        }

    monkeypatch.setattr(backup_mod, "copy_backups_to_target", fake_copy)

    shown: list[str] = []
    monkeypatch.setattr(
        settings_mod.QMessageBox, "warning",
        lambda *a, **k: shown.append(str(a[2])))

    dialog = _dialog(paths, repo)
    dialog.usb_btn.click()
    assert shown
    assert "a.db" in shown[0] and "нет записи" in shown[0]
    assert "c.db" in shown[0]


def test_ui_copy_no_backups(tmp_path: Path, monkeypatch) -> None:
    import inventory.services.backup as backup_mod

    paths = _paths(tmp_path)
    repo = _repo(paths["db"])

    monkeypatch.setattr(settings_mod, "_removable_drives", lambda: ["E:\\"])
    monkeypatch.setattr(
        settings_mod.QInputDialog, "getItem",
        lambda *a, **k: ("E:\\", True))

    called: list = []
    monkeypatch.setattr(
        backup_mod, "copy_backups_to_target",
        lambda *a, **k: called.append(a))

    shown: list[str] = []
    monkeypatch.setattr(
        settings_mod.QMessageBox, "information",
        lambda *a, **k: shown.append(str(a[2])))

    dialog = _dialog(paths, repo)
    dialog.usb_btn.click()
    assert not called  # до сервиса дело не дошло
    assert shown and "нечего" in shown[0]


def test_ui_cancel_choice(tmp_path: Path, monkeypatch) -> None:
    import inventory.services.backup as backup_mod

    paths = _paths(tmp_path)
    repo = _repo(paths["db"])
    backup_database(paths["db"], paths["backups"])

    monkeypatch.setattr(settings_mod, "_removable_drives", lambda: ["E:\\"])
    monkeypatch.setattr(
        settings_mod.QInputDialog, "getItem",
        lambda *a, **k: ("", False))

    called: list = []
    monkeypatch.setattr(
        backup_mod, "copy_backups_to_target",
        lambda *a, **k: called.append(a))

    dialog = _dialog(paths, repo)
    dialog.usb_btn.click()
    assert not called

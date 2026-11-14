# tests/test_backup.py
import hashlib
import json
import sqlite3

from inventory.core.db import Database
from inventory.core.repository import InventoryRepository
from inventory.services.backup import (
    backup_database,
    prune_backups,
    restore_database,
    verify_manifest,
)


def test_backup_and_restore(tmp_path):
    db_path = tmp_path / "inventory.db"
    db = Database(db_path)
    repo = InventoryRepository(db)
    session = repo.create_session("Объект", "Инспектор")
    repo.create_location(session.id, "1", "101")
    db.close()

    backup_path = backup_database(db_path, tmp_path / "backups")
    assert backup_path is not None
    assert backup_path.exists()

    # Портим рабочую базу и восстанавливаем.
    db_path.write_bytes(b"broken")
    safety = restore_database(backup_path, db_path, tmp_path / "safety")
    assert safety is not None
    assert safety.exists()

    connection = sqlite3.connect(str(db_path))
    try:
        count = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    finally:
        connection.close()
    assert count == 1


# ------------------------------------------------------------- манифест
def _make_repo(tmp_path):
    db = Database(tmp_path / "inventory.db")
    repo = InventoryRepository(db)
    repo.create_session("Объект", "Инспектор")
    return repo, tmp_path / "inventory.db"


def test_backup_registers_sha256_in_manifest(tmp_path):
    repo, db_path = _make_repo(tmp_path)
    backup_database(db_path, tmp_path / "backups")
    backup_database(db_path, tmp_path / "backups")
    manifest = json.loads(
        (tmp_path / "backups" / "manifest.json").read_text("utf-8")
    )
    entries = manifest["backups"]
    assert len(entries) == 2
    # свежие сверху (обратная сортировка по имени-штампу)
    assert entries[0]["file"] >= entries[1]["file"]
    for entry in entries:
        path = tmp_path / "backups" / entry["file"]
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        assert entry["sha256"] == expected
        assert entry["size"] == path.stat().st_size
        assert entry["created"].count("T") == 1
    assert verify_manifest(tmp_path / "backups") == []


def test_prune_syncs_manifest(tmp_path):
    repo, db_path = _make_repo(tmp_path)
    backups = tmp_path / "backups"
    for _ in range(4):
        backup_database(db_path, backups)
    removed = prune_backups(backups, keep=2)
    assert len(removed) == 2
    manifest = json.loads((backups / "manifest.json").read_text("utf-8"))
    names = {entry["file"] for entry in manifest["backups"]}
    on_disk = {path.name for path in backups.glob("inventory_*.db")}
    assert names == on_disk
    assert len(names) == 2
    assert verify_manifest(backups) == []


def test_verify_detects_tamper_and_missing(tmp_path):
    repo, db_path = _make_repo(tmp_path)
    backups = tmp_path / "backups"
    first = backup_database(db_path, backups)
    second = backup_database(db_path, backups)
    assert verify_manifest(backups) == []

    # Порчу содержимое второй копии.
    with second.open("ab") as fh:
        fh.write(b"tampered")
    issues = verify_manifest(backups)
    assert len(issues) == 1 and second.name in issues[0] and "SHA-256" in issues[0]

    # Удаляю первую: запись в манифесте остаётся — файл пропал.
    first.unlink()
    issues = verify_manifest(backups)
    assert any(first.name in issue and "нет на диске" in issue for issue in issues)


def test_verify_flags_untracked_copy(tmp_path):
    repo, db_path = _make_repo(tmp_path)
    backups = tmp_path / "backups"
    backup_database(db_path, backups)
    (backups / "inventory_ручная_копия.db").write_bytes(b"manual")
    issues = verify_manifest(backups)
    assert len(issues) == 1
    assert "нет записи в манифесте" in issues[0]


def test_verify_empty_dir_and_missing_dir(tmp_path):
    assert verify_manifest(tmp_path / "нет_такой") == []
    empty = tmp_path / "пусто"
    empty.mkdir()
    assert verify_manifest(empty) == []

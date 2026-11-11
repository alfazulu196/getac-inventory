# tests/test_backup.py
import sqlite3

from inventory.core.db import Database
from inventory.core.repository import InventoryRepository
from inventory.services.backup import backup_database, restore_database


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

# tests/test_db_compat.py
"""Совместимость со старой базой (песочница перед полевым выездом).

Проверяем: база, созданная ранней версией приложения (без новых колонок и
без записи о версии схемы), открывается новой сборкой и сама получает
недостающие колонки через цепочку MIGRATIONS.
"""
from __future__ import annotations

import pytest

from inventory.core.db import SCHEMA_VERSION, Database
from inventory.core.repository import InventoryRepository


def _columns(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_old_db_without_schema_version_self_migrates(tmp_path):
    # 1) «Старая» база: колонки поздних версий удалены, счётчик версии сброшен.
    path = tmp_path / "inventory.db"
    fresh = Database(path)
    fresh.conn.execute("ALTER TABLE sessions DROP COLUMN contractor")
    fresh.conn.execute("ALTER TABLE sessions DROP COLUMN baseline_path")
    fresh.conn.execute("ALTER TABLE devices DROP COLUMN nvr_channel")
    fresh.conn.execute("DELETE FROM schema_version")
    fresh.conn.commit()
    fresh.close()

    # 2) Новая сборка открывает её как есть — без ручных действий.
    aged = Database(path)
    assert "contractor" in _columns(aged.conn, "sessions")
    assert "baseline_path" in _columns(aged.conn, "sessions")
    assert "nvr_channel" in _columns(aged.conn, "devices")
    row = aged.conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    assert row["version"] == SCHEMA_VERSION

    # 3) База полностью работоспособна: создаём сессию, локацию, устройство.
    repo = InventoryRepository(aged)
    session = repo.create_session("Старая база", "Инспектор")
    location = repo.create_location(session.id, "1", "101", "Корпус", "Шкаф")
    device = repo.create_device(
        session.id,
        "switch",
        location.id,
        model="DES-1210",
        note="после миграции",
        is_verified=False,
    )
    assert device.id
    assert device.nvr_channel is None  # колонка дородилась миграцией
    aged.close()


def test_future_db_is_rejected(tmp_path):
    path = tmp_path / "future.db"
    old = Database(path)
    old.conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 5,))
    old.conn.commit()
    old.close()
    with pytest.raises(RuntimeError, match="более новой"):
        Database(path)

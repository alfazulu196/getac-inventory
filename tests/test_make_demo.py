# tests/test_make_demo.py
"""Генератор демо-сессии: счётчики, повторный запуск, «прошлая таблица»."""
from __future__ import annotations

from pathlib import Path


from tools.make_demo import build_demo


def test_build_demo_creates_session_and_baseline(tmp_path: Path):
    db_path = tmp_path / "demo.db"
    result = build_demo(db_path)
    assert result["devices"] >= 25
    assert result["locations"] == 6
    baseline = result["baseline"]
    assert isinstance(baseline, Path) and baseline.exists()

    # «Прошлая таблица» читается парсером сверки.
    from inventory.core.baseline import parse_baseline

    rows = parse_baseline(baseline)
    serials = {row["serial"] for row in rows}
    assert len(rows) >= 25
    assert "452000000009" in serials        # проблемная камера из демо
    assert "4520099900001" in serials       # «пропавшая» из прошлой проверки

    # В базе демо-сессия активна.
    from inventory.core.repository import InventoryRepository

    repo = InventoryRepository(
        __import__("inventory.core.db", fromlist=["Database"]).Database(db_path)
    )
    session = repo.get_active_session()
    assert session is not None and session.name == "Демо-репетиция"


def test_build_demo_is_reentrant(tmp_path: Path):
    db_path = tmp_path / "demo.db"
    first = build_demo(db_path)
    second = build_demo(db_path)
    assert first["devices"] == second["devices"]


def test_build_demo_refuses_over_live_session(tmp_path: Path):
    from inventory.core.db import Database
    from inventory.core.repository import InventoryRepository

    db_path = tmp_path / "real.db"
    db = Database(db_path)
    repo = InventoryRepository(db)
    repo.create_session("Настоящий объект", "Инспектор")
    db.close()

    import pytest

    with pytest.raises(SystemExit):
        build_demo(db_path)

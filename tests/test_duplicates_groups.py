# tests/test_duplicates_groups.py
"""find_duplicate_groups: полный просмотр сессии по S/N и MAC."""
from __future__ import annotations

from pathlib import Path

from inventory.core.db import Database
from inventory.core.duplicates import find_duplicate_groups
from inventory.core.repository import InventoryRepository


def _repo(tmp_path: Path):
    db = Database(tmp_path / "dup.db")
    repo = InventoryRepository(db)
    session = repo.create_session("Объект", "Инспектор")
    location = repo.create_location(session.id, "1", "10", "", "")
    return repo, session, location


def test_no_duplicates(tmp_path: Path):
    repo, session, location = _repo(tmp_path)
    repo.create_device(session.id, "camera", location.id,
                       serial="AAA111", mac="AA:BB:CC:DD:EE:01")
    repo.create_device(session.id, "camera", location.id, serial="BBB222")
    assert find_duplicate_groups(repo, session.id) == []


def test_serial_and_transitive_mac_one_group(tmp_path: Path):
    """Два устройства по S/N + третье связано с первым по MAC — одна группа."""
    repo, session, location = _repo(tmp_path)
    first = repo.create_device(session.id, "camera", location.id,
                               serial="AAA111", mac="AA:BB:CC:DD:EE:01")
    second = repo.create_device(session.id, "camera", location.id, serial="AAA111")
    third = repo.create_device(session.id, "camera", location.id,
                               mac="AA:BB:CC:DD:EE:01")
    groups = find_duplicate_groups(repo, session.id)
    assert len(groups) == 1
    assert {d.display_id for d in groups[0].devices} == {
        first.display_id, second.display_id, third.display_id
    }
    assert set(groups[0].matched_by) == {"S/N", "MAC"}
    assert groups[0].label() in ("S/N + MAC", "MAC + S/N")


def test_separate_serial_and_mac_groups(tmp_path: Path):
    """Разные пары по S/N и по MAC — две независимые группы."""
    repo, session, location = _repo(tmp_path)
    a1 = repo.create_device(session.id, "camera", location.id, serial="AAA111")
    a2 = repo.create_device(session.id, "camera", location.id, serial="AAA111")
    m1 = repo.create_device(session.id, "switch", location.id, mac="AA:BB:CC:DD:EE:02")
    m2 = repo.create_device(session.id, "switch", location.id, mac="AA:BB:CC:DD:EE:02")
    groups = find_duplicate_groups(repo, session.id)
    assert len(groups) == 2
    members = {tuple(sorted(d.display_id for d in g.devices)) for g in groups}
    assert members == {
        tuple(sorted((a1.display_id, a2.display_id))),
        tuple(sorted((m1.display_id, m2.display_id))),
    }


def test_deleted_devices_do_not_count(tmp_path: Path):
    repo, session, location = _repo(tmp_path)
    first = repo.create_device(session.id, "camera", location.id, serial="AAA111")
    second = repo.create_device(session.id, "camera", location.id, serial="AAA111")
    repo.soft_delete_device(second.id)
    groups = find_duplicate_groups(repo, session.id)
    assert groups == []
    # После удаления первого — тоже пусто.
    repo.soft_delete_device(first.id)
    assert find_duplicate_groups(repo, session.id) == []


def test_demo_session_counts(tmp_path: Path):
    """Смок на связке: у демо-генератора дубли намеренно не создаются."""
    from tools.make_demo import build_demo

    db_path = tmp_path / "demo.db"
    build_demo(db_path)
    db = Database(db_path)
    repo = InventoryRepository(db)
    session = repo.get_active_session()
    assert find_duplicate_groups(repo, session.id) == []

# tests/test_session_import.py
"""Импорт сессии из другой базы: перенос, конфликты, идемпотентность."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from inventory.core.db import Database
from inventory.core.duplicates import find_duplicate_groups
from inventory.core.repository import InventoryRepository
from inventory.core.session_import import (
    CONFLICT_APPEND,
    CONFLICT_FILL,
    CONFLICT_SKIP,
    import_session,
    list_source_sessions,
)


def _db(tmp_path: Path, name: str):
    db = Database(tmp_path / name)
    return db, InventoryRepository(db)


def _make_source(tmp_path: Path) -> tuple[Path, str]:
    """База напарника: камера со связью на коммутатор, фото-файл и примечания."""
    db, repo = _db(tmp_path, "source.db")
    session = repo.create_session("Объект B", "Петров П.П.")
    loc = repo.create_location(session.id, "2", "210", "Корпус А", "Ш-4")
    sw = repo.create_device(session.id, "switch", loc.id, serial="SW999")
    cam = repo.create_device(
        session.id, "camera", loc.id, serial="CAM999", mac="AA:BB:CC:00:11:22",
        ip="192.168.20.55", model="DS-2CD2143G0", status="faulty",
        note="Со второго ноутбука", nvr_channel="D5",
    )
    repo.set_connection(cam.id, sw.id, "Gi0/9", "UTP")
    photos_dir = tmp_path / "source_photos"
    photos_dir.mkdir(exist_ok=True)
    photo_file = photos_dir / "cam999_1.jpg"
    photo_file.write_bytes(b"\xff\xd8\xffstubdata")
    repo.add_photo(cam.id, str(photo_file), role="overall")
    repo.update_device(cam.id, photo_path=str(photo_file))
    source_id = session.id
    db.close()
    return tmp_path / "source.db", source_id


def test_list_source_sessions(tmp_path: Path):
    source_path, _source_id = _make_source(tmp_path)
    sessions = list_source_sessions(source_path)
    assert len(sessions) == 1
    info = sessions[0]
    assert info.object_name == "Объект B"
    assert info.device_count == 2
    assert info.status == "active"


def test_import_no_conflicts(tmp_path: Path):
    source_path, source_id = _make_source(tmp_path)
    db, repo = _db(tmp_path / "target", "target.db")
    session = repo.create_session("Объект A", "Иванов И.И.")
    repo.create_location(session.id, "1", "101", "Корпус А", "Ш-1")
    photos_dir = source_path.parent / "source_photos"

    report = import_session(
        repo, session.id, source_path, source_id, photos_dir=photos_dir,
        on_conflict=CONFLICT_SKIP,
    )
    assert report.imported == 2
    assert report.skipped_duplicate == 0
    assert report.connections_added == 1
    assert report.connections_skipped == 0
    assert report.locations_created == 1  # 2/210 нет в цели
    assert report.locations_reused == 0
    assert report.photos_copied == 1

    devices = repo.list_devices(session.id)
    assert len(devices) == 2
    assert sorted(d.display_id for d in devices) == ["CAM-001", "SW-001"]
    cam = next(d for d in devices if d.serial == "CAM999")
    assert cam.status == "faulty"
    assert cam.nvr_channel == "D5"
    connection = repo.get_connection(cam.id)
    assert connection is not None and connection.upstream_device_id
    # Фото: файл скопирован в целевую папку, обложка указывает на него.
    assert cam.photo_path and Path(cam.photo_path).exists()
    assert "cam999_1" in cam.photo_path
    photos = repo.list_photos(cam.id)
    assert len(photos) == 1 and photos[0].role == "overall"
    db.close()


def test_conflict_modes(tmp_path: Path):
    """Три режима разбора одного конфликта по S/N: skip / append / fill."""
    for mode in (CONFLICT_SKIP, CONFLICT_APPEND, CONFLICT_FILL):
        sub = tmp_path / mode
        sub.mkdir()
        source_path, source_id = _make_source(sub)
        db, repo = _db(sub, "target.db")
        session = repo.create_session("Объект A", "Иванов И.И.")
        loc = repo.create_location(session.id, "1", "101", "Корпус А", "Ш-1")
        # Существующее устройство с тем же S/N: модель/IP/note/канал пустые.
        mine = repo.create_device(session.id, "camera", loc.id, serial="CAM999")

        report = import_session(
            repo, session.id, source_path, source_id,
            photos_dir=sub / "source_photos", on_conflict=mode,
        )
        mine = repo.get_device(mine.id)
        if mode == CONFLICT_SKIP:
            assert report.imported == 1        # только коммутатор
            assert report.skipped_duplicate == 1
            assert mine.model is None
            assert find_duplicate_groups(repo, session.id) == []
        elif mode == CONFLICT_APPEND:
            assert report.imported == 2
            assert report.skipped_duplicate == 0
            groups = find_duplicate_groups(repo, session.id)
            assert len(groups) == 1            # пара камер с одним S/N
            assert mine.id in {d.id for d in groups[0].devices}
            assert report.photos_copied == 1   # фото импортированной копии
        else:  # fill
            assert report.imported == 1        # только коммутатор
            assert report.filled == 1
            assert mine.model == "DS-2CD2143G0"
            assert mine.ip == "192.168.20.55"
            assert mine.nvr_channel == "D5"
            assert mine.note == "Со второго ноутбука"
            assert mine.status == "installed"  # статус существующего сохранён
        db.close()


def test_import_is_idempotent(tmp_path: Path):
    """Повторный импорт той же базы не создаёт копий."""
    source_path, source_id = _make_source(tmp_path)
    db, repo = _db(tmp_path / "target2", "target.db")
    session = repo.create_session("Объект A", "Иванов И.И.")
    repo.create_location(session.id, "1", "101", "Корпус А", "Ш-1")
    photos_dir = source_path.parent / "source_photos"

    first = import_session(
        repo, session.id, source_path, source_id, photos_dir=photos_dir
    )
    assert first.imported == 2
    second = import_session(
        repo, session.id, source_path, source_id, photos_dir=photos_dir
    )
    assert second.imported == 0
    assert second.skipped_existing == 2
    assert len(repo.list_devices(session.id)) == 2
    db.close()


def test_import_renumbers_display_ids(tmp_path: Path):
    """У обеих баз есть CAM-001 — в целевой сессии номеров-двойников нет."""
    source_path, source_id = _make_source(tmp_path)
    db, repo = _db(tmp_path / "target3", "target.db")
    session = repo.create_session("Объект A", "Иванов И.И.")
    loc = repo.create_location(session.id, "1", "101", "Корпус А", "Ш-1")
    repo.create_device(session.id, "camera", loc.id, serial="MYCAM1")  # займёт CAM-001
    import_session(repo, session.id, source_path, source_id)
    display_ids = [d.display_id for d in repo.list_devices(session.id)]
    assert len(display_ids) == len(set(display_ids)) == 3
    assert "CAM-002" in display_ids  # импортная камера получила следующий номер
    db.close()


def test_import_rejects_finished(tmp_path: Path):
    """Импорт в завершённую сессию запрещён: ValueError."""
    import pytest

    source_path, source_id = _make_source(tmp_path)
    db, repo = _db(tmp_path / "target5", "target.db")
    session = repo.create_session("Объект A", "Иванов И.И.")
    repo.finish_session(session.id)
    with pytest.raises(ValueError):
        import_session(repo, session.id, source_path, source_id)
    db.close()


def test_import_handles_bad_ip(tmp_path: Path):
    """Кривой IP в источнике не роняет импорт: поле пропускается, данные в безопасности."""
    import pytest  # noqa: F401

    source_path, source_id = _make_source(tmp_path)
    src2 = sqlite3.connect(source_path)
    src2.execute("UPDATE devices SET ip = '999.10.20.30' WHERE serial = 'CAM999'")
    src2.commit()
    src2.close()

    db, repo = _db(tmp_path / "target6", "target.db")
    session = repo.create_session("Объект A", "Иванов И.И.")
    repo.create_location(session.id, "1", "101", "Корпус А", "Ш-1")
    report = import_session(repo, session.id, source_path, source_id)
    assert report.imported == 2
    assert any("IP" in warning for warning in report.warnings)
    cam = next(d for d in repo.list_devices(session.id) if d.serial == "CAM999")
    assert cam.ip is None
    db.close()

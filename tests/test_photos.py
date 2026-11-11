# tests/test_photos.py
"""Таблица photos: добавить, перечислить в порядке, убрать."""
from __future__ import annotations

from pathlib import Path

from inventory.core.db import Database
from inventory.core.repository import InventoryRepository


def _setup(tmp_path: Path):
    db = Database(tmp_path / "photos.db")
    repo = InventoryRepository(db)
    session = repo.create_session("Объект", "Инспектор")
    location = repo.create_location(session.id, "1", "10", "", "")
    device = repo.create_device(session.id, "camera", location.id, serial="ABC")
    return repo, device


def test_add_photo_keeps_order(tmp_path: Path):
    repo, device = _setup(tmp_path)
    p1 = repo.add_photo(device.id, str(tmp_path / "first.jpg"))
    p2 = repo.add_photo(device.id, str(tmp_path / "second.jpg"), role="overall")
    photos = repo.list_photos(device.id)
    assert [photo.id for photo in photos] == [p1.id, p2.id]
    assert photos[1].role == "overall"


def test_delete_photo_keeps_others(tmp_path: Path):
    repo, device = _setup(tmp_path)
    p1 = repo.add_photo(device.id, str(tmp_path / "a.jpg"))
    repo.add_photo(device.id, str(tmp_path / "b.jpg"))
    repo.delete_photo(p1.id)
    photos = repo.list_photos(device.id)
    assert len(photos) == 1 and photos[0].path.endswith("b.jpg")


def test_delete_missing_photo_is_noop(tmp_path: Path):
    repo, device = _setup(tmp_path)
    repo.delete_photo(98765)
    assert repo.list_photos(device.id) == []


def test_delete_cover_photo_falls_back_to_previous(tmp_path: Path):
    """Второе фото заменяет «обложку» устройства после удаления первого."""
    repo, device = _setup(tmp_path)
    p1 = repo.add_photo(device.id, str(tmp_path / "a.jpg"))
    p2 = repo.add_photo(device.id, str(tmp_path / "b.jpg"))
    repo.update_device(device.id, photo_path=p2.path)
    repo.delete_photo(p2.id)
    updated = repo.get_device(device.id)
    assert updated.photo_path == p1.path

# tests/test_db.py
import pytest

from inventory.core.db import Database
from inventory.core.duplicates import check_duplicates
from inventory.core.parser import parse_code
from inventory.core.repository import InventoryRepository


@pytest.fixture()
def repo(tmp_path):
    db = Database(tmp_path / "test.db")
    repository = InventoryRepository(db)
    yield repository
    db.close()


@pytest.fixture()
def session(repo):
    return repo.create_session("Объект №1", "Иванов И.И.")


@pytest.fixture()
def location(repo, session):
    return repo.create_location(session.id, floor="2", room="204", building="Корпус А", rack="2")


def test_create_and_get_session(repo, session):
    assert session.status == "active"
    assert repo.get_active_session().id == session.id
    assert repo.get_session(session.id).object_name == "Объект №1"


def test_only_one_active_session(repo, session):
    with pytest.raises(ValueError):
        repo.create_session("Объект №2", "Петров П.П.")


def test_finish_session(repo, session):
    repo.finish_session(session.id)
    assert repo.get_session(session.id).status == "completed"
    assert repo.get_active_session() is None


def test_create_location_and_label(repo, session, location):
    assert location.floor == "2"
    assert "2 этаж" in location.label()
    assert "204" in location.label()
    assert repo.get_or_create_location(session.id, "2", "204", "Корпус А", "2").id == location.id


def test_device_display_ids_and_normalization(repo, session, location):
    first = repo.create_device(
        session.id,
        "camera",
        location.id,
        serial="abc 123",
        mac="78-83-c4-5a-6b-7c",
        ip="192.168.10.51",
    )
    second = repo.create_device(session.id, "camera", location.id)
    switch = repo.create_device(session.id, "switch", location.id, serial="FDO2134X0ZZ")

    assert first.display_id == "CAM-001"
    assert second.display_id == "CAM-002"
    assert switch.display_id == "SW-001"
    assert first.serial == "abc 123"
    assert first.serial_norm == "ABC123"
    assert first.mac == "78:83:C4:5A:6B:7C"
    assert first.mac_norm == "7883C45A6B7C"
    assert first.location.label() == "Корпус А, 2 этаж, 204, шкаф 2"


def test_update_device(repo, session, location):
    device = repo.create_device(session.id, "camera", location.id, serial="ABC123")
    updated = repo.update_device(device.id, model="DS-2CD2143G0", note="Тест")
    assert updated.model == "DS-2CD2143G0"
    assert updated.note == "Тест"


def test_invalid_ip_rejected(repo, session, location):
    with pytest.raises(ValueError):
        repo.create_device(session.id, "camera", location.id, ip="999.1.1.1")


def test_soft_delete(repo, session, location):
    device = repo.create_device(session.id, "camera", location.id)
    repo.soft_delete_device(device.id)
    assert repo.get_device(device.id) is None
    assert repo.get_device(device.id, include_deleted=True) is not None
    assert repo.count_devices(session.id) == 0


def test_duplicates_by_serial_and_mac(repo, session, location):
    first = repo.create_device(
        session.id, "camera", location.id, serial="ABC123", mac="AA:BB:CC:DD:EE:FF"
    )
    parsed = parse_code("SN=ABC123;MAC=AA:BB:CC:DD:EE:FF")
    matches = check_duplicates(repo, session.id, parsed=parsed)
    assert len(matches) == 1
    assert matches[0].device.id == first.id
    assert set(matches[0].matched_by) == {"serial", "mac"}


def test_ean_is_not_device_duplicate(repo, session, location):
    repo.create_device(session.id, "camera", location.id, serial="ABC123")
    parsed = parse_code("4006381333931")
    assert check_duplicates(repo, session.id, parsed=parsed) == []


def test_connection(repo, session, location):
    camera = repo.create_device(session.id, "camera", location.id)
    switch = repo.create_device(session.id, "switch", location.id)
    connection = repo.set_connection(camera.id, switch.id, "Gi0/12", "UTP")
    assert connection.upstream_device_id == switch.id
    assert connection.upstream_port == "Gi0/12"
    view = repo.list_connections_view(session.id)
    assert view[0]["device_display_id"] == "CAM-001"
    assert view[0]["upstream_display_id"] == "SW-001"


def test_connection_rejects_self(repo, session, location):
    camera = repo.create_device(session.id, "camera", location.id)
    with pytest.raises(ValueError):
        repo.set_connection(camera.id, camera.id)


def test_summary(repo, session, location):
    repo.create_device(session.id, "camera", location.id, serial="ABC123")
    repo.create_device(session.id, "switch", location.id, serial="FDO2134X0ZZ")
    repo.create_device(session.id, "camera", location.id)
    summary = repo.get_summary(session.id)
    assert summary["total"] == 3
    assert summary["no_serial_mac"] == 1
    assert summary["no_connection"] == 2
    assert {item["code"] for item in summary["by_type"]} == {"camera", "switch"}


def test_scan_events(repo, session):
    repo.log_scan(session.id, "ABC123", "serial", "ABC123", "created")
    events = repo.list_scan_events(session.id)
    assert len(events) == 1
    assert events[0].raw_code == "ABC123"


def test_settings(repo):
    repo.set_setting("last_location_id", "abc")
    assert repo.get_setting("last_location_id") == "abc"
    assert repo.get_setting("missing", "default") == "default"

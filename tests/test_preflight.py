# tests/test_preflight.py
from inventory.config import AppConfig, data_paths
from inventory.services.preflight import CheckResult, PreflightService


def test_preflight_database_and_export_dir(tmp_path):
    config = AppConfig(data_dir=str(tmp_path))
    paths = data_paths(config)
    service = PreflightService(config, paths)

    database = service.check_database()
    assert database.status == "ok"

    export = service.check_export_dir()
    assert export.status == "ok"


def test_preflight_blocks_on_critical_error():
    checks = [
        CheckResult("db", "База", "error", "нет доступа", critical=True),
        CheckResult("scanner", "Сканер", "warning", "нет порта"),
    ]
    assert PreflightService.can_start(checks) is False


def test_preflight_allows_warnings():
    checks = [
        CheckResult("db", "База", "ok", "ok", critical=True),
        CheckResult("scanner", "Сканер", "warning", "нет порта"),
    ]
    assert PreflightService.can_start(checks) is True


def test_preflight_photo_port_states(tmp_path, monkeypatch):
    """Порт приёма фото: свободен / будет соседний / заняты все."""
    import inventory.services.photo_server as photo_server

    config = AppConfig(data_dir=str(tmp_path))
    service = PreflightService(config, data_paths(config))

    monkeypatch.setattr(
        photo_server, "find_free_port", lambda *args, **kwargs: photo_server.DEFAULT_PORT
    )
    assert service.check_photo_port().status == "ok"

    monkeypatch.setattr(
        photo_server, "find_free_port", lambda *args, **kwargs: photo_server.DEFAULT_PORT + 3
    )
    check = service.check_photo_port()
    assert check.status == "warning"

    monkeypatch.setattr(photo_server, "find_free_port", lambda *args, **kwargs: None)
    assert service.check_photo_port().status == "error"


def test_preflight_photo_port_real(tmp_path):
    """Реальная проверка: на чистой машине порт свободен (или уйдёт на соседний)."""
    config = AppConfig(data_dir=str(tmp_path))
    service = PreflightService(config, data_paths(config))
    check = service.check_photo_port()
    assert check.status in ("ok", "warning")

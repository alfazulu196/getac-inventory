# tests/test_baseline.py
"""Парсинг прошлой таблицы и результат сверки."""
from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from inventory.core.baseline import compare_baseline, parse_baseline
from inventory.core.models import Device
from inventory.core.repository import InventoryRepository
from inventory.core.db import Database


class _AnyLoc:
    id = "loc"

    def label(self) -> str:
        return "Здание"


def _device(**kwargs) -> Device:
    defaults = dict(
        id="d1",
        session_id="s1",
        display_id="CAM-001",
        device_type="camera",
        location_id="loc",
        status="installed",
        created_at="2026-01-01",
        updated_at="2026-01-01",
        location=_AnyLoc(),
    )
    defaults.update(kwargs)
    return Device(**defaults)


def _xlsx(tmp_path: Path, headers: list[str], rows: list[list]) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Отчёт"
    ws.append(headers)
    for row in rows:
        ws.append(row)
    path = tmp_path / "past.xlsx"
    wb.save(path)
    return path


def test_parse_baseline_reads_all_headers(tmp_path: Path):
    path = _xlsx(
        tmp_path,
        ["Камера", "S/N", "MAC", "IP", "Производитель", "Модель", "Место"],
        [
            ["CAM-101", "AB12CD34", "AA-BB-CC-DD-EE-FF", "192.168.10.5", "Hikvision", "DS-2CD", "Этаж 2"],
            ["", "", "", "", "", "", ""],
            ["CAM-102", "  ", "", "", "", "", ""],
        ],
    )
    rows = parse_baseline(path)
    assert len(rows) == 2  # пустые строки пропускаются только без id
    first = rows[0]
    assert first["serial_norm"] == "AB12CD34"
    assert first["mac_norm"] == "AABBCCDDEEFF"
    assert first["ip"] == "192.168.10.5"
    assert first["manufacturer"] == "Hikvision"


def test_parse_baseline_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        parse_baseline(tmp_path / "nope.xlsx")


def test_compare_baseline_matches_changes_and_missing(tmp_path: Path):
    baseline = [
        {"display_id": "N-1", "serial_norm": "AB12", "mac_norm": "", "model": "DS-2CD",
         "ip": "192.168.10.5", "location_label": "Этаж 2", "status": "ОК",
         "device_type": "камера", "manufacturer": "", "serial": "AB12", "mac": ""},
        {"display_id": "N-2", "serial_norm": "XY99", "mac_norm": "", "model": "",
         "ip": "", "location_label": "", "status": "", "device_type": "",
         "manufacturer": "", "serial": "XY99", "mac": ""},
        {"display_id": "N-3", "serial_norm": "LOST", "mac_norm": "", "model": "",
         "ip": "", "location_label": "", "status": "", "device_type": "",
         "manufacturer": "", "serial": "LOST", "mac": ""},
    ]
    devices = [
        _device(id="d1", serial="AB12", serial_norm="AB12", mac_norm="", ip="192.168.10.6"),
        _device(id="d2", display_id="CAM-002", serial="NEW", serial_norm="NEW", mac_norm=""),
    ]
    result = compare_baseline(baseline, devices)
    assert result.counts["matched"] == 0  # поменялся IP → changed
    assert result.counts["changed"] == 1
    assert result.counts["new"] == 1
    assert result.counts["missing"] == 2  # N-2 и N-3 не встречены в обходе
    assert result.changed[0][2][0].startswith("IP")
    assert {row["display_id"] for row in result.missing} == {"N-2", "N-3"}


def test_baseline_repo_roundtrip(tmp_path: Path):
    db = Database(tmp_path / "inv.db")
    repo = InventoryRepository(db)
    session = repo.create_session("Объект", "Инспектор")
    rows = [
        {"display_id": "N-1", "serial_norm": "AB12", "mac_norm": "", "model": None,
         "ip": None, "location_label": None, "status": None, "device_type": None,
         "manufacturer": None, "serial": "AB12", "mac": None},
    ]
    result = repo.import_baseline(session.id, "/путь/прошлая.xlsx", rows)
    assert result == {"total": 1, "usable": 1}
    stored = repo.list_baseline(session.id)
    assert len(stored) == 1 and stored[0].serial_norm == "AB12"
    fresh_session = repo.get_session(session.id)
    assert fresh_session.baseline_path == "/путь/прошлая.xlsx"


def test_schema_migration_from_v1(tmp_path: Path):
    import sqlite3

    import inventory.core.db as db_module

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    # Минимальная схема версии 1: без photos/baseline_devices и без baseline_path.
    conn.executescript(
        """
        CREATE TABLE schema_version (version INTEGER, applied_at TEXT);
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, name TEXT, object_name TEXT, inspector TEXT,
            started_at TEXT, finished_at TEXT, status TEXT DEFAULT 'active',
            comment TEXT
        );
        CREATE TABLE locations (
            id TEXT PRIMARY KEY, session_id TEXT, building TEXT, floor TEXT,
            room TEXT, rack TEXT, unit TEXT, description TEXT, created_at TEXT
        );
        CREATE TABLE devices (
            id TEXT PRIMARY KEY, session_id TEXT, display_id TEXT, asset_tag TEXT,
            device_type TEXT, subtype TEXT, manufacturer TEXT, model TEXT,
            serial TEXT, serial_norm TEXT, mac TEXT, mac_norm TEXT, ip TEXT,
            location_id TEXT, power_type TEXT, status TEXT, note TEXT,
            photo_path TEXT, scanned_code TEXT, code_type TEXT,
            attributes_json TEXT, is_verified INTEGER, created_at TEXT,
            updated_at TEXT, deleted_at TEXT
        );
        INSERT INTO schema_version(version, applied_at) VALUES (1, 'x');
        INSERT INTO sessions(id, name, object_name, inspector, started_at, status)
        VALUES ('s1', 'n', 'o', 'i', 't', 'active');
        """
    )
    conn.commit()
    conn.close()

    db = db_module.Database(path)
    from inventory.core.db import SCHEMA_VERSION

    assert db.fetchone("SELECT version FROM schema_version")["version"] == SCHEMA_VERSION
    # Таблицы и колонки появились.
    db.fetchone("SELECT * FROM photos LIMIT 1")
    db.fetchone("SELECT * FROM baseline_devices LIMIT 1")
    db.fetchone("SELECT baseline_path FROM sessions")
    db.fetchone("SELECT nvr_channel FROM devices")
    db.fetchone("SELECT contractor FROM sessions")
    db.close()

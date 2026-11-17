# tests/test_export_netbox.py
"""Экспорт NetBox: slug-и, парсинг позиций, сборка CSV из БД.

Заголовки CSV заморожены по netbox/dcim/forms/bulk_import.py
(DeviceImportForm и др.) — расхождение столбца ломает импорт у клиента,
поэтому тест стоит на страже каждого столбца.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))  # noqa: E402

from export_netbox import (  # noqa: E402
    CSV_HEADERS,
    build_export,
    collect,
    parse_position,
    slugify,
    unique_slug,
    write_csv_files,
)


def test_slugify():
    assert slugify("Корпус А") == "korpus-a"
    assert slugify("D-Link") == "d-link"
    assert slugify("") == "x"
    assert slugify("Стойка 42U!") == "stoyka-42u"


def test_unique_slug():
    taken: set[str] = set()
    first = unique_slug("Room", taken)
    second = unique_slug("Room", taken)
    assert first == "room"
    assert second == "room-2"


def test_parse_position():
    assert parse_position("U5-U8") == (5, 4)
    assert parse_position("U5") == (5, 1)
    assert parse_position("U9 – U11") == (9, 3)
    assert parse_position("U12-U10") == (10, 3)  # перевёрнутый диапазон
    assert parse_position("U5–U8") == (5, 4)  # en-dash
    assert parse_position("полка") is None
    assert parse_position("Uplink") is None


def _make_db(tmp_path: Path):
    from inventory.core.db import Database

    db = Database(tmp_path / "inv.db")
    ts = "2026-11-17T10:00:00+03:00"
    with db.transaction():
        db.execute(
            "INSERT INTO sessions (id, name, object_name, inspector, "
            "started_at, status) VALUES (?,?,?,?,?,?)",
            ("s1", "Сессия", "Объект Демо", "Иванов", ts, "active"),
        )
        for loc_id, rack in (("l1", "Ш-1"), ("l2", "Ш-2"), ("l3", "")):
            db.execute(
                "INSERT INTO locations (id, session_id, building, floor, "
                "room, rack, unit, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (loc_id, "s1", "Корпус А", "1", "101", rack, "", ts),
            )
        rows = [
            # id, display, type, mfr, model, serial, loc, status, note, attrs
            ("d1", "SW-001", "switch", "D-Link", "DES-3200-18", "S1",
             "l1", "installed", "В стойке: U5-U8", ""),
            ("d2", "UPS-001", "ups", None, "Back-UPS", None,
             "l1", "installed", "", '{"position": "U9"}'),
            ("d3", "CAM-001", "camera", "Hikvision", "DS-2CD", None,
             "l3", "installed", "полка", ""),
            ("d4", "SW-002", "switch", "D-Link", "DES-3200-18", None,
             "l2", "faulty", "В стойке: U10", ""),
        ]
        for i, (did, disp, typ, mfr, model, serial, loc, status, note,
                attrs) in enumerate(rows):
            db.execute(
                "INSERT INTO devices (id, session_id, display_id, "
                "device_type, manufacturer, model, serial, location_id, "
                "status, note, attributes_json, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (did, "s1", disp, typ, mfr, model, serial, loc, status,
                 note, attrs, ts, ts),
            )
    return db


def test_build_export(tmp_path):
    db = _make_db(tmp_path)
    result = build_export(collect(db.path))

    sites = result["tables"]["sites.csv"]
    assert len(sites) == 1
    assert sites[0]["name"] == "Объект Демо"
    assert sites[0]["slug"] == "obekt-demo"

    locations = result["tables"]["locations.csv"]
    assert len(locations) == 1  # одно помещение, две стойки внутри
    assert locations[0]["name"] == "Корпус А - 1 - 101"
    assert locations[0]["slug"] == "korpus-a-1-101"

    racks = result["tables"]["racks.csv"]
    assert [r["name"] for r in racks] == ["Ш-1", "Ш-2"]
    assert racks[0]["width"] == "19"
    assert racks[0]["u_height"] == 42

    devices = {d["name"]: d for d in result["tables"]["devices.csv"]}
    assert devices["SW-001"]["position"] == "5"  # нижний юнит U5-U8
    assert devices["SW-001"]["face"] == "front"
    assert devices["SW-001"]["manufacturer"] == "D-Link"
    assert devices["UPS-001"]["position"] == "9"  # из attributes_json
    assert devices["UPS-001"]["manufacturer"] == "Unknown"
    assert devices["CAM-001"]["rack"] == ""
    assert devices["CAM-001"]["position"] == ""
    assert devices["CAM-001"]["face"] == ""
    assert devices["SW-002"]["status"] == "failed"  # faulty
    assert devices["SW-002"]["position"] == "10"

    types = {(t["manufacturer"], t["model"]): t["u_height"]
             for t in result["tables"]["device_types.csv"]}
    assert types[("D-Link", "DES-3200-18")] == 4  # высота из U5-U8
    assert types[("Unknown", "Back-UPS")] == 1  # U9
    db.close()


def test_write_csv_headers(tmp_path):
    db = _make_db(tmp_path)
    result = build_export(collect(db.path))
    out = tmp_path / "out"
    write_csv_files(out, result["tables"])
    with (out / "devices.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == CSV_HEADERS["devices.csv"]
    assert len(rows) == 5  # заголовок + 4 устройства
    assert (out / "README.txt").is_file()
    db.close()

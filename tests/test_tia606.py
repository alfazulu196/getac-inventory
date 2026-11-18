# tests/test_tia606.py
"""TIA-606: генерация/валидация меток и режим БД.

Схема меток заморожена: <SITE>-<ROOM>-<RACK> и
<SITE>-<ROOM>-<RACK>-<PANEL>-<PORT>, сегменты ASCII A-Z0-9.
Расхождение схемы ломает наклейки, уже распечатанные в поле.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))  # noqa: E402

from export_netbox import collect  # noqa: E402
from tia606 import (  # noqa: E402
    LabelSpec,
    generate,
    parse,
    rack_labels,
    validate,
    write_labels_csv,
)


def test_generate_translit():
    assert generate(LabelSpec("Корпус А", "101", "Ш-1")) == "KORPUSA-101-SH1"
    port = LabelSpec("Демо", "Серверная", "Ш-4", "A1", "P01")
    assert generate(port) == "DEMO-SERVERNAYA-SH4-A1-P01"


def test_generate_truncates_long_site():
    label = generate(LabelSpec("ОченьДлинноеИмяОбъекта", "101", "Ш-1"))
    site, room, rack = label.split("-")
    assert site == "OCHENDLI"  # 8 символов, лимит site
    assert len(site) == 8


def test_generate_partial_port_fails():
    with pytest.raises(ValueError):
        generate(LabelSpec("A", "101", "SH1", panel="A1"))


def test_validate():
    assert validate("A-101-SH1") == []
    assert validate("a-101-sh1-a1-p01") == []  # регистр не важен
    assert len(validate("A-101")) == 1  # 2 сегмента
    assert len(validate("A-101-SH1-A1")) == 1  # 4 сегмента — неоднозначно
    assert len(validate("A-101-Ш1")) == 1  # кириллица в стикере недопустима
    assert validate("A-101-SHTOLERKA")[0].startswith("сегмент rack")
    assert len(validate("")) == 1


def test_parse_roundtrip():
    spec = LabelSpec("Demo", "Room1", "R1", "A1", "P02")
    assert parse(generate(spec)) == LabelSpec(
        "DEMO", "ROOM1", "R1", "A1", "P02")
    with pytest.raises(ValueError):
        parse("A-101")


def test_db_mode(tmp_path):
    from inventory.core.db import Database

    db = Database(tmp_path / "inv.db")
    ts = "2026-11-17T10:00:00+03:00"
    with db.transaction():
        db.execute(
            "INSERT INTO sessions (id, name, object_name, inspector, "
            "started_at, status) VALUES (?,?,?,?,?,?)",
            ("s1", "Сессия", "Объект Демо", "Иванов", ts, "active"),
        )
        # Два помещения с разным написанием корпуса, но одинаковой
        # нормализованной меткой — предупреждение о дубликате.
        for loc_id, building in (("l1", "Корпус А"), ("l2", "Корпус-А")):
            db.execute(
                "INSERT INTO locations (id, session_id, building, floor, "
                "room, rack, unit, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (loc_id, "s1", building, "1", "101", "Ш-1", "", ts),
            )
    rows, warnings = rack_labels(collect(db.path))
    assert len(rows) == 2
    assert rows[0]["label"] == "OBEKTDEM-KORPUSA1101-SH1"
    assert rows[0]["label"] == rows[1]["label"]
    assert any("дубликат метки" in w for w in warnings)

    out = tmp_path / "out"
    path = write_labels_csv(out, rows)
    with path.open(encoding="utf-8", newline="") as fh:
        table = list(csv.reader(fh))
    assert table[0] == ["label", "level", "site", "room", "rack",
                        "panel", "port"]
    assert len(table) == 3  # заголовок + 2 стойки
    db.close()

# tests/test_session_baseline.py
"""Сверка из архива сессии (без Excel) и шапка титула из настроек."""
from __future__ import annotations

from openpyxl import load_workbook

from inventory.core.baseline import (
    compare_baseline,
    parse_baseline,
    session_baseline_rows,
)
from inventory.core.db import Database
from inventory.core.export_excel import export_session
from inventory.core.repository import InventoryRepository


def _repo(tmp_path, name="test.db"):
    db = Database(tmp_path / name)
    return db, InventoryRepository(db)


def _populate(repo) -> str:
    """Прошлая сессия: камера, коммутатор, «снятая» позиция."""
    session = repo.create_session("Объект прошлый", "Петров П.П.")
    location = repo.create_location(session.id, "1", "101", "Корпус А", "Ш-1")
    repo.create_device(
        session.id, "camera", location.id,
        serial="C1", mac="AA:BB:CC:00:00:01", model="DS-1", ip="10.0.0.1",
    )
    repo.create_device(
        session.id, "switch", location.id, serial="S1", model="SW-1", ip="10.0.0.2",
    )
    repo.create_device(session.id, "nvr", location.id, serial="OLD")
    repo.finish_session(session.id)  # активной может быть только одна сессия
    return session.id


def _populate_current(repo) -> str:
    """Текущая сессия: та же камера, коммутатор сменил IP, плюс новое."""
    session = repo.create_session("Объект текущий", "Иванов И.И.")
    location = repo.create_location(session.id, "1", "101", "Корпус А", "Ш-1")
    repo.create_device(
        session.id, "camera", location.id,
        serial="C1", mac="AA:BB:CC:00:00:01", model="DS-1", ip="10.0.0.1",
    )
    repo.create_device(
        session.id, "switch", location.id, serial="S1", model="SW-1", ip="10.0.0.9",
    )
    repo.create_device(session.id, "ap", location.id, serial="AP1")
    return session.id


def test_keys_match_parse_baseline_roundtrip(tmp_path):
    """Строки архива повторяют набор ключей parse_baseline из экспорта."""
    db, repo = _repo(tmp_path)
    past_id = _populate(repo)
    output = export_session(repo, past_id, tmp_path / "past_report.xlsx")

    from_excel = parse_baseline(output)
    from_archive = session_baseline_rows(repo, past_id)
    assert from_excel and from_archive
    assert set(from_excel[0].keys()) == set(from_archive[0].keys())
    db.close()


def test_compare_against_archive_session(tmp_path):
    """Сверка текущей сессии с архивной: совпало/изменилось/новое/пропало."""
    db, repo = _repo(tmp_path)
    past_id = _populate(repo)
    current_id = _populate_current(repo)

    result = compare_baseline(
        session_baseline_rows(repo, past_id),
        repo.list_devices(current_id),
    )
    assert result.counts["matched"] == 1
    assert result.counts["changed"] == 1
    assert result.counts["new"] == 1
    assert result.counts["missing"] == 1
    changed, = [result.changed]
    assert changed[0][0].serial == "S1"
    db.close()


def test_titul_includes_report_settings(tmp_path):
    """Организация/договор из настроек печатаются в шапке титула."""
    db, repo = _repo(tmp_path)
    session_id = _populate(repo)
    repo.set_setting("report_org", "ООО «Ромашка»")
    repo.set_setting("report_contract", "Договор № 2026-17")

    output = export_session(repo, session_id, tmp_path / "report.xlsx")
    titul = load_workbook(output)["Титул"]
    label_texts = {
        titul.cell(row=row, column=1).value: titul.cell(row=row, column=2).value
        for row in range(1, titul.max_row + 1)
    }
    assert label_texts.get("Организация (подрядчик)") == "ООО «Ромашка»"
    assert label_texts.get("Договор") == "Договор № 2026-17"
    db.close()


def test_titul_empty_settings_left_out(tmp_path):
    """Без настроек титула строк «Организация» в заголовке нет."""
    db, repo = _repo(tmp_path)
    session_id = _populate(repo)
    output = export_session(repo, session_id, tmp_path / "report2.xlsx")
    titul = load_workbook(output)["Титул"]
    labels = [
        titul.cell(row=row, column=1).value for row in range(1, titul.max_row + 1)
    ]
    assert "Организация (подрядчик)" not in labels
    assert "Договор" not in labels
    db.close()

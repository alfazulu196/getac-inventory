# tests/test_sessions_contractor_reopen.py
"""Подрядчик в сессии (выбор на старте) и повторное открытие проекта."""
from __future__ import annotations

import pytest
from openpyxl import load_workbook

from inventory.core.db import Database
from inventory.core.export_excel import export_session
from inventory.core.repository import InventoryRepository


def _repo(tmp_path, name="test.db") -> InventoryRepository:
    db = Database(tmp_path / name)
    return InventoryRepository(db)


def test_create_session_keeps_contractor(tmp_path):
    repo = _repo(tmp_path)
    session = repo.create_session(
        "Объект", "Инспектор", name="Проект 1", contractor="ООО Полюс"
    )
    assert session.contractor == "ООО Полюс"
    loaded = repo.get_session(session.id)
    assert loaded is not None and loaded.contractor == "ООО Полюс"

    repo.finish_session(session.id)  # активной может быть только одна
    plain = repo.create_session("Объект 2", "Инспектор")
    assert plain.contractor is None


def test_contractor_list_remembered(tmp_path):
    repo = _repo(tmp_path)
    repo.remember_contractor("ООО Сигма")
    repo.remember_contractor("ООО Полюс")
    repo.remember_contractor("ООО Полюс")  # поднимает на верх списка
    assert repo.list_contractors() == ["ООО Полюс", "ООО Сигма"]
    repo.remember_contractor("")  # пустой ввод ничего не меняет
    assert repo.list_contractors() == ["ООО Полюс", "ООО Сигма"]


def test_reopen_completed_session(tmp_path):
    repo = _repo(tmp_path)
    first = repo.create_session("Фаза 1", "Иванов И.И.", name="Первый этап")
    location = repo.create_location(first.id, "1", "101", "Корпус А", "Ш-1")
    repo.create_device(first.id, "camera", location.id, serial="C1")
    repo.finish_session(first.id)

    # Пока активна другая сессия — открытие завершённой запрещено.
    second = repo.create_session("Фаза 2", "Иванов И.И.", name="Второй этап")
    with pytest.raises(ValueError):
        repo.reopen_session(first.id)

    # После завершения активной сессии проект открывается снова.
    repo.finish_session(second.id)
    reopened = repo.reopen_session(first.id)
    assert reopened.status == "active"
    assert reopened.finished_at is None

    # устройства добавляются в проект без ограничений
    repo.create_device(first.id, "switch", location.id, serial="S2")
    assert repo.count_devices(first.id) == 2

    repo.finish_session(first.id)
    assert repo.get_session(first.id).status == "completed"


def test_excel_titul_prefers_session_contractor(tmp_path):
    """Титульный лист: подрядчик сессии главнее глобальной настройки."""
    repo = _repo(tmp_path)
    session = repo.create_session(
        "Объект", "Инспектор", name="Проект", contractor="ООО Полюс"
    )
    repo.set_setting("report_org", "Старая организация")
    location = repo.create_location(session.id, "1", "101", "Корпус А", "Ш-1")
    repo.create_device(session.id, "camera", location.id, serial="C1")

    output = tmp_path / "report.xlsx"
    export_session(repo, session.id, output)
    ws = load_workbook(output)["Титул"]
    text = "\n".join(
        f"{ws.cell(row=row, column=1).value}={ws.cell(row=row, column=2).value}"
        for row in range(1, ws.max_row + 1)
    )
    assert "Организация (подрядчик)=ООО Полюс" in text

# tests/test_net_report.py
"""v1.6.10: ревизия сети в отчёте — фиксация в базе + лист «Сеть» в Excel."""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from openpyxl import load_workbook  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from inventory.core.db import Database  # noqa: E402
from inventory.core.export_excel import export_session  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.services.net_check import NetCheckResult  # noqa: E402
from inventory.ui.net_audit_dialog import (  # noqa: E402
    NetAuditDialog,
    _STATUS_COLOR,
    _STATUS_TEXT,
)

_APP: QApplication | None = None


def _qapp() -> QApplication:
    global _APP
    if QApplication.instance() is None:
        _APP = QApplication([])
    return QApplication.instance()


def _repo(tmp_path: Path) -> tuple[InventoryRepository, str, str, str]:
    """Сессия: камера с атрибутами (проверка мержа), свитч, ИБП без IP."""
    _qapp()  # QDialog без QApplication убивает процесс молча
    repo = InventoryRepository(Database(tmp_path / "report.db"))
    session = repo.create_session("Объект", "Инспектор")
    location = repo.create_location(session.id, "2", "204", "Корпус А", "Ш2")
    camera = repo.create_device(
        session.id, "camera", location.id,
        model="DS-2CD2143G0", ip="192.168.4.201", mac="A4:2B:B0:D1:E2:F3",
    )
    repo.update_device(camera.id, attributes_json={"vendor": "hikvision"})
    switch = repo.create_device(
        session.id, "switch", location.id,
        model="DES-1210", ip="192.168.4.10")
    ups = repo.create_device(session.id, "ups", location.id, model="AP7921")
    return repo, camera.id, switch.id, ups.id


def test_commit_writes_note_and_attributes(tmp_path: Path, monkeypatch) -> None:
    """Фиксация: note дополнен, net_audit записан, чужие атрибуты целы."""
    repo, camera_id, switch_id, ups_id = _repo(tmp_path)
    dialog = NetAuditDialog([], repo=repo)
    dialog._on_checked({
        "device_id": camera_id,
        "display_id": "CAM-001",
        "model": "DS-2CD2143G0",
        "ip": "192.168.4.201",
        "result": NetCheckResult(
            "alive", "в сети за 2 мс, MAC подтверждён",
            2, "a4:2b:b0:d1:e2:f3", {554: True, 80: False},
        ),
    })
    infos: list[str] = []
    import inventory.ui.net_audit_dialog as audit_mod

    monkeypatch.setattr(
        audit_mod.QMessageBox, "information",
        lambda *a, **k: infos.append(str(a[2])),
    )
    dialog._commit_results()

    assert infos and "1" in infos[0]
    device = repo.get_device(camera_id)
    assert device is not None
    # Примечание: дата + статус + причина.
    assert device.note and device.note.startswith("🌐")
    assert "🟢 в сети" in device.note
    assert "MAC подтверждён" in device.note
    # Служебное поле: полный замер, включая порты.
    assert device.attributes_json
    attrs = json.loads(device.attributes_json)
    assert attrs["net_audit"]["status"] == "alive"
    assert attrs["net_audit"]["ports"] == {"554": True, "80": False}
    assert attrs["net_audit"]["date"]
    # Чужие атрибуты не потерялись (мерж, а не подмена).
    assert attrs.get("vendor") == "hikvision"
    # Кнопка после фиксации гасится — фиксация одноразовая.
    assert not dialog.save_btn.isEnabled()


def test_commit_preserves_note_history(tmp_path: Path, monkeypatch) -> None:
    """Вторая ревизия дописывает строку в примечание, не затирая первую."""
    import inventory.ui.net_audit_dialog as audit_mod

    monkeypatch.setattr(
        audit_mod.QMessageBox, "information", lambda *a, **k: None)
    repo, camera_id, _switch, _ups = _repo(tmp_path)
    dialog = NetAuditDialog([], repo=repo)
    dialog._on_checked({
        "device_id": camera_id,
        "display_id": "CAM-001",
        "model": "DS-2CD2143G0",
        "ip": "192.168.4.201",
        "result": NetCheckResult(
            "alive", "в сети за 2 мс", 2, None, None),
    })
    dialog._commit_results()
    second = NetAuditDialog([], repo=repo)
    second._on_checked({
        "device_id": camera_id,
        "display_id": "CAM-001",
        "model": "DS-2CD2143G0",
        "ip": "192.168.4.201",
        "result": NetCheckResult("unreachable", "не отвечает на ping"),
    })
    second._commit_results()

    device = repo.get_device(camera_id)
    assert device is not None
    lines = (device.note or "").splitlines()
    assert len(lines) == 2  # история копится
    assert "🟢" in lines[0] and "🔴" in lines[1]
    attrs = json.loads(device.attributes_json or "{}")
    assert attrs["net_audit"]["status"] == "unreachable"  # последний замер


def test_commit_without_results_is_noop(tmp_path: Path) -> None:
    repo, camera_id, _switch, _ups = _repo(tmp_path)
    dialog = NetAuditDialog([], repo=repo)  # результатов ещё нет
    dialog._commit_results()  # без результатов диалог не показывается
    device = repo.get_device(camera_id)
    assert device is not None
    assert not device.note


def test_export_excel_net_sheet_with_colors(
    tmp_path: Path, monkeypatch
) -> None:
    """Лист «Сеть»: строки зафиксированных устройств, цвет по статусу."""
    import inventory.ui.net_audit_dialog as audit_mod

    monkeypatch.setattr(
        audit_mod.QMessageBox, "information", lambda *a, **k: None)
    repo, camera_id, switch_id, ups_id = _repo(tmp_path)
    for device_id, status in (
        (camera_id, "alive"),
        (switch_id, "unreachable"),
    ):
        dialog = NetAuditDialog([], repo=repo)
        dialog._on_checked({
            "device_id": device_id,
            "display_id": "X",
            "model": "М",
            "ip": "192.168.4.1",
            "result": NetCheckResult(status, "причина", None, None, None),
        })
        dialog._commit_results()

    out = export_session(repo, repo.list_sessions()[0].id, tmp_path / "out.xlsx")
    wb = load_workbook(out)
    assert "Сеть" in wb.sheetnames
    ws = wb["Сеть"]
    headers = [cell.value for cell in ws[1]]
    assert headers[:4] == ["ID", "Модель", "IP", "Статус"]
    ids = [row[0].value for row in ws.iter_rows(min_row=2)]
    assert len(ids) == 2  # ИБП без ревизии не попадает
    fills = [str(row[3].fill.fgColor.rgb).upper()
             for row in ws.iter_rows(min_row=2)]
    from inventory.core.export_excel import _tint  # noqa: PLC0415

    green = _tint(_STATUS_COLOR["alive"]).upper()
    red = _tint(_STATUS_COLOR["unreachable"]).upper()
    assert any(fill.endswith(green) for fill in fills)
    assert any(fill.endswith(red) for fill in fills)


def test_export_excel_without_audit_has_no_sheet(tmp_path: Path) -> None:
    """Ревизия не проводилась — листа «Сеть» в отчёте нет."""
    repo, _c, _s, _u = _repo(tmp_path)
    out = export_session(repo, repo.list_sessions()[0].id, tmp_path / "out.xlsx")
    wb = load_workbook(out)
    assert "Сеть" not in wb.sheetnames


def test_status_text_and_color_maps_consistent() -> None:
    """Каждому статусу с цветом соответствует текст — и наоборот."""
    assert set(_STATUS_COLOR) <= set(_STATUS_TEXT)
    for status in _STATUS_COLOR:
        assert _STATUS_TEXT[status]

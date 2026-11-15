# tests/test_net_audit.py
"""Ревизия сети сессии (v1.6.9): батч-воркер, сводка диалога, кнопка списка."""
from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402

from inventory.core.db import Database  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.services.net_check import NetCheckResult  # noqa: E402
from inventory.ui import device_list as device_list_mod  # noqa: E402
from inventory.ui.net_audit_dialog import NetAuditDialog  # noqa: E402
from inventory.ui.net_workers import NetAuditWorker  # noqa: E402

_APP: QApplication | None = None


def _qapp() -> QApplication:
    global _APP
    if QApplication.instance() is None:
        _APP = QApplication([])
    return QApplication.instance()


def _pump_until(dialog: NetAuditDialog, seconds: float = 5.0) -> None:
    """Крутить event loop, пока _on_finished не погасит воркер."""
    app = QApplication.instance()
    deadline = time.time() + seconds
    while time.time() < deadline and dialog._worker is not None:
        app.processEvents()
        time.sleep(0.02)


_ITEMS = [
    ("id-1", "CAM-001", "DS-2CD2143G0", "192.168.4.201", "a4:2b:b0:d1:e2:f3"),
    ("id-2", "SW-001", "DES-1210", "192.168.4.10", ""),
    ("id-3", "NVR-001", "DS-7608", "", ""),
]


def test_audit_worker_reports_each_item(monkeypatch) -> None:
    """Воркер отдаёт по одному checked на элемент + прогресс."""
    _qapp()
    import inventory.ui.net_workers as net_workers

    canned = [
        NetCheckResult("alive", "в сети за 2 мс", 2, "a4:2b:b0:d1:e2:f3"),
        NetCheckResult("unreachable", "не отвечает на ping"),
        NetCheckResult("no_ip", "IP не записан — заполните поле IP-адрес"),
    ]
    calls: list[tuple[str, str]] = []

    def fake_check(ip: str, mac: str) -> NetCheckResult:
        calls.append((ip, mac))
        return canned[len(calls) - 1]

    monkeypatch.setattr(net_workers, "check_device", fake_check)
    worker = NetAuditWorker(_ITEMS)
    got: list[dict] = []
    progress: list[tuple[int, int, str]] = []
    worker.checked.connect(got.append)
    worker.progress.connect(lambda d, t, label: progress.append((d, t, label)))
    worker.start()
    worker.wait(5000)
    # Сигналы из потока — в очереди: разгребаем event loop'ом.
    app = QApplication.instance()
    deadline = time.time() + 3
    while time.time() < deadline and len(got) < 3:
        app.processEvents()
        time.sleep(0.02)
    assert len(got) == 3
    assert [row["display_id"] for row in got] == ["CAM-001", "SW-001", "NVR-001"]
    assert got[0]["result"].status == "alive"
    assert got[1]["result"].status == "unreachable"
    assert got[2]["result"].status == "no_ip"
    assert progress[-1] == (3, 3, "NVR-001")


def test_audit_dialog_summary_and_table() -> None:
    """Диалог: строки по статусам, сводка считает категории.

    Элементы без IP / с кривым IP не пингуют сеть — результаты детерминированы.
    """
    _qapp()
    items = [
        ("id-1", "CAM-001", "М1", "", ""),            # no_ip
        ("id-2", "SW-001", "М2", "", ""),             # no_ip
        ("id-3", "NVR-001", "М3", "999.1.2.3", ""),   # bad_ip
    ]
    dialog = NetAuditDialog(items)
    _pump_until(dialog)
    assert dialog._worker is None
    assert dialog.table.rowCount() == 3
    assert "Проверено: 3" in dialog.summary_label.text()
    assert "⚪ без IP/кривой: 3" in dialog.summary_label.text()
    assert "🟢 в сети: 0" in dialog.summary_label.text()


def test_list_button_guard_no_ip(tmp_path: Path, monkeypatch) -> None:
    """Кнопка ревизии без единого IP в сессии — подсказка, диалог не создаётся."""
    _qapp()
    repo = InventoryRepository(Database(tmp_path / "audit.db"))
    session = repo.create_session("Объект", "Инспектор")
    location = repo.create_location(session.id, "2", "204", "Корпус А", "Ш2")
    repo.create_device(session.id, "camera", location.id, model="DS-2CD2143G0")

    shown: list[str] = []
    monkeypatch.setattr(
        device_list_mod.QMessageBox, "information",
        lambda *a, **k: shown.append(k.get("text", a[2] if len(a) > 2 else "")),
    )
    opened: list[object] = []
    import inventory.ui.net_audit_dialog as audit_mod

    monkeypatch.setattr(
        audit_mod.NetAuditDialog, "__init__",
        lambda self, items, **kwargs: opened.append(self),
    )
    dialog = device_list_mod.DeviceListDialog(
        repo, session.id,
        {"photos": tmp_path / "photos", "logs": tmp_path / "logs"},
        current_location=location,
    )
    dialog.net_audit_btn.click()
    assert not opened
    assert shown and "не записан IP" in shown[0]


def test_list_button_opens_audit(tmp_path: Path, monkeypatch) -> None:
    """Кнопка ревизии с IP в сессии — открывает диалог со всеми устройствами."""
    _qapp()
    repo = InventoryRepository(Database(tmp_path / "audit2.db"))
    session = repo.create_session("Объект", "Инспектор")
    location = repo.create_location(session.id, "2", "204", "Корпус А", "Ш2")
    repo.create_device(
        session.id, "camera", location.id,
        model="DS-2CD2143G0", ip="192.168.4.201", mac="A4:2B:B0:D1:E2:F3")
    repo.create_device(session.id, "ups", location.id, model="AP7921")

    import inventory.ui.net_audit_dialog as audit_mod

    opened: list[tuple[NetAuditDialog, list]] = []
    orig_init = audit_mod.NetAuditDialog.__init__

    def spy_init(self, items, **kwargs):
        opened.append((self, list(items)))
        orig_init(self, items, **kwargs)

    monkeypatch.setattr(audit_mod.NetAuditDialog, "__init__", spy_init)
    monkeypatch.setattr(
        audit_mod.NetAuditDialog, "exec",
        lambda self: QDialog.DialogCode.Accepted,
    )
    dialog = device_list_mod.DeviceListDialog(
        repo, session.id,
        {"photos": tmp_path / "photos", "logs": tmp_path / "logs"},
        current_location=location,
    )
    dialog.net_audit_btn.click()
    assert len(opened) == 1
    audit_dialog, items = opened[0]
    assert len(items) == 2  # оба устройства, даже ИБП без IP
    audit_dialog._worker.wait(5000)  # реальный ping до ~1,2 с — ждём
    audit_dialog.reject()

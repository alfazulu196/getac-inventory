# tests/test_rack_phone.py
"""Фото стойки с телефона: режим сервера rack и приём в открытом разборе."""
from __future__ import annotations

import os
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

import inventory.ui.rack_dialog as rack_dialog  # noqa: E402
from inventory.core.db import Database  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.ui.rack_dialog import RackDialog  # noqa: E402

_APP: QApplication | None = None


def _qapp() -> QApplication:
    global _APP
    if QApplication.instance() is None:
        _APP = QApplication([])
    return QApplication.instance()


class _FakeServer:
    """Минимальный контракт PhotoServer, который использует диалог."""

    def __init__(self) -> None:
        self.context: tuple[str, str, str] | None = None
        self.urls = ["http://192.168.0.2:8790/tok"]
        self.started = 0

    def set_context(self, device: str, location: str,
                    mode: str = "device") -> None:
        self.context = (device, location, mode)

    def start(self) -> list[str]:
        self.started += 1
        return self.urls


def _dialog(tmp_path: Path, monkeypatch, server) -> tuple[RackDialog, object]:
    _qapp()
    repo = InventoryRepository(Database(tmp_path / "phone.db"))
    session = repo.create_session("Объект", "Инспектор")
    location = repo.create_location(session.id, "1", "101", "Корпус А", "Ш-1")
    monkeypatch.setattr(rack_dialog, "ollama_status", lambda: (False, "нет ИИ"))
    monkeypatch.setattr(rack_dialog.QMessageBox, "information",
                        lambda *a, **k: None)
    monkeypatch.setattr(RackDialog, "_start_warmup", lambda self: None)
    monkeypatch.setattr(RackDialog, "_another_shot", lambda self: None)
    dialog = RackDialog(repo, session.id,
                        {"photos": tmp_path / "photos",
                         "logs": tmp_path / "logs"},
                        location=location, photo_server=server)
    return dialog, location


def test_dialog_switches_server_to_rack_mode(tmp_path, monkeypatch) -> None:
    server = _FakeServer()
    dialog, location = _dialog(tmp_path, monkeypatch, server)
    assert server.context == ("Стойка", location.label(), "rack")
    assert dialog.phone_btn.isEnabled() is False  # ИИ недоступен — фото некуда
    dialog.close()


def test_phone_photo_goes_straight_to_analysis(tmp_path, monkeypatch) -> None:
    server = _FakeServer()
    dialog, _location = _dialog(tmp_path, monkeypatch, server)
    seen: list[str] = []
    monkeypatch.setattr(RackDialog, "_start_analysis",
                        lambda self, path: seen.append(path))
    dialog.add_photo_from_phone("C:/photos/rack_1.jpg")
    assert seen == ["C:/photos/rack_1.jpg"]
    assert "разбираю" in dialog.status_label.text()
    dialog.close()


def test_phone_link_restores_rack_mode(tmp_path, monkeypatch) -> None:
    server = _FakeServer()
    dialog, location = _dialog(tmp_path, monkeypatch, server)
    opened: list[object] = []

    class _FakeLink:
        def __init__(self, srv, parent=None) -> None:
            opened.append((srv, parent))

        def exec(self) -> int:
            server.context = ("CAM-1", "карточка", "device")  # сменили извне
            return 0

    monkeypatch.setattr(rack_dialog, "PhoneLinkDialog", _FakeLink)
    dialog._open_phone_link()
    assert opened and opened[0][0] is server
    assert server.context == ("Стойка", location.label(), "rack")
    assert server.started == 0  # сервер уже запущен — не перезапускаем
    dialog.close()

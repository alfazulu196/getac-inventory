# tests/test_phone_link_dialog.py
"""Диалог телефона: подсказка зависит от режима (устройство/стойка)."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

from inventory.services.photo_server import PhotoServer  # noqa: E402
from inventory.ui.phone_link_dialog import (  # noqa: E402
    PhoneLinkDialog,
    _hint_text,
)

_APP: QApplication | None = None


def _qapp() -> QApplication:
    global _APP
    if QApplication.instance() is None:
        _APP = QApplication([])
    return QApplication.instance()


def test_hint_for_rack_mode_mentions_direct_analysis() -> None:
    assert "разбор стойки" in _hint_text("rack")
    assert "активному устройству" in _hint_text("device")


def test_dialog_builds_in_rack_mode(tmp_path: Path) -> None:
    _qapp()
    server = PhotoServer(tmp_path / "photos")
    server.set_context("Стойка", "Корпус А · 101", mode="rack")
    dialog = PhoneLinkDialog(server)
    texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert any("разбор стойки" in text for text in texts)
    dialog.close()

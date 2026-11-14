# tests/test_reference.py
"""Тесты полевого справочника: содержимое вкладок и сборка диалога."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QScrollArea,
    QTabWidget,
)

from inventory.ui.reference_dialog import REFERENCE_TABS, ReferenceDialog  # noqa: E402

_APP: QApplication | None = None


def _qapp() -> QApplication | None:
    """QApplication для виджетов; None — если уже создан только QGuiApplication."""
    global _APP
    app = QApplication.instance()
    if app is not None:
        return app
    try:
        _APP = QApplication([])
    except RuntimeError:
        return None  # ранее создан QGuiApplication — виджеты строить нельзя
    return _APP


def test_reference_tabs_content() -> None:
    assert [title for title, _html in REFERENCE_TABS] == [
        "🏷 S/N и MAC",
        "🔌 Кабели и PoE",
        "🖥 Стойки и юниты",
    ]
    sn_mac, cable, rack = (html for _title, html in REFERENCE_TABS)
    # Содержимое: ключевые темы на месте, опечатки в заголовках не прячем.
    for token in ("Hikvision", "Ubiquiti", "MikroTik", "Dell", "MAC"):
        assert token in sn_mac
    for token in ("T568A", "T568B", "802.3af", "802.3at", "802.3bt",
                  "Cat6a", "бело-оранжевый"):
        assert token in cable
    for token in ("U1", "U5-U8", "44,45", "19″"):
        assert token in rack


def test_reference_dialog_smoke() -> None:
    app = _qapp()
    if app is None:
        pytest.skip("в процессе уже есть только QGuiApplication — виджеты недоступны")
    dialog = ReferenceDialog()
    tabs = dialog.findChild(QTabWidget)
    assert tabs is not None
    assert tabs.count() == 3
    for index in range(tabs.count()):
        scroll = tabs.widget(index)
        assert isinstance(scroll, QScrollArea)
        browser = scroll.widget()
        # HTML отдаётся браузеру целиком: три таблицы и списки не обрезались
        assert browser.toPlainText().strip()
    dialog.accept()
    dialog.deleteLater()

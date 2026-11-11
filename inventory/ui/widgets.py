# inventory/ui/widgets.py
"""Общие элементы интерфейса и стили."""
from __future__ import annotations

import sys
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

APP_STYLE = """
QWidget {
    background-color: #1e2430;
    color: #ecf0f1;
    font-family: "Segoe UI", "Segoe UI Emoji", "Arial", sans-serif;
    font-size: 16px;
}
QPushButton {
    border-radius: 10px;
    padding: 8px 14px;
    border: none;
}
QPushButton:disabled {
    background-color: #3a4352;
    color: #8a94a6;
}
QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #2b3442;
    border: 1px solid #3f4b5e;
    border-radius: 8px;
    padding: 8px;
    selection-background-color: #2980b9;
}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus {
    border: 1px solid #3498db;
}
QComboBox QAbstractItemView {
    background-color: #2b3442;
    selection-background-color: #2980b9;
}
QTableWidget, QListWidget, QTreeWidget {
    background-color: #232b38;
    alternate-background-color: #283141;
    gridline-color: #3f4b5e;
    border: 1px solid #3f4b5e;
    border-radius: 8px;
}
QTableWidget::item {
    padding: 6px;
}
QTableWidget {
    alternate-background-color: #283141;
}
QHeaderView::section {
    background-color: #2f3a4d;
    color: #ecf0f1;
    padding: 8px;
    border: none;
    font-weight: bold;
}
QListWidget::item {
    padding: 8px;
    border-bottom: 1px solid #2f3a4d;
}
QListWidget::item:selected {
    background-color: #2980b9;
}
QTabWidget::pane {
    border: 1px solid #3f4b5e;
    border-radius: 8px;
}
QTabBar::tab {
    background-color: #2b3442;
    padding: 10px 18px;
    margin-right: 2px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}
QTabBar::tab:selected {
    background-color: #2980b9;
}
QGroupBox {
    border: 1px solid #3f4b5e;
    border-radius: 10px;
    margin-top: 14px;
    padding-top: 10px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #9fb3c8;
}
QScrollBar:vertical {
    background: #232b38;
    width: 14px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #3f4b5e;
    min-height: 30px;
    border-radius: 7px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
"""


def apply_app_style(app: QApplication) -> None:
    app.setStyleSheet(APP_STYLE)
    font = QFont("Segoe UI", 10)
    app.setFont(font)


def big_button(
    text: str,
    color: str = "#2980b9",
    *,
    height: int = 64,
    font_size: int = 18,
    parent: QWidget | None = None,
) -> QPushButton:
    button = QPushButton(text, parent)
    pressed = QColor(color).darker(125).name()
    button.setStyleSheet(
        f"""
        QPushButton {{
            background-color: {color};
            color: white;
            font-size: {font_size}px;
            font-weight: bold;
            border-radius: 12px;
            min-height: {height}px;
            padding: 8px 12px;
        }}
        QPushButton:pressed {{
            background-color: {pressed};
        }}
        QPushButton:disabled {{
            background-color: #3a4352;
            color: #8a94a6;
        }}
        """
    )
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def secondary_button(text: str, height: int = 48, font_size: int = 15) -> QPushButton:
    button = big_button(text, "#3b4a5f", height=height, font_size=font_size)
    return button


class ToastLabel(QLabel):
    """Всплывающее сообщение внизу экрана."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setVisible(False)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, kind: str = "info", timeout: int = 4000) -> None:
        colors = {
            "info": ("#1f3a5f", "#d6eaf8"),
            "success": ("#1e4d2b", "#d5f5e3"),
            "warning": ("#5a4214", "#fdebd0"),
            "error": ("#5a1f1f", "#fadbd8"),
        }
        background, foreground = colors.get(kind, colors["info"])
        self.setStyleSheet(
            f"background-color: {background}; color: {foreground}; "
            "border-radius: 10px; padding: 10px 16px; font-size: 16px; font-weight: bold;"
        )
        self.setText(text)
        self.setVisible(True)
        self._timer.start(timeout)


def beep_ok() -> None:
    _beep([(2000, 90), (2500, 90), (3000, 120)])


def beep_error() -> None:
    _beep([(500, 250), (350, 350)])


def beep_scan() -> None:
    _beep([(3000, 70)])


def _beep(tones: list[tuple[int, int]]) -> None:
    if sys.platform != "win32":
        QApplication.beep()
        return
    try:
        import winsound

        for frequency, duration in tones:
            winsound.Beep(frequency, duration)
    except Exception:  # noqa: BLE001
        QApplication.beep()


def format_datetime(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return value

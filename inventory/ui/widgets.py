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

# Высококонтрастная «полевая» тема (v1.6.8): чёрный фон, жёлтые поля,
# жирные шрифты — читаемость на ярком солнце на экране Getac V110.
FIELD_STYLE = """
QWidget {
    background-color: #050505;
    color: #ffffff;
    font-family: "Segoe UI", "Segoe UI Emoji", "Arial", sans-serif;
    font-size: 18px;
    font-weight: 600;
}
QLabel, QCheckBox, QRadioButton {
    color: #ffffff;
    font-weight: 600;
}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {
    background-color: #050505;
    color: #ffe066;
    border: 3px solid #ffe066;
    border-radius: 6px;
    font-weight: 700;
    selection-background-color: #ffe066;
    selection-color: #000000;
}
QComboBox {
    background-color: #050505;
    color: #ffe066;
    border: 3px solid #ffe066;
    border-radius: 6px;
    font-weight: 700;
}
QComboBox QAbstractItemView {
    background-color: #050505;
    color: #ffe066;
    border: 3px solid #ffe066;
    selection-background-color: #ffe066;
    selection-color: #000000;
}
QTableWidget {
    background-color: #050505;
    color: #ffffff;
    gridline-color: #555555;
    border: 2px solid #555555;
    font-weight: 600;
}
QHeaderView::section {
    background-color: #111111;
    color: #ffe066;
    border: 1px solid #555555;
    font-weight: 700;
}
QTabWidget::pane { border: 2px solid #555555; }
QTabBar::tab {
    background: #111111;
    color: #bbbbbb;
    font-weight: 700;
}
QTabBar::tab:selected {
    background: #ffe066;
    color: #000000;
}
QGroupBox::title { color: #ffe066; }
QScrollBar:vertical { background: #050505; width: 18px; }
QScrollBar::handle:vertical { background: #ffe066; min-height: 40px; }
QScrollBar:horizontal { background: #050505; height: 18px; }
QScrollBar::handle:horizontal { background: #ffe066; min-width: 40px; }
"""


def apply_app_style(app: QApplication, *, field_mode: bool = False) -> None:
    """Глобальный стиль. field_mode — высококонтрастная «полевая» тема.

    Тема для съёмки под солнцем: чёрный фон, жёлтые поля ввода, жирный
    шрифт на 2 пункта крупнее. Приглушённые inline-подписи диалогов
    перекрашиваются сразу (ensure_field_restyle), а диалоги, созданные
    ПОЗЖЕ, подхватывают перекраску через ensure_field_restyle() в конце
    своего __init__.
    """
    global _FIELD_MODE
    _FIELD_MODE = bool(field_mode)
    app.setStyleSheet(FIELD_STYLE if _FIELD_MODE else APP_STYLE)
    font = QFont("Segoe UI", 12 if _FIELD_MODE else 10)
    if _FIELD_MODE:
        font.setBold(True)
    app.setFont(font)
    ensure_field_restyle()


# Приглушённые цвета inline-подписей → контрастные эквиваленты полевой темы.
_INLINE_SWAP = {
    "#9fb3c8": "#ffffff",
    "#95a5a6": "#dddddd",
    "#f1c40f": "#ffe066",
    "#111820": "#050505",
}
_FIELD_MODE = False


def ensure_field_restyle(app: QApplication | None = None) -> None:
    """Перестроить стили под текущий режим темы.

    Кнопки-фабрики (field_button) перестраиваются в ОБЕ стороны —
    при выключении темы возвращают обычный вид. Прочие inline-стили
    перекрашиваются только в полевом режиме (приглушённые цвета → яркие).
    Диалоги вызывают её в конце __init__, чтобы серые подписи не остались
    приглушёнными на чёрном фоне.
    """
    app = app or QApplication.instance()
    if app is None:
        return
    for widget in app.allWidgets():
        if widget.property("field_button"):
            params = widget.property("field_params")
            if params:
                color, height, font_size = params
                pressed = QColor(color).darker(125).name()
                widget.setStyleSheet(
                    _button_style(
                        color, pressed, height, font_size, field=_FIELD_MODE
                    )
                )
                continue
        if not _FIELD_MODE:
            continue
        style = widget.styleSheet()
        if not style:
            continue
        restyled = style
        for muted, bright in _INLINE_SWAP.items():
            restyled = restyled.replace(muted, bright)
        if restyled != style:
            widget.setStyleSheet(restyled)


def _button_style(
    color: str,
    pressed: str,
    height: int,
    font_size: int,
    *,
    field: bool = False,
) -> str:
    """Стиль кнопки; field — полевая тема (жёлтая рамка, контрастный текст)."""
    text_color = "white"
    extra_border = ""
    disabled_bg, disabled_fg = "#3a4352", "#8a94a6"
    if field:
        # Тёмная кнопка на чёрном фоне → жёлтый текст; цветная → белый.
        if QColor(color).lightness() < 100:
            text_color = "#ffe066"
        extra_border = "border: 3px solid #ffe066;"
        disabled_bg, disabled_fg = "#2a2a2a", "#808080"
    return f"""
    QPushButton {{
        background-color: {color};
        color: {text_color};
        font-size: {font_size}px;
        font-weight: bold;
        {extra_border}
        border-radius: 12px;
        min-height: {height}px;
        padding: 8px 12px;
    }}
    QPushButton:pressed {{
        background-color: {pressed};
    }}
    QPushButton:disabled {{
        background-color: {disabled_bg};
        color: {disabled_fg};
    }}
    """


def big_button(
    text: str,
    color: str = "#2980b9",
    *,
    height: int = 64,
    font_size: int = 18,
    parent: QWidget | None = None,
) -> QPushButton:
    button = QPushButton(text, parent)
    field = _FIELD_MODE
    if field and color == "#3b4a5f":  # вторичная → чёрная с жёлтым акцентом
        color = "#0b0b0b"
    pressed = QColor(color).darker(125).name()
    # Свойства нужны ensure_field_restyle: перестилизовать кнопку целиком,
    # когда тему переключают на лету (стиль строится из параметров).
    button.setProperty("field_button", True)
    button.setProperty("field_params", (color, height, font_size))
    button.setStyleSheet(
        _button_style(color, pressed, height, font_size, field=field)
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

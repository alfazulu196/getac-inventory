# inventory/ui/preflight_dialog.py
"""Экран предстартовой проверки."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from inventory.config import AppConfig
from inventory.core.models import Session
from inventory.services.preflight import CheckResult, PreflightService
from inventory.ui.widgets import big_button, secondary_button, format_datetime


STATUS_LABELS = {"ok": "OK", "warning": "Внимание", "error": "Ошибка"}
STATUS_COLORS = {"ok": "#2ecc71", "warning": "#f1c40f", "error": "#e74c3c"}


class PreflightDialog(QDialog):
    def __init__(
        self,
        config: AppConfig,
        paths: dict[str, Path],
        session: Session,
        parent=None,
    ):
        super().__init__(parent)
        self.config = config
        self.paths = paths
        self.session = session
        self.service = PreflightService(config, paths)
        self.checks: list[CheckResult] = []
        self.setWindowTitle("Проверка готовности")
        self.setMinimumSize(980, 620)
        self._build_ui()
        self.run_checks()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("Проверка готовности к обходу")
        title.setStyleSheet("font-size: 26px; font-weight: bold;")
        layout.addWidget(title)

        info = QLabel(
            f"<b>{self.session.object_name}</b> · инспектор: {self.session.inspector}<br>"
            f"Сессия начата: {format_datetime(self.session.started_at)}"
        )
        info.setStyleSheet("color: #9fb3c8; font-size: 15px;")
        layout.addWidget(info)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Проверка", "Статус", "Сообщение"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(1, 110)
        layout.addWidget(self.table, 1)

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(self.summary_label)

        buttons = QHBoxLayout()
        self.repeat_btn = secondary_button("↻ Повторить проверку", height=54, font_size=16)
        self.repeat_btn.clicked.connect(self.run_checks)
        buttons.addWidget(self.repeat_btn)

        self.cancel_btn = secondary_button("Отмена", height=54, font_size=16)
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_btn)
        buttons.addStretch(1)

        self.start_btn = big_button("▶ Начать обход", "#2e7d32", height=60, font_size=20)
        self.start_btn.clicked.connect(self.accept)
        buttons.addWidget(self.start_btn, 2)
        layout.addLayout(buttons)

    def run_checks(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.checks = self.service.run_all()
        finally:
            QApplication.restoreOverrideCursor()
        self.table.setRowCount(len(self.checks))
        for row, check in enumerate(self.checks):
            title_item = QTableWidgetItem(check.title)
            status_item = QTableWidgetItem(STATUS_LABELS.get(check.status, check.status))
            message_item = QTableWidgetItem(check.message)
            status_item.setForeground(QColor(STATUS_COLORS.get(check.status, "#ecf0f1")))
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if check.status == "error":
                status_item.setFont(self.font())
            self.table.setItem(row, 0, title_item)
            self.table.setItem(row, 1, status_item)
            self.table.setItem(row, 2, message_item)
        counts = PreflightService.summary(self.checks)
        can_start = PreflightService.can_start(self.checks)
        self.summary_label.setText(
            f"OK: {counts['ok']} · предупреждений: {counts['warning']} · ошибок: {counts['error']}"
        )
        self.summary_label.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {'#2ecc71' if can_start else '#e74c3c'};"
        )
        self.start_btn.setEnabled(can_start)
        self.start_btn.setToolTip(
            "" if can_start else "Есть критическая ошибка. Исправьте её и повторите проверку."
        )

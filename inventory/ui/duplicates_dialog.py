# inventory/ui/duplicates_dialog.py
"""Просмотр всех подозрительных пар (одинаковый S/N/MAC) в сессии."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from inventory.config import device_type_name, status_name
from inventory.core.duplicates import find_duplicate_groups
from inventory.core.models import Device
from inventory.core.repository import InventoryRepository
from inventory.services.voice import VoiceService
from inventory.ui.device_card import DeviceCardDialog
from inventory.ui.widgets import big_button, secondary_button


class DuplicatesDialog(QDialog):
    def __init__(
        self,
        repo: InventoryRepository,
        session_id: str,
        paths: dict[str, Path],
        voice: VoiceService | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.session_id = session_id
        self.paths = paths
        self.voice = voice
        self._rows: list[tuple[int, str, Device]] = []  # (номер группы, чем совпало, устройство)
        self.setWindowTitle("Дубликаты")
        self.setMinimumSize(1080, 620)
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.title = QLabel("Дубликаты в сессии")
        self.title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(self.title)

        hint = QLabel(
            "Устройства с одинаковым S/N или MAC. Двойной клик — открыть карточку;\n"
            "можно исправить или удалить лишнюю запись — список обновится."
        )
        hint.setStyleSheet("color: #9fb3c8;")
        layout.addWidget(hint)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Группа", "ID", "Тип", "Модель", "S/N", "MAC", "Локация", "Совпадение"]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 80)
        self.table.doubleClicked.connect(self._open_selected)
        layout.addWidget(self.table, 1)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color: #9fb3c8;")
        layout.addWidget(self.count_label)

        buttons = QHBoxLayout()
        self.open_btn = secondary_button("✎ Открыть карточку", height=54, font_size=16)
        self.open_btn.clicked.connect(self._open_selected)
        buttons.addWidget(self.open_btn)
        buttons.addStretch(1)
        self.close_btn = big_button("Закрыть", "#3b4a5f", height=54, font_size=17)
        self.close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.close_btn, 1)
        layout.addLayout(buttons)

    def refresh(self) -> None:
        groups = find_duplicate_groups(self.repo, self.session_id)
        self._rows = [
            (no, group.label(), device)
            for no, group in enumerate(groups, start=1)
            for device in group.devices
        ]
        self.table.setRowCount(len(self._rows))
        for row, (no, matched, device) in enumerate(self._rows):
            values = [
                f"№{no}",
                device.display_id,
                device_type_name(device.device_type),
                device.model or "",
                device.serial or "",
                device.mac or "",
                device.location.label() if device.location else "",
                matched,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 1:
                    item.setData(Qt.ItemDataRole.UserRole, device.id)
                self.table.setItem(row, column, item)
        if groups:
            devices_total = sum(len(group.devices) for group in groups)
            self.title.setText(f"Дубликаты в сессии: {len(groups)}")
            self.count_label.setText(
                f"Групп: {len(groups)}, устройств в них: {devices_total}"
            )
            self.open_btn.setEnabled(True)
        else:
            self.title.setText("Дубликатов нет")
            self.count_label.setText("")
            self.open_btn.setEnabled(False)

    def _selected_device(self) -> Device | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            return None
        return self._rows[row][2]

    def _open_selected(self) -> None:
        device = self._selected_device()
        if not device:
            QMessageBox.information(self, "Дубликаты", "Выберите устройство в списке")
            return
        dialog = DeviceCardDialog(
            self.repo,
            self.session_id,
            self.paths,
            self.voice,
            device=device,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

# inventory/ui/device_list.py
"""Список устройств с поиском, фильтрами и редактированием."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from inventory.config import DEVICE_TYPES, STATUSES, device_type_name, status_name
from inventory.core.models import Device, Location
from inventory.core.repository import InventoryRepository
from inventory.services.voice import VoiceService
from inventory.ui.device_card import DeviceCardDialog
from inventory.ui.widgets import big_button, secondary_button


class DeviceListDialog(QDialog):
    devices_changed = pyqtSignal()

    def __init__(
        self,
        repo: InventoryRepository,
        session_id: str,
        paths: dict[str, Path],
        voice: VoiceService | None = None,
        current_location: Location | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.session_id = session_id
        self.paths = paths
        self.voice = voice
        self.current_location = current_location
        self._devices: list[Device] = []
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.refresh)
        self.setWindowTitle("Устройства")
        self.setMinimumSize(1100, 650)
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Устройства объекта")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        filters = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Поиск: ID, S/N, MAC, IP, модель, примечание...")
        self.search_edit.textChanged.connect(lambda: self._search_timer.start())
        filters.addWidget(self.search_edit, 3)

        self.type_combo = QComboBox()
        self.type_combo.addItem("Все типы", None)
        for device_type in DEVICE_TYPES:
            self.type_combo.addItem(device_type.name, device_type.code)
        self.type_combo.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.type_combo, 1)

        self.status_combo = QComboBox()
        self.status_combo.addItem("Все статусы", None)
        for status in STATUSES:
            self.status_combo.addItem(status["name"], status["code"])
        self.status_combo.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.status_combo, 1)
        layout.addLayout(filters)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Тип", "Модель", "S/N", "MAC", "Локация", "Статус", "Проверено"]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 100)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 200)
        self.table.setColumnWidth(3, 160)
        self.table.setColumnWidth(4, 170)
        self.table.setColumnWidth(5, 260)
        self.table.doubleClicked.connect(self._open_selected)
        layout.addWidget(self.table, 1)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color: #9fb3c8;")
        layout.addWidget(self.count_label)

        buttons = QHBoxLayout()
        self.add_btn = big_button("＋ Добавить", "#2980b9", height=54, font_size=17)
        self.add_btn.clicked.connect(self._add_device)
        buttons.addWidget(self.add_btn)

        self.open_btn = secondary_button("✎ Открыть", height=54, font_size=16)
        self.open_btn.clicked.connect(self._open_selected)
        buttons.addWidget(self.open_btn)

        self.delete_btn = secondary_button("🗑 Удалить", height=54, font_size=16)
        self.delete_btn.clicked.connect(self._delete_selected)
        buttons.addWidget(self.delete_btn)

        self.tags_btn = secondary_button("🏷 Бирки QR", height=54, font_size=16)
        self.tags_btn.clicked.connect(self._print_tags)
        buttons.addWidget(self.tags_btn)
        buttons.addStretch(1)

        self.close_btn = big_button("Закрыть", "#3b4a5f", height=54, font_size=17)
        self.close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.close_btn, 1)
        layout.addLayout(buttons)

    def refresh(self) -> None:
        query = self.search_edit.text().strip() or None
        device_type = self.type_combo.currentData()
        status = self.status_combo.currentData()
        self._devices = self.repo.list_devices(
            self.session_id,
            query=query,
            device_type=device_type,
            status=status,
        )
        self.table.setRowCount(len(self._devices))
        for row, device in enumerate(self._devices):
            values = [
                device.display_id,
                device_type_name(device.device_type),
                device.model or "",
                device.serial or "",
                device.mac or "",
                device.location.label() if device.location else "",
                status_name(device.status),
                "Да" if device.is_verified else "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, device.id)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        self.count_label.setText(f"Показано: {len(self._devices)}")

    def _selected_device(self) -> Device | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._devices):
            return None
        return self._devices[row]

    def _open_selected(self) -> None:
        device = self._selected_device()
        if not device:
            QMessageBox.information(self, "Устройства", "Выберите устройство в списке")
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
            self.devices_changed.emit()

    def _add_device(self) -> None:
        dialog = DeviceCardDialog(
            self.repo,
            self.session_id,
            self.paths,
            self.voice,
            location=self.current_location,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.devices_changed.emit()

    def _delete_selected(self) -> None:
        device = self._selected_device()
        if not device:
            QMessageBox.information(self, "Устройства", "Выберите устройство в списке")
            return
        reply = QMessageBox.question(
            self,
            "Удаление",
            f"Удалить устройство {device.display_id}?\n"
            "Запись будет скрыта, но останется в журнале.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.repo.soft_delete_device(device.id)
            self.refresh()
            self.devices_changed.emit()

    def _print_tags(self) -> None:
        from inventory.ui.tags_dialog import TagsDialog  # noqa: PLC0415

        dialog = TagsDialog(self.repo, self.session_id, self.paths, self)
        dialog.exec()

# inventory/ui/location_dialog.py
"""Выбор или создание локации."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
)

from inventory.core.models import Location
from inventory.core.repository import InventoryRepository
from inventory.ui.widgets import big_button, secondary_button


class LocationDialog(QDialog):
    def __init__(
        self,
        repo: InventoryRepository,
        session_id: str,
        current_location: Location | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.session_id = session_id
        self.location: Location | None = None
        self._recent: list[Location] = []
        self.setWindowTitle("Место установки")
        self.setMinimumSize(820, 620)
        self._build_ui()
        self._load_recent()
        if current_location:
            self._fill(current_location)

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        # Слева — недавние локации.
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Недавние локации</b>"))
        self.recent_list = QListWidget()
        self.recent_list.setMinimumWidth(340)
        self.recent_list.itemDoubleClicked.connect(self._recent_activated)
        left.addWidget(self.recent_list, 1)
        hint = QLabel("Двойной щелчок — выбрать локацию")
        hint.setStyleSheet("color: #9fb3c8; font-size: 13px;")
        left.addWidget(hint)
        layout.addLayout(left, 1)

        # Справа — форма.
        right = QVBoxLayout()
        form_box = QGroupBox("Новая или изменённая локация")
        form = QFormLayout(form_box)
        self.building_edit = QLineEdit()
        self.building_edit.setPlaceholderText("Корпус А / здание")
        self.floor_edit = QLineEdit()
        self.floor_edit.setPlaceholderText("2")
        self.room_edit = QLineEdit()
        self.room_edit.setPlaceholderText("204")
        self.rack_edit = QLineEdit()
        self.rack_edit.setPlaceholderText("Шкаф №2")
        self.unit_edit = QLineEdit()
        self.unit_edit.setPlaceholderText("U12")
        self.description_edit = QLineEdit()
        self.description_edit.setPlaceholderText("Ориентир: у окна, над дверью...")

        form.addRow("Здание / корпус", self.building_edit)
        form.addRow("Этаж *", self.floor_edit)
        form.addRow("Помещение *", self.room_edit)
        form.addRow("Шкаф", self.rack_edit)
        form.addRow("Юнит", self.unit_edit)
        form.addRow("Ориентир", self.description_edit)
        right.addWidget(form_box)

        buttons = QHBoxLayout()
        self.clear_btn = secondary_button("Очистить", height=48, font_size=15)
        self.clear_btn.clicked.connect(self._clear)
        buttons.addWidget(self.clear_btn)

        self.cancel_btn = secondary_button("Отмена", height=48, font_size=15)
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_btn)

        self.ok_btn = big_button("Выбрать локацию", "#2e7d32", height=56, font_size=17)
        self.ok_btn.clicked.connect(self._accept)
        buttons.addWidget(self.ok_btn, 2)
        right.addLayout(buttons)
        right.addStretch(1)
        layout.addLayout(right, 2)

    def _load_recent(self) -> None:
        self.recent_list.clear()
        self._recent = self.repo.recent_locations(self.session_id, limit=25)
        if not self._recent:
            item = QListWidgetItem("Пока нет сохранённых локаций")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recent_list.addItem(item)
            return
        for location in self._recent:
            item = QListWidgetItem(location.label() or location.short_label())
            item.setData(Qt.ItemDataRole.UserRole, location.id)
            self.recent_list.addItem(item)

    def _recent_activated(self, item: QListWidgetItem) -> None:
        location_id = item.data(Qt.ItemDataRole.UserRole)
        if not location_id:
            return
        for location in self._recent:
            if location.id == location_id:
                self._fill(location)
                self._accept()
                return

    def _fill(self, location: Location) -> None:
        self.building_edit.setText(location.building)
        self.floor_edit.setText(location.floor)
        self.room_edit.setText(location.room)
        self.rack_edit.setText(location.rack)
        self.unit_edit.setText(location.unit)
        self.description_edit.setText(location.description or "")

    def _clear(self) -> None:
        for edit in (
            self.building_edit,
            self.floor_edit,
            self.room_edit,
            self.rack_edit,
            self.unit_edit,
            self.description_edit,
        ):
            edit.clear()
        self.floor_edit.setFocus()

    def _accept(self) -> None:
        floor = self.floor_edit.text().strip()
        room = self.room_edit.text().strip()
        if not floor or not room:
            QMessageBox.warning(self, "Локация", "Укажите этаж и помещение")
            return
        try:
            self.location = self.repo.get_or_create_location(
                self.session_id,
                floor=floor,
                room=room,
                building=self.building_edit.text().strip(),
                rack=self.rack_edit.text().strip(),
                unit=self.unit_edit.text().strip(),
                description=self.description_edit.text().strip() or None,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Локация", f"Не удалось сохранить локацию:\n{exc}")
            return
        self.accept()

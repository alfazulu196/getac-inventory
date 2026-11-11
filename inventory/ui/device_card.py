# inventory/ui/device_card.py
"""Карточка устройства: создание и редактирование."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from inventory.config import (
    DEVICE_TYPE_BY_CODE,
    DEVICE_TYPES,
    POWER_TYPES,
    STATUSES,
    device_type_name,
)
from inventory.core.duplicates import check_duplicates
from inventory.core.oui import lookup_mac_vendor
from inventory.core.models import Device, Location
from inventory.core.parser import normalize_mac
from inventory.core.repository import InventoryRepository
from inventory.services.photo_intel import photo_intel_available
from inventory.services.voice import VoiceService
from inventory.ui.location_dialog import LocationDialog
from inventory.ui.photo_dialog import PhotoCaptureDialog
from inventory.ui.photo_intel_workers import PhotoIntelWorker, detach_running
from inventory.ui.widgets import big_button, secondary_button


class DeviceCardDialog(QDialog):
    def __init__(
        self,
        repo: InventoryRepository,
        session_id: str,
        paths: dict[str, Path],
        voice: VoiceService | None = None,
        *,
        device: Device | None = None,
        location: Location | None = None,
        initial: dict[str, Any] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.session_id = session_id
        self.paths = paths
        self.voice = voice
        self.device = device
        self.location = device.location if device else location
        self.initial = initial or {}
        self.photo_path: str | None = device.photo_path if device else None
        self._pending_photos: list[str] = []
        self._intel_worker: PhotoIntelWorker | None = None
        self._recording = False
        self._voice_timer = QTimer(self)
        self._voice_timer.setInterval(800)
        self._voice_timer.timeout.connect(self._update_voice_button)
        self._voice_timer.start()

        self.setWindowTitle("Карточка устройства" if device else "Новое устройство")
        self.setMinimumSize(900, 680)
        self._build_ui()
        self._load_reference_data()
        if device:
            self._load_device(device)
        else:
            self._apply_initial()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        self.title_label = QLabel("Новое устройство")
        self.title_label.setStyleSheet("font-size: 22px; font-weight: bold;")
        header.addWidget(self.title_label)
        header.addStretch(1)
        if self.device:
            id_label = QLabel(f"ID: {self.device.display_id}")
            id_label.setStyleSheet("color: #f1c40f; font-size: 18px; font-weight: bold;")
            header.addWidget(id_label)
        root.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_main_tab(), "Основное")
        self.tabs.addTab(self._build_connection_tab(), "Связь")
        self.tabs.addTab(self._build_note_tab(), "Заметка и фото")
        root.addWidget(self.tabs, 1)

        buttons = QHBoxLayout()
        self.cancel_btn = secondary_button("Отмена", height=56, font_size=16)
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_btn)
        buttons.addStretch(1)
        self.save_btn = big_button("💾 Сохранить", "#2e7d32", height=56, font_size=18)
        self.save_btn.clicked.connect(self._save)
        buttons.addWidget(self.save_btn, 2)
        root.addLayout(buttons)

    def _build_main_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(8)

        self.type_combo = QComboBox()
        for device_type in DEVICE_TYPES:
            self.type_combo.addItem(device_type.display_name, device_type.code)
        self._last_type_code = self.type_combo.currentData()
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("Тип устройства *", self.type_combo)

        self.subtype_edit = QLineEdit()
        self.subtype_edit.setPlaceholderText("Купольная, уличная, PoE-коммутатор...")
        form.addRow("Подтип", self.subtype_edit)

        self.asset_tag_edit = QLineEdit()
        self.asset_tag_edit.setPlaceholderText("Инвентарный номер заказчика")
        form.addRow("Инвентарный номер", self.asset_tag_edit)

        self.manufacturer_edit = QLineEdit()
        self.manufacturer_edit.setPlaceholderText("Hikvision, Dahua, Cisco...")
        form.addRow("Производитель", self.manufacturer_edit)

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("DS-2CD2143G0, Catalyst 2960X...")
        form.addRow("Модель", self.model_edit)

        self.serial_edit = QLineEdit()
        self.serial_edit.setPlaceholderText("Серийный номер")
        form.addRow("Серийный номер (S/N)", self.serial_edit)

        self.mac_edit = QLineEdit()
        self.mac_edit.setPlaceholderText("AA:BB:CC:DD:EE:FF")
        form.addRow("MAC-адрес", self.mac_edit)
        self.mac_edit.textChanged.connect(self._suggest_mac_vendor)

        self.ip_edit = QLineEdit()
        self.ip_edit.setPlaceholderText("192.168.10.51")
        form.addRow("IP-адрес", self.ip_edit)

        self.power_combo = QComboBox()
        self.power_combo.setEditable(True)
        self.power_combo.addItems(POWER_TYPES)
        form.addRow("Способ питания", self.power_combo)

        self.status_combo = QComboBox()
        for status in STATUSES:
            self.status_combo.addItem(status["name"], status["code"])
        form.addRow("Статус", self.status_combo)

        location_row = QHBoxLayout()
        self.location_label = QLabel("Не выбрана")
        self.location_label.setStyleSheet("color: #f1c40f; font-weight: bold;")
        location_row.addWidget(self.location_label, 1)
        self.location_btn = secondary_button("Изменить", height=40, font_size=14)
        self.location_btn.clicked.connect(self._choose_location)
        location_row.addWidget(self.location_btn)
        form.addRow("Место установки *", location_row)

        self.verified_check = QCheckBox("Проверено вручную")
        form.addRow("", self.verified_check)

        if self.location:
            self.location_label.setText(self.location.label())
        return tab

    def _build_connection_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(8)

        self.upstream_combo = QComboBox()
        form.addRow("Подключено к", self.upstream_combo)

        self.port_edit = QLineEdit()
        self.port_edit.setPlaceholderText("Gi0/12, Port 24, U12")
        form.addRow("Порт", self.port_edit)

        self.nvr_channel_edit = QLineEdit()
        self.nvr_channel_edit.setPlaceholderText("D1…D16 или 1…32")
        self.nvr_channel_edit.setToolTip(
            "Номер канала камеры на видеорегистраторе.\n"
            "В Excel появится лист «Регистраторы» — карта каналов."
        )
        form.addRow("Канал NVR", self.nvr_channel_edit)

        self.cable_combo = QComboBox()
        self.cable_combo.setEditable(True)
        self.cable_combo.addItems(["", "UTP", "FTP", "Оптика", "Другое"])
        form.addRow("Тип кабеля", self.cable_combo)

        self.connection_note_edit = QLineEdit()
        self.connection_note_edit.setPlaceholderText("Примечание к связи")
        form.addRow("Примечание", self.connection_note_edit)

        hint = QLabel(
            "Для камеры или точки доступа укажите коммутатор и порт. "
            "Для камеры на регистраторе — канал NVR. "
            "Для коммутатора — вышестоящий коммутатор/uplink."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9fb3c8;")
        form.addRow("", hint)
        return tab

    def _build_note_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.note_edit = QTextEdit()
        self.note_edit.setPlaceholderText("Примечание, дефекты, ориентиры...")
        layout.addWidget(self.note_edit, 1)

        voice_row = QHBoxLayout()
        self.voice_btn = secondary_button("🎤 Записать заметку", height=48, font_size=15)
        self.voice_btn.clicked.connect(self._toggle_voice)
        voice_row.addWidget(self.voice_btn)
        self.voice_status = QLabel("")
        self.voice_status.setStyleSheet("color: #9fb3c8;")
        voice_row.addWidget(self.voice_status, 1)
        layout.addLayout(voice_row)

        photo_row = QHBoxLayout()
        self.photo_btn = secondary_button("📷 Сделать фото", height=48, font_size=15)
        self.photo_btn.clicked.connect(self._take_photo)
        photo_row.addWidget(self.photo_btn)
        self.label_btn = secondary_button("🔍 Этикетка", height=48, font_size=15)
        self.label_btn.setToolTip(
            "Фото этикетки → автоматический разбор:\n"
            "штрихкод/S/N, MAC и модель подставляются в поля карточки.\n"
            "Работает и с фото, снятым телефоном (загрузите через 📁)."
        )
        self.label_btn.clicked.connect(self._label_photo)
        photo_row.addWidget(self.label_btn)
        ok_intel, intel_reason = photo_intel_available()
        if not ok_intel:
            self.label_btn.setEnabled(False)
            self.label_btn.setToolTip(
                "Разбор этикетки недоступен: " + intel_reason +
                "\nВнесите данные вручную (кнопкой 📷 фото всё равно можно сделать)."
            )
        self.photo_delete_btn = secondary_button("🗑 Убрать", height=48, font_size=14)
        self.photo_delete_btn.setToolTip(
            "Убрать последнее фото из устройства.\n"
            "Файл на диске останется в data\\photos\\"
        )
        self.photo_delete_btn.clicked.connect(self._delete_photo)
        photo_row.addWidget(self.photo_delete_btn)
        self.photo_label = QLabel("Фото не прикреплено")
        self.photo_label.setStyleSheet("color: #9fb3c8;")
        self.photo_label.setWordWrap(True)
        photo_row.addWidget(self.photo_label, 1)
        layout.addLayout(photo_row)

        # Миниатюра последнего снимка (одно устройство может иметь много фото).
        self.photo_thumb = QLabel()
        self.photo_thumb.setFixedSize(240, 170)
        self.photo_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_thumb.setStyleSheet(
            "background-color: #111820; border-radius: 8px; color: #9fb3c8;"
        )
        layout.addWidget(self.photo_thumb, 0, Qt.AlignmentFlag.AlignLeft)
        return tab

    # ------------------------------------------------------------------
    # Данные
    # ------------------------------------------------------------------
    def _load_reference_data(self) -> None:
        self.upstream_combo.clear()
        self.upstream_combo.addItem("Не указано", None)
        devices = self.repo.list_devices(self.session_id)
        current_id = self.device.id if self.device else None
        for device in devices:
            if device.id == current_id:
                continue
            label = f"{device.display_id} — {device_type_name(device.device_type)}"
            if device.model:
                label += f" {device.model}"
            self.upstream_combo.addItem(label, device.id)

    def _on_type_changed(self) -> None:
        """Для новой карточки подставляет способ питания по типу устройства."""
        if self.device:
            return
        new_code = self.type_combo.currentData()
        new_type = DEVICE_TYPE_BY_CODE.get(new_code)
        if not new_type:
            return
        old_type = DEVICE_TYPE_BY_CODE.get(self._last_type_code)
        current_power = self.power_combo.currentText().strip()
        if not current_power or (old_type and current_power == old_type.default_power):
            self.power_combo.setCurrentText(new_type.default_power)
        self._last_type_code = new_code

    def _suggest_mac_vendor(self) -> None:
        """Офлайн-подсказка производителя по первым 3 октетам MAC.

        Поле заполняется только если оно ещё пустое — ручной ввод главнее.
        """
        vendor = lookup_mac_vendor(self.mac_edit.text())
        if vendor and not self.manufacturer_edit.text().strip():
            self.manufacturer_edit.setText(vendor)

    def _apply_initial(self) -> None:
        initial = self.initial
        if initial.get("serial"):
            self.serial_edit.setText(str(initial["serial"]))
        if initial.get("mac"):
            self.mac_edit.setText(str(initial["mac"]))
        if initial.get("ip"):
            self.ip_edit.setText(str(initial["ip"]))
        if initial.get("model"):
            self.model_edit.setText(str(initial["model"]))
        if initial.get("manufacturer"):
            self.manufacturer_edit.setText(str(initial["manufacturer"]))
        if initial.get("asset_tag"):
            self.asset_tag_edit.setText(str(initial["asset_tag"]))
        if initial.get("device_type"):
            self._set_combo_data(self.type_combo, initial["device_type"])
        else:
            self._set_combo_data(self.type_combo, "camera")
        if initial.get("power_type"):
            self.power_combo.setCurrentText(str(initial["power_type"]))
        else:
            device_type = DEVICE_TYPE_BY_CODE.get(self.type_combo.currentData())
            if device_type and device_type.default_power:
                self.power_combo.setCurrentText(device_type.default_power)
        self._last_type_code = self.type_combo.currentData()
        if initial.get("scanned_code") and initial.get("code_type") == "ean":
            self.note_edit.setPlainText(f"Товарный код EAN: {initial['scanned_code']}")
        if initial.get("note_text"):
            self.note_edit.setPlainText(str(initial["note_text"]))
        photo = initial.get("photo_path")
        if photo:
            photo_str = str(photo)
            self.photo_path = photo_str
            if photo_str not in self._pending_photos:
                self._pending_photos.append(photo_str)
            self._refresh_photo_ui()
        if self.location:
            self.location_label.setText(self.location.label())

    def _load_device(self, device: Device) -> None:
        self.title_label.setText(device.title())
        self._set_combo_data(self.type_combo, device.device_type)
        self.subtype_edit.setText(device.subtype or "")
        self.asset_tag_edit.setText(device.asset_tag or "")
        self.manufacturer_edit.setText(device.manufacturer or "")
        self.model_edit.setText(device.model or "")
        self.serial_edit.setText(device.serial or "")
        self.mac_edit.setText(device.mac or "")
        self.ip_edit.setText(device.ip or "")
        self.power_combo.setCurrentText(device.power_type or "")
        self._set_combo_data(self.status_combo, device.status)
        self.nvr_channel_edit.setText(device.nvr_channel or "")
        self.note_edit.setPlainText(device.note or "")
        self.verified_check.setChecked(bool(device.is_verified))
        if device.location:
            self.location = device.location
            self.location_label.setText(device.location.label())
        self._refresh_photo_ui()

        connection = self.repo.get_connection(device.id)
        if connection:
            if connection.upstream_device_id:
                self._set_combo_data(self.upstream_combo, connection.upstream_device_id)
            self.port_edit.setText(connection.upstream_port or "")
            self.cable_combo.setCurrentText(connection.cable_type or "")
            self.connection_note_edit.setText(connection.note or "")

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: Any) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    # ------------------------------------------------------------------
    # Действия
    # ------------------------------------------------------------------
    def _choose_location(self) -> None:
        dialog = LocationDialog(self.repo, self.session_id, self.location, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.location:
            self.location = dialog.location
            self.location_label.setText(dialog.location.label())

    def _toggle_voice(self) -> None:
        if not self.voice:
            return
        if not self._recording:
            if not self.voice.available:
                QMessageBox.information(
                    self,
                    "Голос",
                    "Модель голоса ещё не загружена или недоступна.\n"
                    "Проверьте предстартовую проверку и настройки.",
                )
                return
            if self.voice.start_recording():
                self._recording = True
                self.voice_btn.setText("⏹ Остановить и распознать")
                self.voice_status.setText("Идёт запись...")
        else:
            self._recording = False
            text = self.voice.stop_and_recognize()
            self.voice_btn.setText("🎤 Записать заметку")
            self.voice_status.setText(self.voice.status)
            if text:
                current = self.note_edit.toPlainText().strip()
                self.note_edit.setPlainText(f"{current} {text}".strip())

    def _update_voice_button(self) -> None:
        if self._recording:
            return
        available = bool(self.voice and self.voice.available)
        self.voice_btn.setEnabled(available)
        if self.voice and not available:
            self.voice_status.setText(self.voice.status)
        elif self.voice and available and not self.voice_status.text():
            self.voice_status.setText("Готово")

    def _set_thumbnail(self, path: str | None) -> None:
        if path and Path(path).exists():
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                self.photo_thumb.setPixmap(
                    pixmap.scaled(
                        self.photo_thumb.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                return
        self.photo_thumb.clear()

    def _current_photo_paths(self) -> list[str]:
        """Фото устройства: таблица photos плюс снимки, ожидающие сохранения."""
        paths: list[str] = []
        if self.device:
            paths = [photo.path for photo in self.repo.list_photos(self.device.id)]
        for pending in self._pending_photos:
            if pending not in paths:
                paths.append(pending)
        return paths

    def _refresh_photo_ui(self) -> None:
        """Миниатюра и подпись по текущему списку фото."""
        paths = self._current_photo_paths()
        if paths:
            names = [Path(item).name for item in paths]
            extra = len(names) - 1
            suffix = f" (+ ещё {extra})" if extra > 0 else ""
            self.photo_label.setText(names[-1] + suffix)
            self._set_thumbnail(paths[-1])
        else:
            self.photo_label.setText("Фото не прикреплено")
            self._set_thumbnail(None)

    def _delete_photo(self) -> None:
        paths = self._current_photo_paths()
        if not paths:
            QMessageBox.information(self, "Фото", "Привязанных фото нет")
            return
        path = paths[-1]
        name = Path(path).name
        reply = QMessageBox.question(
            self,
            "Убрать фото",
            f"Убрать «{name}» из устройства?\n"
            "Файл на диске останется в data\\photos\\",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self.device:
            photos = self.repo.list_photos(self.device.id)
            target = next((item for item in photos if item.path == path), None)
            if target is not None:
                # Обложка (device.photo_path) обновляется внутри delete_photo.
                self.repo.delete_photo(target.id)
            self.device = self.repo.get_device(self.device.id) or self.device
        if path in self._pending_photos:
            self._pending_photos.remove(path)
        self._refresh_photo_ui()

    def _take_photo(self) -> None:
        dialog = PhotoCaptureDialog(self.paths["photos"], self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.photo_path:
            self.photo_path = dialog.photo_path
            if self.device:
                self.repo.add_photo(self.device.id, dialog.photo_path)
            else:
                self._pending_photos.append(dialog.photo_path)
            self._refresh_photo_ui()

    # ------------------------------------------------------------------
    # Разбор этикетки по фото («🔍 Этикетка»)
    # ------------------------------------------------------------------
    def _label_photo(self) -> None:
        dialog = PhotoCaptureDialog(self.paths["photos"], self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.photo_path:
            return
        path = dialog.photo_path
        self.photo_path = path
        if self.device:
            self.repo.add_photo(self.device.id, path)
        elif path not in self._pending_photos:
            self._pending_photos.append(path)
        self._refresh_photo_ui()
        self._start_label_intel(path)

    def _start_label_intel(self, path: str) -> None:
        detach_running(self._intel_worker)
        self._intel_worker = PhotoIntelWorker(path, self)
        self._intel_worker.analysed.connect(self._on_label_intel)
        self.label_btn.setEnabled(False)
        self.photo_label.setText("🔍 Разбираю этикетку (несколько секунд)...")
        self._intel_worker.start()

    def _on_label_intel(self, intel, path: str) -> None:
        self.label_btn.setEnabled(True)
        self._intel_worker = None
        self._refresh_photo_ui()  # восстановить подпись фото
        if intel is None:
            return
        if intel.error:
            self.toast_label_info(f"Этикетка: ошибка разбора — {intel.error}")
            return
        if not intel.has_candidates:
            self.toast_label_info(
                "Этикетка: ничего не распознано. Внесите данные вручную "
                "(снимите этикетку крупнее и без бликов)."
            )
            return
        # Подставляем только в пустые поля — ручной ввод главнее.
        applied: list[str] = []
        if intel.serials and not self.serial_edit.text().strip():
            self.serial_edit.setText(intel.serials[0])
            applied.append("S/N")
        if intel.macs and not self.mac_edit.text().strip():
            self.mac_edit.setText(intel.macs[0])
            applied.append("MAC")
        if intel.models and not self.model_edit.text().strip():
            self.model_edit.setText(intel.models[0])
            applied.append("модель")
        if intel.ip_addresses and not self.ip_edit.text().strip():
            self.ip_edit.setText(intel.ip_addresses[0])
            applied.append("IP")
        vendor = None
        if intel.macs:
            vendor = lookup_mac_vendor(intel.macs[0])
        if vendor and not self.manufacturer_edit.text().strip():
            self.manufacturer_edit.setText(vendor)
            applied.append("производитель")

        message_lines = []
        if intel.serials:
            message_lines.append(f"S/N: {', '.join(intel.serials[:4])}")
        if intel.macs:
            message_lines.append(
                "MAC: " + ", ".join(intel.macs[:3]) + (f" (вендор: {vendor})" if vendor else "")
            )
        if intel.models:
            message_lines.append(f"Модель: {', '.join(intel.models[:3])}")
        if intel.part_numbers:
            message_lines.append(f"P/N: {', '.join(intel.part_numbers[:2])}")
        if intel.ip_addresses and "IP" not in applied:
            message_lines.append(f"IP: {', '.join(intel.ip_addresses[:2])}")

        head = (
            "Поля заполнены: " + ", ".join(applied) + "."
            if applied
            else "Новые поля свободны — проверьте ниже."
        )
        QMessageBox.information(
            self,
            "Этикетка распознана",
            f"{head}\n\nКандидаты (проверьте и поправьте вручную):\n"
            + "\n".join(message_lines) + "\n\n"
            "Источник: " + Path(path).name,
        )

    def toast_label_info(self, text: str) -> None:
        self.photo_label.setText(text)
        self.photo_label.setStyleSheet("color: #e67e22;")
        QTimer.singleShot(8000, self._reset_photo_label_style)

    def _reset_photo_label_style(self) -> None:
        self.photo_label.setStyleSheet("color: #9fb3c8;")
        self._refresh_photo_ui()

    def _save(self) -> None:
        if self._recording and self.voice:
            self.voice.stop_and_recognize()
            self._recording = False
        if not self.location:
            QMessageBox.warning(self, "Сохранение", "Выберите место установки")
            return

        serial = self.serial_edit.text().strip() or None
        mac = self.mac_edit.text().strip() or None
        if mac and not normalize_mac(mac):
            reply = QMessageBox.question(
                self,
                "MAC-адрес",
                "MAC-адрес не распознан. Сохранить как введено?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        matches = check_duplicates(
            self.repo,
            self.session_id,
            serial=serial,
            mac=mac,
            exclude_id=self.device.id if self.device else None,
        )
        if matches:
            details = "\n".join(match.message() for match in matches)
            reply = QMessageBox.question(
                self,
                "Возможный дубль",
                f"Найдены устройства с такими же данными:\n\n{details}\n\n"
                "Сохранить всё равно?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        data: dict[str, Any] = {
            "asset_tag": self.asset_tag_edit.text().strip() or None,
            "device_type": self.type_combo.currentData(),
            "subtype": self.subtype_edit.text().strip() or None,
            "manufacturer": self.manufacturer_edit.text().strip() or None,
            "model": self.model_edit.text().strip() or None,
            "serial": serial,
            "mac": mac,
            "ip": self.ip_edit.text().strip() or None,
            "location_id": self.location.id,
            "power_type": self.power_combo.currentText().strip() or None,
            "status": self.status_combo.currentData(),
            "nvr_channel": self.nvr_channel_edit.text().strip() or None,
            "note": self.note_edit.toPlainText().strip() or None,
            "photo_path": self.photo_path,
            "is_verified": self.verified_check.isChecked(),
        }
        if self.initial:
            data.setdefault("scanned_code", self.initial.get("scanned_code"))
            data.setdefault("code_type", self.initial.get("code_type"))

        try:
            if self.device:
                self.device = self.repo.update_device(self.device.id, **data)
            else:
                self.device = self.repo.create_device(
                    self.session_id,
                    data.pop("device_type"),
                    data.pop("location_id"),
                    **data,
                )

            upstream_id = self.upstream_combo.currentData()
            if upstream_id:
                self.repo.set_connection(
                    self.device.id,
                    upstream_id,
                    self.port_edit.text().strip() or None,
                    self.cable_combo.currentText().strip() or None,
                    self.connection_note_edit.text().strip() or None,
                )
            elif self.repo.get_connection(self.device.id):
                self.repo.delete_connection(self.device.id)

            for pending in self._pending_photos:
                self.repo.add_photo(self.device.id, pending)
            self._pending_photos.clear()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Сохранение", f"Не удалось сохранить устройство:\n{exc}")
            return

        self.accept()

    def reject(self) -> None:
        if self._recording and self.voice:
            self.voice.cancel_recording()
        if self._intel_worker is not None:
            detach_running(self._intel_worker)
            self._intel_worker = None
        super().reject()

    def closeEvent(self, event) -> None:
        if self._recording and self.voice:
            self.voice.cancel_recording()
        detach_running(self._intel_worker)
        self._intel_worker = None
        self._voice_timer.stop()
        super().closeEvent(event)

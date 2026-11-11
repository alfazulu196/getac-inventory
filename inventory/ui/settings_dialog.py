# inventory/ui/settings_dialog.py
"""Настройки сканера, голоса и данных."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from inventory.config import APP_VERSION, AppConfig, save_config
from inventory.core.repository import InventoryRepository
from inventory.services.scanner import list_serial_ports
from inventory.services.voice import VoiceService
from inventory.ui.widgets import big_button, secondary_button


class SettingsDialog(QDialog):
    def __init__(
        self,
        config: AppConfig,
        paths: dict[str, Path],
        voice: VoiceService | None = None,
        repo: "InventoryRepository | None" = None,
        parent=None,
    ):
        super().__init__(parent)
        self.config = config
        self.paths = paths
        self.voice = voice
        self.repo = repo
        self.setWindowTitle("Настройки")
        self.setMinimumSize(820, 600)
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Настройки приложения")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._scanner_tab(), "Сканер")
        tabs.addTab(self._voice_tab(), "Голос")
        tabs.addTab(self._report_tab(), "Отчёт")
        tabs.addTab(self._data_tab(), "Данные")
        layout.addWidget(tabs, 1)

        self.version_label = QLabel(f"Версия приложения: {APP_VERSION}")
        self.version_label.setStyleSheet("color: #9fb3c8; font-size: 13px;")
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.version_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = secondary_button("Отмена", height=52, font_size=16)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = big_button("💾 Сохранить", "#2e7d32", height=52, font_size=17)
        save.clicked.connect(self._save)
        buttons.addWidget(save, 2)
        layout.addLayout(buttons)

    def _scanner_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setContentsMargins(14, 14, 14, 14)
        form.setSpacing(8)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("COM-порт", "com")
        self.mode_combo.addItem("Режим клавиатуры (HID)", "hid")
        self.mode_combo.addItem("Только ручной ввод", "manual")
        self.mode_combo.currentIndexChanged.connect(self._update_scanner_fields)
        form.addRow("Режим сканера", self.mode_combo)

        port_row = QHBoxLayout()
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        ports = list_serial_ports()
        for port in ports:
            self.port_combo.addItem(f"{port.device} — {port.description}", port.device)
        if not ports:
            self.port_combo.addItem("COM3", "COM3")
        port_row.addWidget(self.port_combo, 1)
        form.addRow("COM-порт", port_row)

        self.baud_combo = QComboBox()
        self.baud_combo.setEditable(True)
        for baud in (9600, 19200, 38400, 57600, 115200):
            self.baud_combo.addItem(str(baud), baud)
        form.addRow("Скорость", self.baud_combo)

        self.laser_edit = QLineEdit()
        self.laser_edit.setPlaceholderText("F11, F12")
        form.addRow("Клавиши лазера", self.laser_edit)

        self.dedup_spin = QDoubleSpinBox()
        self.dedup_spin.setRange(0.1, 10.0)
        self.dedup_spin.setSingleStep(0.1)
        self.dedup_spin.setSuffix(" с")
        form.addRow("Защита от повтора", self.dedup_spin)

        hint = QLabel(
            "Для Getac лазер обычно настраивается в BIOS/утилите сканера. "
            "Если F11/F12 не работают, укажите другие клавиши."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9fb3c8;")
        form.addRow("", hint)
        return tab

    def _voice_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setContentsMargins(14, 14, 14, 14)
        form.setSpacing(8)

        self.voice_enabled = QCheckBox("Включить голосовые заметки")
        form.addRow("", self.voice_enabled)

        self.mic_combo = QComboBox()
        self.mic_combo.addItem("По умолчанию", None)
        if self.voice:
            for device in self.voice.list_input_devices():
                label = f"{device['name']}" + (" (по умолчанию)" if device["default"] else "")
                self.mic_combo.addItem(label, device["index"])
        form.addRow("Микрофон", self.mic_combo)

        self.model_label = QLabel(str(self.config.voice.model_path))
        self.model_label.setStyleSheet("color: #9fb3c8;")
        form.addRow("Модель Vosk", self.model_label)

        hint = QLabel(
            "Модель загружается в фоне при старте обхода. "
            "Если её нет, голосовые заметки отключаются, остальное работает."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9fb3c8;")
        form.addRow("", hint)
        return tab

    def _report_tab(self) -> QWidget:
        """Шапка титульного листа Excel: под кого оформлен отчёт."""
        tab = QWidget()
        form = QFormLayout(tab)
        form.setContentsMargins(14, 14, 14, 14)
        form.setSpacing(8)

        self.report_org_edit = QLineEdit()
        self.report_org_edit.setPlaceholderText(
            "ООО «ЧОП Форт», ИП Смирнов, УЖК «Вымпел»..."
        )
        form.addRow("Организация (подрядчик)", self.report_org_edit)

        self.report_contract_edit = QLineEdit()
        self.report_contract_edit.setPlaceholderText(
            "Договор № 2026-17, тендер 0042-ТР..."
        )
        form.addRow("Номер договора", self.report_contract_edit)

        hint = QLabel(
            "Эти строки печатаются в шапке титульного листа Excel-отчёта.\n"
            "Заполнение не обязательно: без них на титуле остаются пустые поля."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9fb3c8;")
        form.addRow("", hint)
        return tab

    def _data_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setContentsMargins(14, 14, 14, 14)
        form.setSpacing(8)

        self.data_label = QLabel(str(self.paths["data"]))
        self.data_label.setWordWrap(True)
        form.addRow("Папка данных", self.data_label)
        self.db_label = QLabel(str(self.paths["db"]))
        self.db_label.setWordWrap(True)
        form.addRow("База", self.db_label)
        self.photos_label = QLabel(str(self.paths["photos"]))
        self.photos_label.setWordWrap(True)
        form.addRow("Фото", self.photos_label)
        self.exports_label = QLabel(str(self.paths["exports"]))
        self.exports_label.setWordWrap(True)
        form.addRow("Экспорт", self.exports_label)
        self.backups_label = QLabel(str(self.paths["backups"]))
        self.backups_label.setWordWrap(True)
        form.addRow("Резервные копии", self.backups_label)
        return tab

    def _update_scanner_fields(self) -> None:
        is_com = self.mode_combo.currentData() == "com"
        self.port_combo.setEnabled(is_com)
        self.baud_combo.setEnabled(is_com)

    def _load(self) -> None:
        scanner = self.config.scanner
        index = self.mode_combo.findData(scanner.mode)
        self.mode_combo.setCurrentIndex(index if index >= 0 else 0)
        self._update_scanner_fields()
        self.port_combo.setCurrentText(scanner.port)
        self.baud_combo.setCurrentText(str(scanner.baudrate))
        self.laser_edit.setText(", ".join(scanner.laser_keys))
        self.dedup_spin.setValue(scanner.dedup_seconds)

        self.voice_enabled.setChecked(self.config.voice.enabled)
        if self.config.voice.device is not None:
            mic_index = self.mic_combo.findData(self.config.voice.device)
            if mic_index >= 0:
                self.mic_combo.setCurrentIndex(mic_index)

        if self.repo is not None:
            self.report_org_edit.setText(str(self.repo.get_setting("report_org") or ""))
            self.report_contract_edit.setText(
                str(self.repo.get_setting("report_contract") or "")
            )

    def _save(self) -> None:
        mode = self.mode_combo.currentData()
        port = self.port_combo.currentText().strip()
        if mode == "com" and not port:
            QMessageBox.warning(self, "Настройки", "Укажите COM-порт")
            return
        try:
            baud = int(self.baud_combo.currentText().strip())
        except ValueError:
            QMessageBox.warning(self, "Настройки", "Некорректная скорость COM-порта")
            return
        laser_keys = [
            item.strip() for item in self.laser_edit.text().split(",") if item.strip()
        ]

        self.config.scanner.mode = mode
        self.config.scanner.port = port
        self.config.scanner.baudrate = baud
        self.config.scanner.laser_keys = laser_keys or ["F11", "F12"]
        self.config.scanner.dedup_seconds = self.dedup_spin.value()

        self.config.voice.enabled = self.voice_enabled.isChecked()
        self.config.voice.device = self.mic_combo.currentData()

        if self.repo is not None:
            self.repo.set_setting(
                "report_org", self.report_org_edit.text().strip()
            )
            self.repo.set_setting(
                "report_contract", self.report_contract_edit.text().strip()
            )

        try:
            save_config(self.config)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Настройки", f"Не удалось сохранить настройки:\n{exc}")
            return
        self.accept()

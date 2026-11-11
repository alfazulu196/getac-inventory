# inventory/ui/survey_screen.py
"""Главный экран полевого обхода."""
from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from inventory.config import (
    DEVICE_TYPE_BY_CODE,
    DEVICE_TYPES,
    STATUSES,
    device_type_name,
    save_config,
    status_name,
)
from inventory.core.duplicates import check_duplicates, find_duplicate_groups
from inventory.core.models import Device, Location, Session
from inventory.core.oui import lookup_mac_vendor
from inventory.core.parser import parse_code
from inventory.core.repository import InventoryRepository
from inventory.services.photo_intel import photo_intel_available
from inventory.services.photo_server import PhotoServer
from inventory.services.rack_ai import ollama_status
from inventory.services.scanner import (
    KeyboardScannerBuffer,
    ScannerThread,
    trigger_laser,
)
from inventory.services.voice import VoiceService
from inventory.ui.device_card import DeviceCardDialog
from inventory.ui.device_list import DeviceListDialog
from inventory.ui.duplicates_dialog import DuplicatesDialog
from inventory.ui.finish_dialog import FinishDialog
from inventory.ui.help_dialog import HelpDialog
from inventory.ui.location_dialog import LocationDialog
from inventory.ui.phone_link_dialog import PhoneLinkDialog
from inventory.ui.photo_dialog import PhotoCaptureDialog
from inventory.ui.photo_intel_workers import PhotoIntelWorker, detach_running
from inventory.ui.rack_dialog import RackDialog
from inventory.ui.settings_dialog import SettingsDialog
from inventory.ui.voice_workers import VoiceLoadWorker, VoiceRecognizeWorker
from inventory.ui.widgets import (
    ToastLabel,
    beep_error,
    beep_ok,
    beep_scan,
    big_button,
    secondary_button,
)


class PhotoLinkBridge(QObject):
    """Мост: фото пришло из потока сервера — сигнал уходит в главный поток."""

    photo_received = pyqtSignal(str)

    def forward(self, path: str, _device_name: str) -> None:
        self.photo_received.emit(path)


class SurveyScreen(QWidget):
    session_finished = pyqtSignal()

    def __init__(
        self,
        repo: InventoryRepository,
        config,
        paths: dict[str, Path],
        session: Session,
        voice: VoiceService,
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.config = config
        self.paths = paths
        self.session = session
        self.voice = voice
        self.current_type = (
            config.last_device_type
            if config.last_device_type in DEVICE_TYPE_BY_CODE
            else "camera"
        )
        self.current_location: Location | None = self._load_last_location()
        self.active_device: Device | None = None
        self.quick_mode = True
        self.scanner_thread: ScannerThread | None = None
        self.voice_loader: VoiceLoadWorker | None = None
        self.voice_recognizer: VoiceRecognizeWorker | None = None
        self._voice_recording = False
        self._photo_intel_worker: PhotoIntelWorker | None = None
        self._photo_intel_path: str | None = None
        self._battery_warned = False
        self._battery_timer = QTimer(self)
        self._battery_timer.setInterval(30_000)
        self._battery_timer.timeout.connect(self._update_battery)
        self.hid_buffer = KeyboardScannerBuffer(
            max_interval=0.1,
            strip_prefix=config.scanner.strip_prefix,
            strip_suffix=config.scanner.strip_suffix,
        )
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._build_ui()
        self._start_photo_server()
        self.refresh_all()
        self.restart_scanner()
        self._start_voice_loader()
        self._update_battery()
        self._battery_timer.start()

    # ------------------------------------------------------------------
    # Фото с телефона (сервер приёма)
    # ------------------------------------------------------------------
    def _start_photo_server(self) -> None:
        self.photo_bridge = PhotoLinkBridge(self)
        self.photo_bridge.photo_received.connect(self._on_phone_photo)
        self.photo_server = PhotoServer(self.paths["photos"], on_photo=self.photo_bridge.forward)
        try:
            self.photo_server.start()
        except RuntimeError as exc:
            self.photo_server.urls = []
            QTimer.singleShot(1500, lambda text=str(exc): self.toast.show_message(
                f"📱 Приём фото с телефона: {text}", "error", timeout=10000
            ))

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # Верхняя панель: сессия, локация и кнопка настроек.
        top = QHBoxLayout()
        subtitle = [self.session.inspector]
        project = (self.session.name or "").strip()
        if project and project != f"Инвентаризация: {self.session.object_name}":
            subtitle.append(project)
        if (self.session.contractor or "").strip():
            subtitle.append(f"подрядчик: {self.session.contractor.strip()}")
        self.session_label = QLabel(
            f"<b>{escape(self.session.object_name)}</b><br>"
            f"<span style='color:#9fb3c8; font-size:13px;'>{escape(' · '.join(subtitle))}</span>"
        )
        self.session_label.setMinimumWidth(200)
        top.addWidget(self.session_label)

        self.location_btn = big_button("📍 Локация не выбрана", "#8e44ad", height=56, font_size=15)
        self.location_btn.clicked.connect(self.choose_location)
        top.addWidget(self.location_btn, 2)

        self.settings_btn = secondary_button("⚙", height=52, font_size=20)
        self.settings_btn.setFixedWidth(62)
        self.settings_btn.setToolTip("Настройки")
        self.settings_btn.clicked.connect(self.open_settings)
        top.addWidget(self.settings_btn)

        self.help_btn = secondary_button("?", height=52, font_size=20)
        self.help_btn.setFixedWidth(48)
        self.help_btn.setToolTip("Справка: шпаргалка по работе в поле")
        self.help_btn.clicked.connect(self.open_help)
        top.addWidget(self.help_btn)
        root.addLayout(top)

        # Строка состояния: батарея, сканер, голос.
        status = QHBoxLayout()
        status.setSpacing(18)
        self.battery_label = QLabel("🔋 —")
        self.battery_label.setStyleSheet("font-size: 13px; color: #9fb3c8;")
        status.addWidget(self.battery_label)

        self.scanner_label = QLabel("Сканер: —")
        self.scanner_label.setStyleSheet("font-size: 13px; color: #9fb3c8;")
        status.addWidget(self.scanner_label)

        self.voice_label = QLabel("Голос: —")
        self.voice_label.setStyleSheet("font-size: 13px; color: #9fb3c8;")
        status.addWidget(self.voice_label)

        status.addStretch(1)
        root.addLayout(status)

        main = QHBoxLayout()
        main.setSpacing(12)

        # Левая колонка: типы оборудования.
        left_box = QGroupBox("Тип оборудования")
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(8, 14, 8, 8)
        type_grid = QGridLayout()
        type_grid.setSpacing(6)
        self.type_group = QButtonGroup(self)
        self.type_group.setExclusive(True)
        self.type_buttons: dict[str, QPushButton] = {}
        for index, device_type in enumerate(DEVICE_TYPES):
            button = QPushButton(f"{device_type.icon} {device_type.name}")
            button.setCheckable(True)
            button.setMinimumHeight(52)
            button.setStyleSheet(
                """
                QPushButton {
                    background-color: #2b3442;
                    color: #ecf0f1;
                    font-size: 13px;
                    font-weight: bold;
                    border-radius: 9px;
                    padding: 5px;
                    text-align: left;
                    padding-left: 10px;
                }
                QPushButton:checked {
                    background-color: #16a085;
                    color: white;
                }
                QPushButton:pressed {
                    background-color: #1abc9c;
                }
                """
            )
            button.clicked.connect(
                lambda checked=False, code=device_type.code: self.set_type(code)
            )
            self.type_group.addButton(button)
            self.type_buttons[device_type.code] = button
            type_grid.addWidget(button, index // 2, index % 2)
        left_layout.addLayout(type_grid)
        left_layout.addStretch(1)
        self.quick_check = QCheckBox("Быстрый режим:\nсохранять сразу")
        self.quick_check.setChecked(True)
        self.quick_check.setStyleSheet("font-size: 13px; color: #9fb3c8;")
        self.quick_check.toggled.connect(self._set_quick_mode)
        left_layout.addWidget(self.quick_check)
        main.addWidget(left_box, 0)

        # Центральная колонка: сканирование и активное устройство.
        center = QVBoxLayout()
        center.setSpacing(8)

        scan_box = QGroupBox("Сканирование")
        scan_layout = QVBoxLayout(scan_box)
        scan_layout.setContentsMargins(10, 14, 10, 10)
        scan_layout.setSpacing(8)

        self.last_result = QLabel(
            "Выберите тип и локацию, затем сканируйте устройство."
        )
        self.last_result.setWordWrap(True)
        self.last_result.setMinimumHeight(58)
        self.last_result.setStyleSheet(
            "background-color: #111820; border-radius: 8px; padding: 10px; "
            "font-size: 15px; color: #d6eaf8;"
        )
        scan_layout.addWidget(self.last_result)

        self.laser_btn = big_button("🔥 ЗАЖЕЧЬ ЛАЗЕР", "#d35400", height=84, font_size=24)
        self.laser_btn.clicked.connect(self.activate_laser)
        scan_layout.addWidget(self.laser_btn)

        manual_row = QHBoxLayout()
        self.manual_edit = QLineEdit()
        self.manual_edit.setPlaceholderText("Или введите код вручную и нажмите Enter")
        self.manual_edit.returnPressed.connect(self.manual_scan)
        manual_row.addWidget(self.manual_edit, 1)
        self.manual_btn = big_button("OK", "#2980b9", height=48, font_size=17)
        self.manual_btn.setFixedWidth(90)
        self.manual_btn.clicked.connect(self.manual_scan)
        manual_row.addWidget(self.manual_btn)
        scan_layout.addLayout(manual_row)

        self.add_manual_btn = secondary_button(
            "＋ Добавить без сканирования", height=44, font_size=14
        )
        self.add_manual_btn.clicked.connect(self.add_manual_device)
        scan_layout.addWidget(self.add_manual_btn)
        self.rack_btn = secondary_button(
            "🖥 Стойка по фото (ИИ)", height=44, font_size=14
        )
        self.rack_btn.setToolTip(
            "Фото стойки → черновик списка видимых юнитов → создать пачкой.\n"
            "Локальный ИИ (ollama), офлайн."
        )
        self.rack_btn.clicked.connect(self.open_rack_dialog)
        scan_layout.addWidget(self.rack_btn)
        _ok_rack, _rack_reason = ollama_status()
        if not _ok_rack:
            self.rack_btn.setEnabled(False)
            self.rack_btn.setToolTip(
                "Распознавание стойки недоступно: " + _rack_reason +
                "\nОстальные функции работают, юниты вписывайте вручную."
            )
        center.addWidget(scan_box)

        active_box = QGroupBox("Активное устройство")
        active_layout = QVBoxLayout(active_box)
        active_layout.setContentsMargins(10, 14, 10, 10)
        active_layout.setSpacing(6)
        self.active_card = QTextEdit()
        self.active_card.setReadOnly(True)
        self.active_card.setFrameShape(QFrame.Shape.NoFrame)
        self.active_card.setFixedHeight(112)
        self.active_card.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.active_card.document().setDocumentMargin(0)
        self.active_card.setStyleSheet(
            "QTextEdit { background-color: #111820; border-radius: 8px; padding: 8px; "
            "font-size: 14px; color: #d6eaf8; }"
        )
        active_layout.addWidget(self.active_card)

        active_buttons = QHBoxLayout()
        active_buttons.setSpacing(6)
        self.note_btn = big_button("🎤 Заметка", "#2e7d32", height=50, font_size=15)
        self.note_btn.clicked.connect(self.toggle_voice_note)
        active_buttons.addWidget(self.note_btn)
        self.photo_btn = secondary_button("📷 Фото", height=50, font_size=14)
        self.photo_btn.clicked.connect(self.take_photo)
        active_buttons.addWidget(self.photo_btn)
        self.phone_btn = secondary_button("📱 Телефон", height=50, font_size=13)
        self.phone_btn.setToolTip("Принять фото с телефона по Wi-Fi (QR-адрес)")
        self.phone_btn.clicked.connect(self.open_phone_link)
        active_buttons.addWidget(self.phone_btn)
        self.detail_btn = secondary_button("✏ Подробно", height=50, font_size=14)
        self.detail_btn.clicked.connect(self.open_active_card)
        active_buttons.addWidget(self.detail_btn)
        self.connection_btn = secondary_button("🔌 Связь", height=50, font_size=14)
        self.connection_btn.clicked.connect(self.open_active_connection)
        active_buttons.addWidget(self.connection_btn)
        self.active_buttons_row = QWidget()
        active_buttons_row_layout = QVBoxLayout(self.active_buttons_row)
        active_buttons_row_layout.setContentsMargins(0, 0, 0, 0)
        active_buttons_row_layout.setSpacing(6)
        active_buttons_row_layout.addLayout(active_buttons)
        active_buttons_row_layout.addWidget(self._make_status_row())
        active_layout.addWidget(self.active_buttons_row)

        self.voice_status_label = QLabel("")
        self.voice_status_label.setStyleSheet("color: #9fb3c8; font-size: 13px;")
        self.voice_status_label.setVisible(False)
        active_layout.addWidget(self.voice_status_label)
        center.addWidget(active_box)

        self.counters_label = QLabel("")
        self.counters_label.setStyleSheet("color: #9fb3c8; font-size: 14px;")
        center.addWidget(self.counters_label)
        center.addStretch(1)
        main.addLayout(center, 1)

        # Правая колонка: последние устройства и навигация.
        right = QVBoxLayout()
        right.setSpacing(8)
        recent_box = QGroupBox("Последние устройства")
        recent_layout = QVBoxLayout(recent_box)
        recent_layout.setContentsMargins(8, 14, 8, 8)
        self.recent_list = QListWidget()
        self.recent_list.setMinimumWidth(310)
        self.recent_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.recent_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.recent_list.itemClicked.connect(self._select_recent)
        self.recent_list.itemDoubleClicked.connect(self._open_recent)
        recent_layout.addWidget(self.recent_list, 1)
        right.addWidget(recent_box, 1)

        self.list_btn = big_button("📋 Все устройства", "#34495e", height=52, font_size=15)
        self.list_btn.clicked.connect(self.open_device_list)
        right.addWidget(self.list_btn)

        self.duplicates_btn = big_button("👥 Дубликаты", "#b9770e", height=52, font_size=15)
        self.duplicates_btn.clicked.connect(self.open_duplicates)
        right.addWidget(self.duplicates_btn)

        self.finish_btn = big_button("🏁 Завершить обход", "#c0392b", height=62, font_size=17)
        self.finish_btn.clicked.connect(self.finish_session)
        right.addWidget(self.finish_btn)
        main.addLayout(right, 0)

        root.addLayout(main, 1)
        self.toast = ToastLabel()
        root.addWidget(self.toast)

        # Начальное состояние.
        self.type_buttons[self.current_type].setChecked(True)
        self.update_location_button()
        self._update_duplicates_count()

    # ------------------------------------------------------------------
    # Состояние
    # ------------------------------------------------------------------
    def _load_last_location(self) -> Location | None:
        if not self.config.last_location_id:
            return None
        location = self.repo.get_location(self.config.last_location_id)
        if location and location.session_id == self.session.id:
            return location
        return None

    def _set_quick_mode(self, enabled: bool) -> None:
        self.quick_mode = enabled

    def set_type(self, code: str) -> None:
        if code not in DEVICE_TYPE_BY_CODE:
            return
        self.current_type = code
        self.config.last_device_type = code
        self.type_buttons[code].setChecked(True)
        try:
            save_config(self.config)
        except OSError:
            pass
        self.refresh_counters()

    def choose_location(self) -> None:
        dialog = LocationDialog(self.repo, self.session.id, self.current_location, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.location:
            self.current_location = dialog.location
            self.config.last_location_id = dialog.location.id
            try:
                save_config(self.config)
            except OSError:
                pass
            self.update_location_button()
            self.toast.show_message(f"Локация: {dialog.location.label()}", "info")

    def update_location_button(self) -> None:
        if self.current_location:
            label = self.current_location.label()
            self.location_btn.setText(f"📍 {label}")
            self.location_btn.setToolTip(f"Текущая локация: {label}")
        else:
            self.location_btn.setText("📍 Локация не выбрана")
            self.location_btn.setToolTip("Укажите этаж, помещение и шкаф")

    def ensure_location(self) -> bool:
        if self.current_location:
            return True
        self.choose_location()
        return self.current_location is not None

    def refresh_all(self) -> None:
        self.refresh_active()
        self.refresh_recent()
        self.refresh_counters()
        self.update_location_button()
        self._update_duplicates_count()

    def open_duplicates(self) -> None:
        dialog = DuplicatesDialog(self.repo, self.session.id, self.paths, self.voice, self)
        dialog.exec()
        self._update_duplicates_count()

    def _update_duplicates_count(self) -> None:
        groups = find_duplicate_groups(self.repo, self.session.id)
        self.duplicates_btn.setText(
            f"👥 Дубликаты ({len(groups)})" if groups else "👥 Дубликаты"
        )

    def _make_status_row(self) -> QWidget:
        """Строка «чипов» статуса под активной карточкой: один клик, без карточки."""
        labels = {
            "installed": "✔ ОК",
            "faulty": "✖ Не работает",
            "replace": "🛠 Замена",
            "spare": "📦 Резерв",
            "missing": "❓ Не найдено",
            "removed": "⛔ Снят",
        }
        self.status_row = QWidget()
        row = QHBoxLayout(self.status_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.status_buttons: dict[str, QPushButton] = {}
        for status in STATUSES:
            code = status["code"]
            color = status["color"]
            button = QPushButton(labels.get(code, status["name"]))
            button.setCheckable(True)
            button.setMinimumHeight(38)
            button.setToolTip("Быстрый статус: не открывая «Подробно»")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                "QPushButton { border: 1px solid #3a4a5a; border-radius: 8px;"
                " background: #1b2733; color: #c8d6e5; font-size: 12px;"
                " padding: 4px 10px; }"
                f"QPushButton:hover {{ border-color: {color}; }}"
                f"QPushButton:checked {{ background: {color}; color: #101820;"
                " font-weight: bold; border-color: #0f1620; }"
            )
            button.clicked.connect(
                lambda _checked=False, c=code: self._apply_quick_status(c)
            )
            self.status_buttons[code] = button
            row.addWidget(button)
        row.addStretch(1)
        return self.status_row

    def _apply_quick_status(self, code: str) -> None:
        """Меняет статус активного устройства прямо с экрана обхода."""
        if not self.active_device:
            return
        current = self.repo.get_device(self.active_device.id)
        if current is None or current.status == code:
            self.refresh_active()
            return
        self.repo.update_device(self.active_device.id, status=code)
        self.refresh_active()

    def refresh_active(self) -> None:
        if self.active_device:
            self.active_device = self.repo.get_device(self.active_device.id)
        if not self.active_device:
            self.active_card.setPlainText(
                "Нет активного устройства. Отсканируйте или добавьте устройство."
            )
            for button in (self.note_btn, self.photo_btn, self.detail_btn, self.connection_btn):
                button.setEnabled(False)
            self.phone_btn.setEnabled(True)
            self.status_row.setVisible(False)
            self.photo_server.set_context("", "")
            return

        device = self.active_device
        lines = [
            f"<b>{escape(device.display_id)}</b> · {escape(device_type_name(device.device_type))}",
        ]
        model = " ".join(part for part in (device.manufacturer, device.model) if part)
        if model:
            lines.append(escape(model))
        identifiers = []
        if device.serial:
            identifiers.append(f"S/N: {escape(device.serial)}")
        if device.mac:
            identifiers.append(f"MAC: {escape(device.mac)}")
        if identifiers:
            lines.append(" · ".join(identifiers))
        lines.append(f"Статус: {escape(status_name(device.status))}")
        location = device.location.label() if device.location else ""
        if location:
            lines.append(f"📍 {escape(location)}")
        self.active_card.setHtml("<span>" + "<br>".join(lines) + "</span>")
        self.photo_server.set_context(device.display_id, location)
        self.detail_btn.setEnabled(True)
        self.photo_btn.setEnabled(True)
        self.connection_btn.setEnabled(True)
        self.note_btn.setEnabled(self.voice.available and not self._voice_recording)
        self.note_btn.setText("⏹ Стоп" if self._voice_recording else "🎤 Заметка")
        # Чипы статуса синхронизируем с карточкой: блокируем сигналы,
        # чтобы setChecked не запускал _apply_quick_status.
        self.status_row.setVisible(True)
        for code, button in self.status_buttons.items():
            button.setEnabled(True)
            button.blockSignals(True)
            button.setChecked(code == device.status)
            button.blockSignals(False)

    def refresh_recent(self) -> None:
        self.recent_list.clear()
        devices = self.repo.list_devices(self.session.id, limit=15)
        for device in devices:
            label = f"{device.display_id} · {device_type_name(device.device_type)}"
            if device.model:
                label += f" · {device.model}"
            if device.serial:
                label += f" · {device.serial}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, device.id)
            item.setToolTip(label)
            self.recent_list.addItem(item)
            if self.active_device and device.id == self.active_device.id:
                self.recent_list.setCurrentItem(item)
        if not devices:
            item = QListWidgetItem("Пока нет устройств")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recent_list.addItem(item)

    def refresh_counters(self) -> None:
        summary = self.repo.get_summary(self.session.id)
        current_name = device_type_name(self.current_type)
        current_count = next(
            (item["count"] for item in summary["by_type"] if item["code"] == self.current_type),
            0,
        )
        self.counters_label.setText(
            f"Всего: {summary['total']} · {current_name}: {current_count} · "
            f"Дублей: {summary['duplicate_groups']} · "
            f"Без S/N и MAC: {summary['no_serial_mac']}"
        )

    # ------------------------------------------------------------------
    # Сканирование
    # ------------------------------------------------------------------
    def activate_laser(self) -> None:
        if not self.ensure_location():
            return
        success = trigger_laser(
            self.config.scanner.laser_keys,
            self.config.scanner.laser_delay,
        )
        # Серия импульсов (как удержание аппаратной кнопки): ещё 2 пульса,
        # чтобы было время навести луч на этикетку.
        for delay_ms in (1000, 2000):
            QTimer.singleShot(
                delay_ms,
                lambda: trigger_laser(
                    self.config.scanner.laser_keys,
                    self.config.scanner.laser_delay,
                ),
            )
        beep_scan()
        if success:
            self.last_result.setText(
                "Лазер активирован (3 импульса). Наведите луч на этикетку и считайте код."
            )
        else:
            self.last_result.setText(
                "Сканер не отозвался на программный триггер. "
                "Используйте аппаратную кнопку сканера или введите код вручную."
            )

    def manual_scan(self) -> None:
        text = self.manual_edit.text().strip()
        if not text:
            return
        self.manual_edit.clear()
        self.process_code(text, source="manual")

    def process_code(self, raw: str, source: str = "com") -> None:
        if self.session.status != "active":
            QMessageBox.warning(self, "Сессия", "Сессия уже завершена")
            return
        if not self.ensure_location():
            return

        parsed = parse_code(raw)
        if parsed.code_type.value in ("mac", "serial") or (
            parsed.code_type.value == "qr" and parsed.fields
        ):
            color = "#2ecc71"
        elif parsed.code_type.value == "unknown":
            color = "#e67e22"
        else:
            color = "#f1c40f"
        self.last_result.setText(
            f"<span style='color:{color};'><b>Код:</b> {escape(parsed.raw)}</span><br>"
            f"<b>{escape(parsed.describe())}</b>"
        )

        matches = check_duplicates(self.repo, self.session.id, parsed=parsed)
        if matches:
            box = QMessageBox(self)
            box.setWindowTitle("Возможный дубль")
            box.setText("Найдены устройства с такими же данными:")
            box.setInformativeText("\n".join(match.message() for match in matches))
            open_btn = box.addButton("Открыть существующее", QMessageBox.ButtonRole.AcceptRole)
            add_btn = box.addButton("Добавить как новое", QMessageBox.ButtonRole.ActionRole)
            box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is open_btn:
                self.active_device = matches[0].device
                self.refresh_active()
                self._log(raw, parsed, "duplicate", matches[0].device.id)
                beep_error()
                return
            if clicked is not add_btn:
                self._log(raw, parsed, "ignored")
                return

        initial = self._initial_from_parsed(parsed)
        if self.quick_mode:
            self._create_from_initial(raw, parsed, initial)
        else:
            dialog = DeviceCardDialog(
                self.repo,
                self.session.id,
                self.paths,
                self.voice,
                location=self.current_location,
                initial=initial,
                parent=self,
            )
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.device:
                self.active_device = dialog.device
                self._log(raw, parsed, "created", dialog.device.id)
                beep_ok()
                self.refresh_all()
                self.toast.show_message(
                    f"Добавлено: {dialog.device.display_id}", "success"
                )
            else:
                self._log(raw, parsed, "ignored")

    def _initial_from_parsed(self, parsed) -> dict[str, Any]:
        initial: dict[str, Any] = {
            "scanned_code": parsed.raw,
            "code_type": parsed.code_type.value,
            "device_type": self.current_type,
        }
        for key in ("serial", "mac", "ip", "model", "manufacturer", "asset_tag"):
            if parsed.fields.get(key):
                initial[key] = parsed.fields[key]
        return initial

    def _create_from_initial(self, raw: str, parsed, initial: dict[str, Any]) -> None:
        note = None
        if parsed.code_type.value == "ean":
            note = f"Товарный код EAN: {parsed.ean}. Требует проверки."
        elif parsed.code_type.value == "unknown":
            note = f"Код не распознан: {parsed.raw}. Требует проверки."
        power = DEVICE_TYPE_BY_CODE[self.current_type].default_power
        try:
            device = self.repo.create_device(
                self.session.id,
                self.current_type,
                self.current_location.id,  # type: ignore[union-attr]
                serial=initial.get("serial"),
                mac=initial.get("mac"),
                ip=initial.get("ip"),
                model=initial.get("model"),
                manufacturer=initial.get("manufacturer"),
                asset_tag=initial.get("asset_tag"),
                power_type=power or None,
                note=note,
                scanned_code=initial.get("scanned_code"),
                code_type=initial.get("code_type"),
                is_verified=False,
            )
        except Exception as exc:  # noqa: BLE001
            self._log(raw, parsed, "error")
            beep_error()
            QMessageBox.critical(self, "Сканирование", f"Не удалось добавить устройство:\n{exc}")
            return
        self.active_device = device
        self._log(raw, parsed, "created", device.id)
        beep_ok()
        self.refresh_all()
        self.toast.show_message(f"Добавлено: {device.display_id}", "success")

    def _log(self, raw: str, parsed, action: str, device_id: str | None = None) -> None:
        try:
            self.repo.log_scan(
                self.session.id,
                raw,
                parsed.code_type.value,
                parsed.value,
                action,
                device_id,
            )
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Активное устройство
    # ------------------------------------------------------------------
    def _select_recent(self, item: QListWidgetItem) -> None:
        device_id = item.data(Qt.ItemDataRole.UserRole)
        if device_id:
            self.active_device = self.repo.get_device(device_id)
            self.refresh_active()

    def _open_recent(self, item: QListWidgetItem) -> None:
        self._select_recent(item)
        self.open_active_card()

    def add_manual_device(self) -> None:
        if not self.ensure_location():
            return
        dialog = DeviceCardDialog(
            self.repo,
            self.session.id,
            self.paths,
            self.voice,
            location=self.current_location,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.device:
            self.active_device = dialog.device
            beep_ok()
            self.refresh_all()
            self.toast.show_message(f"Добавлено: {dialog.device.display_id}", "success")

    def open_rack_dialog(self) -> None:
        if not self.ensure_location():
            return
        dialog = RackDialog(
            self.repo,
            self.session.id,
            self.paths,
            self.voice,
            location=self.current_location,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.created_devices:
            self.active_device = dialog.created_devices[-1]
            beep_ok()
            self.refresh_all()
            self.toast.show_message(
                f"🖥 Стойка: создано устройств — {dialog.created}",
                "success",
                timeout=10000,
            )

    def open_active_card(self) -> None:
        if not self.active_device:
            QMessageBox.information(self, "Устройство", "Сначала выберите или отсканируйте устройство")
            return
        dialog = DeviceCardDialog(
            self.repo,
            self.session.id,
            self.paths,
            self.voice,
            device=self.active_device,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.active_device = dialog.device
            self.refresh_all()

    def open_active_connection(self) -> None:
        if not self.active_device:
            QMessageBox.information(self, "Устройство", "Сначала выберите или отсканируйте устройство")
            return
        dialog = DeviceCardDialog(
            self.repo,
            self.session.id,
            self.paths,
            self.voice,
            device=self.active_device,
            parent=self,
        )
        dialog.tabs.setCurrentIndex(1)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.active_device = dialog.device
            self.refresh_all()

    def toggle_voice_note(self) -> None:
        if not self.active_device:
            QMessageBox.information(self, "Голос", "Сначала выберите устройство")
            return
        if not self._voice_recording:
            if not self.voice.available:
                QMessageBox.information(
                    self,
                    "Голос",
                    f"Голос недоступен:\n{self.voice.status}",
                )
                return
            if self.voice.start_recording():
                self._voice_recording = True
                self.note_btn.setText("⏹ Стоп")
                self._set_voice_status("Идёт запись...")
                beep_scan()
        else:
            if self.voice_recognizer and self.voice_recognizer.isRunning():
                return
            self._voice_recording = False
            self.note_btn.setEnabled(False)
            self.note_btn.setText("⏳ Распознавание...")
            self._set_voice_status("Обработка речи...")
            self.voice_recognizer = VoiceRecognizeWorker(self.voice, self)
            self.voice_recognizer.recognized.connect(self._on_voice_recognized)
            self.voice_recognizer.start()

    def _set_voice_status(self, text: str) -> None:
        """Статус голоса показываем только во время записи/распознавания."""
        self.voice_status_label.setText(text)
        self.voice_status_label.setVisible(bool(text))

    def _on_voice_recognized(self, text: str, status: str) -> None:
        self.note_btn.setText("🎤 Заметка")
        self._set_voice_status("")
        if text and self.active_device:
            note = (self.active_device.note or "").strip()
            self.active_device = self.repo.update_device(
                self.active_device.id,
                note=f"{note} {text}".strip(),
            )
            self.refresh_active()
            self.toast.show_message("Заметка добавлена", "success")
        elif not text:
            self.toast.show_message("Речь не распознана", "warning")
        self.voice_recognizer = None

    def open_phone_link(self) -> None:
        dialog = PhoneLinkDialog(self.photo_server, self)
        dialog.exec()

    def _on_phone_photo(self, path: str) -> None:
        if not self.active_device:
            # Нет активного устройства: пробуем опознать этикетку и создать
            # устройство по фото.
            self._start_photo_creation(path)
            return
        device = self.repo.get_device(self.active_device.id)
        if device is None:
            return
        self.repo.add_photo(device.id, path)
        self.repo.update_device(device.id, photo_path=path)
        self.refresh_active()
        self.toast.show_message(f"📷 Фото с телефона добавлено: {device.display_id}", "success")
        beep_ok()

    # ------------------------------------------------------------------
    # Создание устройства по фото этикетки с телефона
    # ------------------------------------------------------------------
    def _start_photo_creation(self, path: str) -> None:
        if self._photo_intel_worker is not None and self._photo_intel_worker.isRunning():
            # Предыдущее фото ещё разбирается — не начать двойную работу.
            self.toast.show_message("📱 Фото принято, предыдущее ещё разбирается", "warning")
            return
        if self.session.status != "active":
            self.toast.show_message(
                "📱 Фото принято, но сессия завершена — файл сохранён без привязки",
                "warning",
                timeout=10000,
            )
            return
        detach_running(self._photo_intel_worker)
        self._photo_intel_worker = None
        ok_intel, intel_reason = photo_intel_available()
        if not ok_intel:
            self.toast.show_message(
                "📱 Фото принято, но разбор этикетки недоступен: "
                + intel_reason + " — файл сохранён в data\\photos",
                "warning",
                timeout=12000,
            )
            return
        self._photo_intel_path = path
        self._photo_intel_worker = PhotoIntelWorker(path, self)
        self._photo_intel_worker.analysed.connect(self._on_photo_creation_intel)
        self.toast.show_message(
            "📱 Фото этикетки принято — разбираю (несколько секунд)...", "success", timeout=8000
        )
        self._photo_intel_worker.start()

    def _on_photo_creation_intel(self, intel, path: str) -> None:
        self._photo_intel_worker = None
        if self.session.status != "active":
            self.toast.show_message(
                "📱 Этикетка распознана, но сессия уже завершена — файл сохранён без привязки",
                "warning",
            )
            beep_error()
            return
        if intel is None or intel.error or not intel.has_candidates:
            self.toast.show_message(
                "📱 Фото с телефона принято, но этикетка не распознана —"
                " файл сохранён без привязки",
                "warning",
                timeout=12000,
            )
            beep_error()
            return
        if not self.ensure_location():
            return
        summary = intel.summary()
        vendor = lookup_mac_vendor(intel.macs[0]) if intel.macs else None
        reply = QMessageBox.question(
            self,
            "Создать устройство по фото?",
            f"На фото распознаны кандидаты:\n\n{summary}\n"
            + (f"\nВендор по MAC: {vendor}\n" if vendor else "")
            + "\nСоздать новое устройство с этими данными?\n\n"
            "Да — откроется карточка с заполненными полями и фото.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self.toast.show_message("Фото сохранено в data\\photos без привязки", "warning")
            return
        initial = {
            "device_type": self.current_type,
            "photo_path": path,
            "note_text": "Создано по фото этикетки (телефон) — проверьте поля.",
        }
        if intel.serials:
            initial["serial"] = intel.serials[0]
        if intel.macs:
            initial["mac"] = intel.macs[0]
        if intel.models:
            initial["model"] = intel.models[0]
        if intel.ip_addresses:
            initial["ip"] = intel.ip_addresses[0]
        if vendor:
            initial["manufacturer"] = vendor
        dialog = DeviceCardDialog(
            self.repo,
            self.session.id,
            self.paths,
            self.voice,
            location=self.current_location,
            initial=initial,
            parent=self,
        )
        accepted = dialog.exec() == QDialog.DialogCode.Accepted and dialog.device
        self._photo_intel_path = None
        if accepted:
            self.active_device = dialog.device
            beep_ok()
            self.refresh_all()
            self.toast.show_message(f"Добавлено: {dialog.device.display_id}", "success")
            try:
                self.repo.log_scan(self.session.id, Path(path).name, "photo", None, "created", dialog.device.id)
            except Exception:  # noqa: BLE001 — журнал не критичен
                pass
        else:
            self.toast.show_message("Фото сохранено в data\\photos без привязки", "warning")

    def take_photo(self) -> None:
        if not self.active_device:
            QMessageBox.information(self, "Фото", "Сначала выберите устройство")
            return
        dialog = PhotoCaptureDialog(self.paths["photos"], self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.photo_path:
            self.repo.add_photo(self.active_device.id, dialog.photo_path)
            self.active_device = self.repo.update_device(
                self.active_device.id, photo_path=dialog.photo_path
            )
            self.refresh_active()
            self.toast.show_message("Фото прикреплено", "success")

    # ------------------------------------------------------------------
    # Навигация
    # ------------------------------------------------------------------
    def open_device_list(self) -> None:
        dialog = DeviceListDialog(
            self.repo,
            self.session.id,
            self.paths,
            self.voice,
            self.current_location,
            self,
        )
        dialog.devices_changed.connect(self.refresh_all)
        dialog.exec()
        self.refresh_all()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self.paths, self.voice, self.repo, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.restart_scanner()
            self._start_voice_loader()
            self.refresh_all()
            self.toast.show_message("Настройки сохранены", "success")

    def open_help(self) -> None:
        HelpDialog(self).exec()

    def finish_session(self) -> None:
        dialog = FinishDialog(self.repo, self.session, self.paths, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            detach_running(self._photo_intel_worker)
            self._photo_intel_worker = None
            self.stop_scanner()
            self.photo_server.stop()
            self.voice.cancel_recording()
            self.session_finished.emit()
        else:
            self.refresh_all()

    def detach_intel_worker(self) -> None:
        """Отпустить фоновой разбор этикетки при закрытии экрана обхода."""
        detach_running(self._photo_intel_worker)
        self._photo_intel_worker = None

    # ------------------------------------------------------------------
    # Сканер
    # ------------------------------------------------------------------
    def restart_scanner(self) -> None:
        self.stop_scanner()
        mode = self.config.scanner.mode
        if mode == "com":
            self.scanner_label.setText(f"Сканер: {self.config.scanner.port}...")
            self.scanner_label.setStyleSheet("font-size: 13px; color: #f1c40f;")
            self.scanner_thread = ScannerThread(self.config.scanner, self)
            self.scanner_thread.code_scanned.connect(self.process_code)
            self.scanner_thread.status_changed.connect(self._on_scanner_status)
            self.scanner_thread.start()
        elif mode == "hid":
            self.scanner_label.setText("Сканер: HID (клавиатура)")
            self.scanner_label.setStyleSheet("font-size: 13px; color: #2ecc71;")
            self.setFocus()
        else:
            self.scanner_label.setText("Сканер: ручной ввод")
            self.scanner_label.setStyleSheet("font-size: 13px; color: #9fb3c8;")
        self.hid_buffer = KeyboardScannerBuffer(
            max_interval=0.1,
            strip_prefix=self.config.scanner.strip_prefix,
            strip_suffix=self.config.scanner.strip_suffix,
        )

    def stop_scanner(self) -> None:
        if self.scanner_thread:
            self.scanner_thread.stop()
            self.scanner_thread = None

    def _on_scanner_status(self, message: str, connected: bool) -> None:
        self.scanner_label.setText(f"Сканер: {'OK' if connected else 'нет'}")
        self.scanner_label.setToolTip(message)
        self.scanner_label.setStyleSheet(
            f"font-size: 13px; color: {'#2ecc71' if connected else '#e67e22'};"
        )

    def eventFilter(self, obj, event) -> bool:
        if (
            event.type() == QEvent.Type.KeyPress
            and self.config.scanner.mode == "hid"
            and self.isVisible()
            and QApplication.activeModalWidget() is None
        ):
            focus = QApplication.focusWidget()
            if not isinstance(focus, (QLineEdit, QTextEdit, QComboBox, QAbstractSpinBox)):
                text = event.text()
                if text:
                    code = self.hid_buffer.feed(text)
                    if code:
                        self.process_code(code, source="hid")
                        return True
        return super().eventFilter(obj, event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QApplication.instance().installEventFilter(self)
        self.setFocus()

    def hideEvent(self, event) -> None:
        QApplication.instance().removeEventFilter(self)
        super().hideEvent(event)

    # ------------------------------------------------------------------
    # Голос и батарея
    # ------------------------------------------------------------------
    def _start_voice_loader(self) -> None:
        if not self.config.voice.enabled:
            self.voice_label.setText("Голос: отключён")
            self.voice_label.setStyleSheet("font-size: 13px; color: #9fb3c8;")
            return
        if self.voice.available:
            self.voice_label.setText("Голос: готов")
            self.voice_label.setStyleSheet("font-size: 13px; color: #2ecc71;")
            self.refresh_active()
            return
        if self.voice_loader and self.voice_loader.isRunning():
            return
        self.voice_label.setText("Голос: загрузка...")
        self.voice_label.setStyleSheet("font-size: 13px; color: #f1c40f;")
        self.voice_loader = VoiceLoadWorker(self.voice, self)
        self.voice_loader.loaded.connect(self._on_voice_loaded)
        self.voice_loader.start()

    def _on_voice_loaded(self, ok: bool, status: str) -> None:
        self.voice_label.setText(f"Голос: {'готов' if ok else 'недоступен'}")
        self.voice_label.setStyleSheet(
            f"font-size: 13px; color: {'#2ecc71' if ok else '#e67e22'};"
        )
        self.voice_label.setToolTip(status)
        self.refresh_active()
        self.voice_loader = None

    def _update_battery(self) -> None:
        try:
            import psutil

            battery = psutil.sensors_battery()
            if battery is None:
                self.battery_label.setText("🔋 сеть")
                return
            percent = int(battery.percent)
            icon = "🔋" if percent > 20 else "🪫"
            self.battery_label.setText(f"{icon} {percent}%")
            color = "#2ecc71"
            if percent <= self.config.low_battery_warning:
                color = "#e67e22"
            if percent <= self.config.low_battery_critical:
                color = "#e74c3c"
            self.battery_label.setStyleSheet(f"font-size: 13px; color: {color};")
            if (
                percent <= self.config.low_battery_critical
                and not self._battery_warned
                and not battery.power_plugged
            ):
                self._battery_warned = True
                self.toast.show_message(
                    f"Критический заряд: {percent}%. Подключите питание!",
                    "error",
                    timeout=10000,
                )
        except Exception:  # noqa: BLE001
            self.battery_label.setText("🔋 —")

    # ------------------------------------------------------------------
    # Закрытие
    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        self.stop_scanner()
        self.photo_server.stop()
        if self._voice_recording:
            self.voice.cancel_recording()
        self._battery_timer.stop()
        try:
            QApplication.instance().removeEventFilter(self)
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)

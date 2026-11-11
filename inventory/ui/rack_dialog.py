# inventory/ui/rack_dialog.py
"""Черновик стойки: фото → список видимых юнитов → пачка устройств.

Например: сфотографировали серверную стойку телефоном — приложение
(локальная ollama, офлайн) распознаёт видимые юниты, вы показываете
редактируемую таблицу, отмечаете позиции и создаёте устройства пачкой
в текущей локации. Детали (S/N, MAC, порты) потом добираются лазером
или крупным кадром этикетки («🔍 Этикетка» в карточке).
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from inventory.config import DEVICE_TYPES, DEVICE_TYPE_BY_CODE
from inventory.core.repository import InventoryRepository
from inventory.services.rack_ai import (
    RackAnalysis,
    RackDevice,
    ollama_status,
    position_sort_key,
)
from inventory.services.voice import VoiceService
from inventory.ui.photo_dialog import PhotoCaptureDialog
from inventory.ui.rack_workers import RackAiWorker, WarmupWorker, detach_running
from inventory.ui.widgets import big_button, secondary_button

_TYPE_INDEX = {t.code: i for i, t in enumerate(DEVICE_TYPES)}

_COL_CHECK, _COL_TYPE, _COL_NAME, _COL_POS, _COL_NOTE = 0, 1, 2, 3, 4
_HEADERS = ["", "Тип", "Бренд/модель", "Юнит", "Что видно"]


class RackDialog(QDialog):
    """Фото стойки → редактируемый список → «Создать выбранные»."""

    def __init__(
        self,
        repo: InventoryRepository,
        session_id: str,
        paths: dict[str, Path],
        voice: VoiceService | None = None,
        *,
        location=None,
        photo_path: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.session_id = session_id
        self.paths = paths
        self.voice = voice
        self.location = location
        self._photo_path = photo_path
        self._analysis: RackAnalysis | None = None
        self._worker: RackAiWorker | None = None
        self._warm_worker: WarmupWorker | None = None
        self._model_name = ""
        self.created: int = 0
        self.created_devices: list = []

        self.setWindowTitle("🖥 Стойка по фото (ИИ)")
        self.setMinimumSize(980, 640)
        self._build_ui()
        self._begin()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(8)

        hint = QLabel(
            "Фото стойки → список видимых юнитов. Проверьте строки: снимите "
            "галочку с мусора, поправьте тип/модель. Созданные устройства "
            "получат пометку «проверить»; S/N и MAC потом добавляются "
            "лазером или «🔍 Этикеткой» крупным кадром."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9fb3c8;")
        root.addWidget(hint)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #f1c40f; font-weight: bold;")
        root.addWidget(self.status_label)

        self.table = QTableWidget(0, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.setColumnWidth(_COL_CHECK, 28)
        self.table.setColumnWidth(_COL_TYPE, 190)
        self.table.setColumnWidth(_COL_NAME, 260)
        self.table.setColumnWidth(_COL_POS, 90)
        root.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.shot_btn = secondary_button("🔁 Ещё кадр", height=52, font_size=15)
        self.shot_btn.setToolTip("Разобрать второй кадр стойки и дополнить список")
        self.shot_btn.clicked.connect(self._another_shot)
        buttons.addWidget(self.shot_btn)
        self.row_btn = secondary_button("➕ Строка", height=52, font_size=15)
        self.row_btn.setToolTip("Вписать юнит вручную, если модель его пропустила")
        self.row_btn.clicked.connect(self._add_empty_row)
        buttons.addWidget(self.row_btn)
        buttons.addStretch(1)
        self.cancel_btn = secondary_button("Отмена", height=52, font_size=15)
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_btn)
        self.create_btn = big_button(
            "💾 Создать выбранные", "#2e7d32", height=56, font_size=16
        )
        self.create_btn.setEnabled(False)
        self.create_btn.clicked.connect(self._create_selected)
        buttons.addWidget(self.create_btn, 2)
        root.addLayout(buttons)

    # ------------------------------------------------------------------
    # Поток работы
    # ------------------------------------------------------------------
    def _begin(self) -> None:
        ok, info = ollama_status()
        if not ok:
            self.status_label.setText(f"❌ Распознавание недоступно: {info}")
            self.status_label.setStyleSheet("color: #e67e22;")
            self.shot_btn.setEnabled(False)
            QMessageBox.information(
                self,
                "Стойка по фото",
                "ИИ-распознавание стойки недоступно:\n" + info +
                "\n\nМожно вписать юниты вручную кнопкой «➕ Строка».",
            )
            return
        self.status_label.setText(f"Модель: {info} — готова")
        self.status_label.setStyleSheet("color: #2ecc71;")
        self._start_warmup()
        if self._photo_path:
            self._start_analysis(self._photo_path)
        else:
            self._another_shot()

    def _start_warmup(self) -> None:
        """Модель загружается в память заранее — к моменту щелчка камеры."""
        detach_running(self._warm_worker)
        self._warm_worker = WarmupWorker(self._model_name, self)
        self._warm_worker.warmed.connect(self._on_warmed)
        self._warm_worker.start()

    def _on_warmed(self, model: str, error: str, was_cold: bool) -> None:
        detach_running(self._warm_worker)
        self._warm_worker = None
        if error:
            return  # разбор уже покажет причину, не дублируем
        busy = self._worker is not None and self._worker.isRunning()
        if busy:
            return  # не перекрывать статус анализа
        text = "🔥 Модель в памяти — стойка готова к съёмке"
        if was_cold:
            text = "🔥 Модель загружена в память (была холодной) — можно снимать"
        self.status_label.setText(text)
        self.status_label.setStyleSheet("color: #2ecc71;")

    def _another_shot(self) -> None:
        dialog = PhotoCaptureDialog(self.paths["photos"], self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.photo_path:
            if not self.table.rowCount():
                return  # второй выбор с пустой таблицей — отменяем выбор кадра
            return
        self._start_analysis(dialog.photo_path)

    def _start_analysis(self, path: str) -> None:
        detach_running(self._worker)
        self._worker = RackAiWorker(path, self, log_dir=self.paths.get("logs"))
        self._worker.analysed.connect(self._on_analysis)
        self._set_busy(True)
        self._worker.start()

    def _set_busy(self, busy: bool) -> None:
        self.shot_btn.setEnabled(not busy)
        self.row_btn.setEnabled(not busy)
        self.create_btn.setEnabled(False)
        if busy:
            self.status_label.setText(
                "🖥 Ищу юниты на фото (локальный ИИ, на CPU до 2–3 минут)..."
            )
            self.status_label.setStyleSheet("color: #f1c40f;")

    def _on_analysis(self, analysis, path: str) -> None:
        detach_running(self._worker)
        self._worker = None
        self.shot_btn.setEnabled(True)
        self.row_btn.setEnabled(True)
        if analysis is None:
            return
        self._analysis = analysis
        seconds = f"{analysis.elapsed_s:.0f} с"
        if not analysis.ok:
            self.status_label.setText(f"❌ Разбор не удался: {analysis.error}")
            self.status_label.setStyleSheet("color: #e67e22;")
            return
        if not analysis.devices:
            self.status_label.setText(
                f"🤷 Модель не нашла устройств ({seconds}) — возможно, кадр "
                "не весь попал: снимите стойку целиком и попробуйте «🔁 Ещё кадр». "
                "Или добавьте строки вручную."
            )
            self.status_label.setStyleSheet("color: #e67e22;")
            return
        added = 0
        for device in analysis.devices:
            if self._row_for_device(device) >= 0:
                continue  # такой юнит уже есть в таблице
            self._append_row(device)
            added += 1
        if added:
            self._order_rows_by_position()
        extra = f" (добавлено строк: {added})" if added < len(analysis.devices) else ""
        self.status_label.setText(
            f"✅ Найдено юнитов: {len(analysis.devices)}{extra} · {seconds} · "
            f"{Path(path).name}"
        )
        self.status_label.setStyleSheet("color: #2ecc71;")
        self.create_btn.setEnabled(self.table.rowCount() > 0)
        self._update_create_caption()

    # ------------------------------------------------------------------
    # Таблица
    # ------------------------------------------------------------------
    def _row_for_device(self, device: RackDevice) -> int:
        """Дубль определяет ПОЗИЦИЯ, а не описание.

        Разные юниты одной модели (U5 и U6) — законные дубликаты, их нельзя
        терять. Совпадение title+detail используется только когда позиция
        неизвестна хотя бы у одного из сравниваемых.
        """
        for row in range(self.table.rowCount()):
            pos_item = self.table.item(row, _COL_POS)
            row_pos = (pos_item.text().strip() if pos_item else "")
            device_pos = (device.position or "").strip()
            if device_pos and row_pos:
                if row_pos.lower() != device_pos.lower():
                    continue  # позиции известны и различаются → не дубль
                title = self.table.item(row, _COL_NAME)
                if title and title.text().strip() == device.title:
                    return row
                continue
            title = self.table.item(row, _COL_NAME)
            note = self.table.item(row, _COL_NOTE)
            if (
                title
                and title.text().strip() == device.title
                and note
                and note.text().strip() == device.detail
            ):
                return row
        return -1

    def _append_row(self, device: RackDevice | None = None) -> int:
        rd = {
            "type": device.type_code if device else "other",
            "name": device.title if device else "",
            "brand": device.brand if device else "",
            "pos": device.position if device else "",
            "note": device.detail if device else "",
            "checked": True,
        }
        return self._append_row_data(rd)

    def _append_row_data(self, rd: dict) -> int:
        row = self.table.rowCount()
        self.table.insertRow(row)
        checked = (
            Qt.CheckState.Checked if rd.get("checked", True) else Qt.CheckState.Unchecked
        )
        check = QTableWidgetItem()
        check.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
        )
        check.setCheckState(checked)
        self.table.setItem(row, _COL_CHECK, check)

        type_combo = QComboBox()
        for device_type in DEVICE_TYPES:
            type_combo.addItem(device_type.icon + " " + device_type.display_name, device_type.code)
        self.table.setCellWidget(row, _COL_TYPE, type_combo)
        index = _TYPE_INDEX.get(rd.get("type") or "other")
        if index is not None:
            type_combo.setCurrentIndex(index)

        name_item = QTableWidgetItem(str(rd.get("name") or ""))
        name_item.setData(Qt.ItemDataRole.UserRole, rd.get("brand") or "")
        self.table.setItem(row, _COL_NAME, name_item)
        self.table.setItem(row, _COL_POS, QTableWidgetItem(str(rd.get("pos") or "")))
        self.table.setItem(row, _COL_NOTE, QTableWidgetItem(str(rd.get("note") or "")))

        # Коннект после смены индекса: автозаполнение не трогает готовое имя.
        type_combo.currentIndexChanged.connect(lambda _i, r=row: self._on_type_changed(r))
        return row

    def _rows_snapshot(self) -> list[dict]:
        """Все данные строк (без виджетов) — для пересборки/сортировки."""
        rows = []
        for r in range(self.table.rowCount()):
            combo = self.table.cellWidget(r, _COL_TYPE)
            name_item = self.table.item(r, _COL_NAME)
            pos_item = self.table.item(r, _COL_POS)
            note_item = self.table.item(r, _COL_NOTE)
            check = self.table.item(r, _COL_CHECK)
            rows.append(
                {
                    "type": combo.currentData() if combo else "other",
                    "name": name_item.text() if name_item else "",
                    "brand": (
                        str(name_item.data(Qt.ItemDataRole.UserRole) or "")
                        if name_item else ""
                    ),
                    "pos": pos_item.text() if pos_item else "",
                    "note": note_item.text() if note_item else "",
                    "checked": bool(
                        check and check.checkState() == Qt.CheckState.Checked
                    ),
                }
            )
        return rows

    def _rewrite_rows(self, rows: list[dict]) -> None:
        self.table.setRowCount(0)
        for rd in rows:
            self._append_row_data(rd)

    def _order_rows_by_position(self) -> None:
        """Юниты по порядку: U1, U2 … U5-U8; ручные и пустые — в конце."""
        self._rewrite_rows(
            sorted(self._rows_snapshot(), key=lambda rd: position_sort_key(rd["pos"]))
        )

    def _on_type_changed(self, row: int) -> None:
        combo = self.table.cellWidget(row, _COL_TYPE)
        if combo is None:
            return
        code = combo.currentData()
        device_type = DEVICE_TYPE_BY_CODE.get(code)
        name_item = self.table.item(row, _COL_NAME)
        if device_type and name_item and not name_item.text().strip():
            name_item.setText(device_type.display_name)

    def _add_empty_row(self) -> None:
        self._append_row(None)
        self._order_rows_by_position()
        self.create_btn.setEnabled(True)
        self._update_create_caption()

    def _update_create_caption(self) -> None:
        selected = self._selected_rows()
        self.create_btn.setText(f"💾 Создать выбранные ({selected})")

    def _selected_rows(self) -> int:
        count = 0
        for row in range(self.table.rowCount()):
            item = self.table.item(row, _COL_CHECK)
            if item and item.checkState() == Qt.CheckState.Checked:
                count += 1
        return count

    # ------------------------------------------------------------------
    # Создание устройств пачкой
    # ------------------------------------------------------------------
    def _create_selected(self) -> None:
        if self.location is None:
            QMessageBox.warning(
                self, "Стойка", "Локация не выбрана — устройство создавать некуда."
            )
            return
        created = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, _COL_CHECK)
            if not item or item.checkState() != Qt.CheckState.Checked:
                continue
            combo = self.table.cellWidget(row, _COL_TYPE)
            code = combo.currentData() if combo else "other"
            note_parts = []
            position = (self.table.item(row, _COL_POS).text() or "").strip()
            if position:
                note_parts.append(f"В стойке: {position}")
            seen = (self.table.item(row, _COL_NOTE).text() or "").strip()
            if seen:
                note_parts.append(seen)
            note_parts.append("Создано по фото стойки (ИИ-черновик) — проверить.")
            device_type = DEVICE_TYPE_BY_CODE.get(code)
            name_item = self.table.item(row, _COL_NAME)
            text = (name_item.text() or "").strip()
            brand = str(name_item.data(Qt.ItemDataRole.UserRole) or "")
            if brand and text.lower().startswith(brand.lower()):
                # «D-Link DES-1210» → бренд D-Link, модель DES-1210.
                pass_brand = brand
                pass_model = text[len(brand):].strip() or None
            elif self._looks_like_model(text):
                pass_brand = None
                pass_model = text
            else:
                pass_brand = None
                pass_model = None
            try:
                device = self.repo.create_device(
                    self.session_id,
                    code,
                    self.location.id,
                    power_type=device_type.default_power if device_type else None,
                    manufacturer=pass_brand,
                    model=pass_model,
                    note=" · ".join(note_parts),
                    is_verified=False,
                )
            except Exception as exc:  # noqa: BLE001 — не роняем пакетное создание
                QMessageBox.warning(self, "Создание", f"Не удалось создать устройство:\n{exc}")
                continue
            created.append(device)
        if not created:
            QMessageBox.information(self, "Создание", "Ни одна строка не выбрана")
            return
        self.created = len(created)
        self.created_devices = created
        self.accept()

    @staticmethod
    def _looks_like_model(text: str) -> bool:
        """Отграничить «свободную» подпись вида «панель портов» от модели."""
        if not text:
            return False
        letters = sum(1 for c in text if c.isalnum())
        return letters >= 4 and any(c.isdigit() or c.isupper() for c in text)

    # ------------------------------------------------------------------
    def reject(self) -> None:
        detach_running(self._worker)
        self._worker = None
        detach_running(self._warm_worker)
        self._warm_worker = None
        super().reject()

    def closeEvent(self, event) -> None:
        detach_running(self._worker)
        self._worker = None
        detach_running(self._warm_worker)
        self._warm_worker = None
        super().closeEvent(event)

# inventory/ui/import_session_dialog.py
"""Диалог импорта сессии из другой SQLite-базы (второй ноутбук)."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from inventory.core.repository import InventoryRepository
from inventory.core.session_import import (
    CONFLICT_APPEND,
    CONFLICT_FILL,
    CONFLICT_SKIP,
    import_session,
    list_source_sessions,
)

SOURCE_LABELS = {
    CONFLICT_SKIP: "Пропустить исходные дубли",
    CONFLICT_APPEND: "Добавить как новые записи",
    CONFLICT_FILL: "Заполнить пустые поля существующих",
}
MODE_HINTS = {
    CONFLICT_SKIP: (
        "Устройство с таким же S/N или MAC уже записано — исходная запись не переносится."
    ),
    CONFLICT_APPEND: (
        "Обе записи остаются; пара попадёт в «👥 Дубликаты» — разберёте по карточкам."
    ),
    CONFLICT_FILL: (
        "Существующая запись остаётся; из исходной переносятся только пустые поля."
    ),
}


class ImportSessionDialog(QDialog):
    """Выбор базы и сессии источника, режима конфликтов и запуск импорта."""

    def __init__(
        self,
        repo: InventoryRepository,
        paths: dict[str, Path],
        session,
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.paths = paths
        self.session = session
        self.source_path: Path | None = None
        self._source_sessions = []
        self._active_sources: dict[str, str] = {}

        self.setWindowTitle("Импорт сессии из другой базы")
        self.setMinimumWidth(720)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel(f"Импорт в сессию «{self.session.name}»")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        layout.addWidget(title)

        intro = QLabel(
            "С ноутбука-напарника возьмите его базу (data\\inventory.db) и папку "
            "data\\photos — фото перенесутся из неё. Действия происходят только "
            "в вашей базе, файл напарника не изменяется."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #9fb3c8;")
        layout.addWidget(intro)

        # --- Шаг 1: файл -------------------------------------------------
        file_row = QHBoxLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        self.file_edit.setPlaceholderText("Путь к базе напарника (*.db)")
        file_row.addWidget(self.file_edit, 1)
        self.browse_btn = QPushButton("📂 Обзор…")
        self.browse_btn.clicked.connect(self._pick_file)
        file_row.addWidget(self.browse_btn)
        layout.addLayout(file_row)

        # --- Шаг 2: сессия источника ------------------------------------
        form = QFormLayout()
        self.session_combo = QComboBox()
        self.session_combo.setEnabled(False)
        self.session_combo.currentIndexChanged.connect(self._update_conflict_info)
        form.addRow("Сессия источника:", self.session_combo)
        layout.addLayout(form)

        self.conflicts_label = QLabel("")
        self.conflicts_label.setStyleSheet("color: #9fb3c8;")
        layout.addWidget(self.conflicts_label)

        # --- Шаг 3: режим для совпадений ---------------------------------
        self.mode_group = QGroupBox("Что делать с совпадениями по S/N или MAC")
        mode_layout = QVBoxLayout(self.mode_group)
        self.mode_buttons = QButtonGroup(self)
        self._mode_radios = {}
        for code in (CONFLICT_SKIP, CONFLICT_APPEND, CONFLICT_FILL):
            radio = QRadioButton(SOURCE_LABELS[code])
            self.mode_buttons.addButton(radio)
            mode_layout.addWidget(radio)
            self._mode_radios[code] = radio
        self._mode_radios[CONFLICT_SKIP].setChecked(True)
        self.mode_hint = QLabel(MODE_HINTS[CONFLICT_SKIP])
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setStyleSheet("color: #9fb3c8;")
        mode_layout.addWidget(self.mode_hint)
        for code, radio in self._mode_radios.items():
            radio.toggled.connect(self._on_mode_changed)
        self.mode_group.setEnabled(False)
        layout.addWidget(self.mode_group)

        # --- Кнопки --------------------------------------------------------
        buttons = QHBoxLayout()
        self.import_btn = QPushButton("⤵ Импортировать")
        self.import_btn.setEnabled(False)
        self.import_btn.setStyleSheet(
            "font-size: 17px; padding: 12px 28px; background-color: #2980b9; "
            "color: white; border-radius: 6px;"
        )
        self.import_btn.clicked.connect(self._import)
        buttons.addWidget(self.import_btn)
        buttons.addStretch(1)
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------
    def _pick_file(self) -> None:
        start_dir = str(self.source_path or self.paths["data"])
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "База напарника",
            start_dir,
            "Базы инвентаризации (*.db);;Все файлы (*)",
        )
        if not selected:
            return
        self._load_file(Path(selected))

    def _load_file(self, path: Path) -> None:
        try:
            sessions = list_source_sessions(path)
        except ValueError as exc:
            QMessageBox.warning(self, "Импорт", str(exc))
            return
        self.source_path = path
        self.file_edit.setText(str(path))
        self._source_sessions = sessions
        self.session_combo.clear()
        self._active_sources = {}
        for item in sessions:
            status_note = " (завершена)" if item.status != "active" else ""
            label = (
                f"{item.object_name} · {item.name} · {item.inspector} · "
                f"устройств: {item.device_count}{status_note}"
            )
            self.session_combo.addItem(label, item.id)
            self._active_sources[item.id] = item.status
        self.session_combo.setEnabled(bool(sessions))
        self.conflicts_label.setText(
            "" if sessions else "В этой базе активных сессий нет"
        )
        self._update_conflict_info()

    def _on_mode_changed(self) -> None:
        for code, radio in self._mode_radios.items():
            if radio.isChecked():
                self.mode_hint.setText(MODE_HINTS[code])
                return

    def _update_conflict_info(self) -> None:
        """Пересчитывает совпадения между выбранной сессией источника и текущей."""
        source_id = self.session_combo.currentData()
        enable = bool(self.source_path and source_id)
        self.mode_group.setEnabled(enable)
        self.import_btn.setEnabled(False)
        if not enable:
            self.conflicts_label.setText("")
            return
        conflicts = self._count_conflicts(source_id)
        self.conflicts_label.setText(
            f"Совпадений по S/N или MAC с текущей сессией: {conflicts}"
        )
        self._current_conflicts = conflicts
        self.import_btn.setEnabled(True)

    def _count_conflicts(self, source_id: str) -> int:
        if not self.source_path:
            return 0
        try:
            source_rows = self._read_source_devices(source_id)
        except Exception:  # noqa: BLE001 — любая ошибка чтения блокирует импорт ниже
            return 0
        from inventory.core.parser import normalize_mac_compact, normalize_serial

        mine_serial = {
            device.serial_norm for device in self.repo.list_devices(self.session.id)
            if device.serial_norm
        }
        mine_mac = {
            device.mac_norm for device in self.repo.list_devices(self.session.id)
            if device.mac_norm
        }
        count = 0
        seen: set[str] = set()
        for row in source_rows:
            serial_norm = normalize_serial(row["serial"])
            mac_norm = normalize_mac_compact(row["mac"])
            hit = (serial_norm and serial_norm in mine_serial) or (
                mac_norm and mac_norm in mine_mac
            )
            if hit:
                seen.add(row["id"])
                count = len(seen)
        return count

    def _read_source_devices(self, source_id: str):
        import sqlite3

        connection = sqlite3.connect(f"file:{self.source_path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            return connection.execute(
                "SELECT * FROM devices WHERE session_id = ? AND deleted_at IS NULL",
                (source_id,),
            ).fetchall()
        finally:
            connection.close()

    # ------------------------------------------------------------------
    def _import(self) -> None:
        source_id = self.session_combo.currentData()
        if not self.source_path or not source_id:
            return
        mode = next(
            code
            for code, radio in self._mode_radios.items()
            if radio.isChecked()
        )
        photos_dir = self.source_path.parent / "photos"
        if not photos_dir.exists():
            photos_dir = None
        try:
            report = import_session(
                self.repo,
                self.session.id,
                self.source_path,
                source_id,
                photos_dir=photos_dir,
                on_conflict=mode,
            )
        except ValueError as exc:
            QMessageBox.critical(self, "Импорт", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — показать причину пользователю
            QMessageBox.critical(
                self, "Импорт", f"Не удалось импортировать: {exc}"
            )
            return

        if report.photo_failures:
            failure_names = ", ".join(report.photo_failures[:5])
            suffix = "" if len(report.photo_failures) <= 5 else f" (и ещё {len(report.photo_failures) - 5})"
            QMessageBox.warning(
                self,
                "Импорт",
                "Не удалось скопировать файлы фото:\n"
                f"{failure_names}{suffix}\n\n"
                "Проверьте, что рядом с базой лежит папка photos ноутбука-напарника.",
            )

        message = f"Импорт завершён.\n\n{report.summary()}"
        diff_count = len(report.fill_differences)
        if mode == CONFLICT_FILL and report.fill_differences:
            shown = "\n".join(report.fill_differences[:10])
            more = "" if diff_count <= 10 else f"\n… и ещё {diff_count - 10}"
            message += f"\n\nРасхождения:\n{shown}{more}"
        QMessageBox.information(self, "Импорт", message)
        self.accept()

# inventory/ui/start_screen.py
"""Стартовый экран: активная сессия, создание новой, история."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from inventory.core.export_excel import export_session, suggested_export_path
from inventory.core.models import Session
from inventory.core.repository import InventoryRepository
from inventory.services.backup import backup_database, prune_backups
from inventory.ui.import_session_dialog import ImportSessionDialog
from inventory.ui.widgets import big_button, format_datetime, secondary_button

SESSION_STATUS_NAMES = {
    "active": "Активна",
    "completed": "Завершена",
    "archived": "В архиве",
}


class SessionHistoryDialog(QDialog):
    session_reopened = pyqtSignal(object)  # Session, открытая для продолжения

    def __init__(
        self,
        repo: InventoryRepository,
        paths: dict[str, Path] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.paths = paths
        self.sessions: list[Session] = repo.list_sessions(limit=200)
        self.exported_path: Path | None = None
        self.setWindowTitle("История сессий")
        self.setMinimumSize(1080, 560)
        self._build_ui()
        self._fill_table()
        self._update_buttons()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("История сессий")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)
        hint = QLabel(
            "Выберите сессию: выгрузите её в Excel или откройте снова, "
            "чтобы продолжить обход и добавлять устройства."
        )
        hint.setStyleSheet("color: #9fb3c8;")
        layout.addWidget(hint)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Дата", "Проект", "Объект", "Инспектор", "Устройств", "Статус"]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.itemDoubleClicked.connect(lambda _item: self._continue_selected())
        layout.addWidget(self.table, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #9fb3c8;")
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.continue_btn = big_button(
            "▶ Продолжить (добавить устройства)", "#2e7d32", height=52, font_size=15
        )
        self.continue_btn.setToolTip(
            "Открыть выбранную сессию снова: сканируйте устройства в её список"
        )
        self.continue_btn.clicked.connect(self._continue_selected)
        buttons.addWidget(self.continue_btn)
        self.export_btn = big_button("📊 Экспорт Excel", "#8e44ad", height=52, font_size=16)
        self.export_btn.clicked.connect(self._export)
        buttons.addWidget(self.export_btn)
        self.open_folder_btn = secondary_button("📂 Папка экспорта", height=52, font_size=15)
        self.open_folder_btn.clicked.connect(self._open_folder)
        buttons.addWidget(self.open_folder_btn)
        buttons.addStretch(1)
        close_btn = big_button("Закрыть", "#3b4a5f", height=52, font_size=16)
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn, 1)
        layout.addLayout(buttons)

    def _fill_table(self) -> None:
        self.table.setRowCount(len(self.sessions))
        for row, session in enumerate(self.sessions):
            values = [
                format_datetime(session.started_at),
                session.name or "—",
                session.object_name,
                session.inspector,
                str(self.repo.count_devices(session.id)),
                SESSION_STATUS_NAMES.get(session.status, session.status),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, session.id)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(1, max(self.table.columnWidth(1), 200))
        self.table.setColumnWidth(2, max(self.table.columnWidth(2), 280))

    def _selected_session(self) -> Session | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.sessions):
            return None
        return self.sessions[row]

    def _update_buttons(self) -> None:
        has_selection = self._selected_session() is not None
        self.export_btn.setEnabled(has_selection and self.paths is not None)
        self.continue_btn.setEnabled(has_selection)
        self.open_folder_btn.setEnabled(self.paths is not None)

    def _continue_selected(self) -> None:
        session = self._selected_session()
        if not session:
            QMessageBox.information(self, "Продолжить", "Выберите сессию в списке")
            return
        if session.status == "active":
            self.session_reopened.emit(session)
            self.accept()
            return
        message = (
            f"Проект «{session.name}» ({session.object_name}) — "
            f"{SESSION_STATUS_NAMES.get(session.status, session.status).lower()}.\n"
            "Открыть его снова, чтобы добавлять устройства?"
        )
        active = self.repo.get_active_session()
        if active:
            message += f"\n\nТекущая активная сессия «{active.object_name}» будет завершена."
        reply = QMessageBox.question(self, "Продолжить сессию", message)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            if active:
                self.repo.finish_session(active.id)
            reopened = self.repo.reopen_session(session.id)
        except ValueError as exc:
            QMessageBox.warning(self, "Продолжить", str(exc))
            return
        self.status_label.setText(
            f"Сессия открыта: {reopened.object_name} (проект «{reopened.name}»)"
        )
        self.session_reopened.emit(reopened)
        self.accept()

    def _export(self) -> None:
        session = self._selected_session()
        if not session or not self.paths:
            QMessageBox.information(self, "Экспорт", "Выберите сессию в списке")
            return
        try:
            output = suggested_export_path(
                self.paths["exports"], session.name, session.object_name
            )
            self.exported_path = export_session(self.repo, session.id, output)
            backup_database(self.paths["db"], self.paths["backups"])
            prune_backups(self.paths["backups"], keep=30)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Экспорт", f"Не удалось сформировать отчёт:\n{exc}")
            return
        self.status_label.setText(f"Отчёт сохранён: {self.exported_path}")
        self.status_label.setStyleSheet("color: #2ecc71;")
        self.open_folder_btn.setEnabled(True)

    def _open_folder(self) -> None:
        if not self.paths:
            return
        folder = self.exported_path.parent if self.exported_path else self.paths["exports"]
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


class StartScreen(QWidget):
    session_selected = pyqtSignal(object)
    settings_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(
        self,
        repo: InventoryRepository,
        paths: dict[str, Path] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.paths = paths
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 28, 40, 28)
        root.setSpacing(16)

        title = QLabel("Инвентаризация объекта")
        title.setStyleSheet("font-size: 34px; font-weight: bold; color: #ecf0f1;")
        root.addWidget(title)
        subtitle = QLabel("Getac V110 · камеры, коммутаторы, регистраторы и другое оборудование")
        subtitle.setStyleSheet("font-size: 16px; color: #9fb3c8;")
        root.addWidget(subtitle)

        self.active_box = QGroupBox("Текущая сессия")
        active_layout = QVBoxLayout(self.active_box)
        self.active_info = QLabel("Активной сессии нет")
        self.active_info.setWordWrap(True)
        self.active_info.setStyleSheet("font-size: 17px;")
        active_layout.addWidget(self.active_info)
        active_buttons = QHBoxLayout()
        self.continue_btn = big_button("▶ Продолжить обход", "#2e7d32", height=64, font_size=20)
        self.continue_btn.clicked.connect(self._continue_active)
        self.continue_btn.setVisible(False)
        active_buttons.addWidget(self.continue_btn, 2)
        self.history_btn = secondary_button("🕘 История сессий", height=56, font_size=16)
        self.history_btn.clicked.connect(self._show_history)
        active_buttons.addWidget(self.history_btn, 1)
        self.import_btn = secondary_button("⤵ Импорт из базы…", height=56, font_size=15)
        self.import_btn.setToolTip(
            "Перенести сессию с второго ноутбука (данные + папка photos) в текущую"
        )
        self.import_btn.clicked.connect(self._import_session)
        self.import_btn.setVisible(False)
        active_buttons.addWidget(self.import_btn, 1)
        active_layout.addLayout(active_buttons)
        root.addWidget(self.active_box)

        self.new_box = QGroupBox("Новый обход")
        form = QFormLayout(self.new_box)
        self.object_edit = QLineEdit()
        self.object_edit.setPlaceholderText("Например: Бизнес-центр «Север», корпус А")
        self.inspector_edit = QLineEdit()
        self.inspector_edit.setPlaceholderText("ФИО инспектора")
        self.contractor_combo = QComboBox()
        self.contractor_combo.setEditable(True)
        self.contractor_combo.setToolTip(
            "Подрядчик (организация). Список запоминается автоматически — "
            "выберите или впишите нового. В отчёте Excel попадает в титульный лист."
        )
        self._fill_contractor_combo()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Необязательно: название проекта")
        self.comment_edit = QTextEdit()
        self.comment_edit.setPlaceholderText("Необязательно: комментарий к сессии")
        self.comment_edit.setMaximumHeight(90)
        form.addRow("Объект *", self.object_edit)
        form.addRow("Инспектор *", self.inspector_edit)
        form.addRow("Подрядчик", self.contractor_combo)
        form.addRow("Название", self.name_edit)
        form.addRow("Комментарий", self.comment_edit)

        new_buttons = QHBoxLayout()
        self.create_btn = big_button("＋ Создать сессию", "#2980b9", height=64, font_size=20)
        self.create_btn.clicked.connect(self._create_session)
        new_buttons.addWidget(self.create_btn, 2)
        self.settings_btn = secondary_button("⚙ Настройки", height=56, font_size=16)
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        new_buttons.addWidget(self.settings_btn, 1)
        form.addRow("", new_buttons)
        root.addWidget(self.new_box, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.quit_btn = secondary_button("Выход", height=44, font_size=14)
        self.quit_btn.clicked.connect(self.quit_requested.emit)
        bottom.addWidget(self.quit_btn)
        root.addLayout(bottom)

    def _fill_contractor_combo(self) -> None:
        current = self.contractor_combo.currentText()
        self.contractor_combo.clear()
        self.contractor_combo.addItem("")  # пустая строка = без подрядчика
        for name in self.repo.list_contractors():
            self.contractor_combo.addItem(name)
        index = self.contractor_combo.findText(current)
        self.contractor_combo.setCurrentIndex(index if index >= 0 else 0)

    def refresh(self) -> None:
        self._fill_contractor_combo()
        active = self.repo.get_active_session()
        if active:
            count = self.repo.count_devices(active.id)
            self.active_info.setText(
                f"<b>{active.object_name}</b><br>"
                f"Инспектор: {active.inspector}<br>"
                f"Начата: {format_datetime(active.started_at)} · устройств: {count}"
            )
            self.continue_btn.setVisible(True)
            self.import_btn.setVisible(True)
            self.create_btn.setEnabled(False)
            self.create_btn.setToolTip("Сначала завершите активную сессию")
        else:
            self.active_info.setText("Активной сессии нет")
            self.continue_btn.setVisible(False)
            self.import_btn.setVisible(False)
            self.create_btn.setEnabled(True)
            self.create_btn.setToolTip("")
        self.object_edit.setFocus()

    def _continue_active(self) -> None:
        active = self.repo.get_active_session()
        if active:
            self.session_selected.emit(active)

    def _create_session(self) -> None:
        contractor = self.contractor_combo.currentText().strip() or None
        try:
            session = self.repo.create_session(
                object_name=self.object_edit.text(),
                inspector=self.inspector_edit.text(),
                name=self.name_edit.text().strip() or None,
                comment=self.comment_edit.toPlainText().strip() or None,
                contractor=contractor,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Новая сессия", str(exc))
            return
        if contractor:
            self.repo.remember_contractor(contractor)
        self.session_selected.emit(session)

    def _show_history(self) -> None:
        dialog = SessionHistoryDialog(self.repo, self.paths, self)
        dialog.session_reopened.connect(self.session_selected.emit)
        dialog.exec()

    def _import_session(self) -> None:
        active = self.repo.get_active_session()
        if not active:
            return
        dialog = ImportSessionDialog(self.repo, self.paths, active, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

# inventory/ui/finish_dialog.py
"""Завершение сессии: сводка, экспорт, акт PDF, чек-лист докрутки, закрытие."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from inventory.core.baseline import parse_baseline, session_baseline_rows
from inventory.core.export_excel import export_session, suggested_export_path
from inventory.core.models import Session
from inventory.core.repository import InventoryRepository
from inventory.services.backup import backup_database, prune_backups
from inventory.services.report_pdf import generate_act_pdf, generate_followup_pdf
from inventory.ui.widgets import big_button, format_datetime, secondary_button


class FinishDialog(QDialog):
    def __init__(
        self,
        repo: InventoryRepository,
        session: Session,
        paths: dict[str, Path],
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.session = session
        self.paths = paths
        self.exported_path: Path | None = None
        self._summary = repo.get_summary(session.id)
        self.setWindowTitle("Завершение обхода")
        self.setMinimumSize(980, 680)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        title = QLabel("Завершение обхода")
        title.setStyleSheet("font-size: 26px; font-weight: bold;")
        layout.addWidget(title)
        subtitle = QLabel(f"{self.session.object_name} · инспектор: {self.session.inspector}")
        subtitle.setStyleSheet("color: #9fb3c8; font-size: 15px;")
        layout.addWidget(subtitle)

        total = self._summary["total"]
        cards = QHBoxLayout()
        cards.addWidget(self._metric("Устройств", str(total), "#2980b9"))
        cards.addWidget(self._metric("Без S/N и MAC", str(self._summary["no_serial_mac"]), "#e67e22"))
        cards.addWidget(
            self._metric("Камеры/AP без связи", str(self._summary["no_connection"]), "#e67e22")
        )
        cards.addWidget(self._metric("Групп дублей", str(self._summary["duplicate_groups"]), "#e74c3c"))
        cards.addWidget(self._metric("Не проверено", str(self._summary["unverified"]), "#95a5a6"))
        layout.addLayout(cards)

        tables = QHBoxLayout()
        self.type_table = self._make_table(["Тип", "Количество"])
        for item in self._summary["by_type"]:
            self._append_row(self.type_table, [item["name"], str(item["count"])])
        tables.addWidget(self.type_table, 1)

        self.status_table = self._make_table(["Статус", "Количество"])
        for item in self._summary["by_status"]:
            self._append_row(self.status_table, [item["name"], str(item["count"])])
        tables.addWidget(self.status_table, 1)
        layout.addLayout(tables, 1)

        # Сверка с прошлой инвентаризацией (baseline).
        match_row = QHBoxLayout()
        self.baseline_btn = secondary_button(
            "🗂 Сверить с прошлой таблицей…", height=52, font_size=14
        )
        self.baseline_btn.setToolTip(
            "Выбрать Excel прошлой инвентаризации — появится лист «Сверка»:\n"
            "что совпало, изменилось, новое и что пропало"
        )
        self.baseline_btn.clicked.connect(self._import_baseline)
        match_row.addWidget(self.baseline_btn)
        self.archive_btn = secondary_button(
            "🗄 Из архива сессий…", height=52, font_size=13
        )
        self.archive_btn.setToolTip(
            "Сверить с завершённой сессией из этой же базы —\n"
            "не нужен файл Excel, например при регулярном обходе своего объекта"
        )
        self.archive_btn.clicked.connect(self._import_baseline_from_archive)
        match_row.addWidget(self.archive_btn)
        self.baseline_status = QLabel("Прошлая таблица: не выбрана")
        self.baseline_status.setWordWrap(True)
        self.baseline_status.setStyleSheet("color: #9fb3c8; font-size: 14px;")
        match_row.addWidget(self.baseline_status, 1)
        layout.addLayout(match_row)
        if self.session.baseline_path:
            baseline_count = len(self.repo.list_baseline(self.session.id))
            self.baseline_status.setText(
                f"Прошлая таблица: {self.session.baseline_path} ({baseline_count} строк)"
            )

        self.export_status = QLabel("Отчёт ещё не сформирован")
        self.export_status.setWordWrap(True)
        self.export_status.setStyleSheet("color: #f1c40f; font-size: 15px;")
        layout.addWidget(self.export_status)

        buttons = QHBoxLayout()
        self.continue_btn = secondary_button("← Продолжить обход", height=56, font_size=16)
        self.continue_btn.clicked.connect(self.reject)
        buttons.addWidget(self.continue_btn)

        self.open_folder_btn = secondary_button("📂 Открыть папку", height=56, font_size=16)
        self.open_folder_btn.clicked.connect(self._open_export_folder)
        self.open_folder_btn.setEnabled(False)
        buttons.addWidget(self.open_folder_btn)

        self.act_btn = secondary_button("📄 Акт PDF", height=56, font_size=15)
        self.act_btn.setToolTip(
            "Печатный документ для заказчика: шапка объекта, таблица "
            "оборудования, строка подписей."
        )
        self.act_btn.clicked.connect(self._export_act)
        buttons.addWidget(self.act_btn)

        self.checklist_btn = secondary_button("✅ Чек-лист докрутки", height=56, font_size=15)
        self.checklist_btn.setToolTip(
            "План на следующий выезд: устройства, где не хватает данных "
            "(S/N, MAC, фото, подтверждение), с клеткой для отметки."
        )
        self.checklist_btn.clicked.connect(self._export_checklist)
        buttons.addWidget(self.checklist_btn)
        buttons.addStretch(1)

        self.export_btn = big_button("📊 Экспорт Excel", "#8e44ad", height=56, font_size=17)
        self.export_btn.clicked.connect(self._export)
        buttons.addWidget(self.export_btn, 1)

        self.finish_btn = big_button("✓ Завершить сессию", "#2e7d32", height=56, font_size=17)
        self.finish_btn.clicked.connect(self._finish)
        buttons.addWidget(self.finish_btn, 1)
        layout.addLayout(buttons)

    @staticmethod
    def _metric(title: str, value: str, color: str) -> QLabel:
        label = QLabel(f"<div style='font-size: 13px; color: #9fb3c8;'>{title}</div>"
                       f"<div style='font-size: 30px; font-weight: bold; color: {color};'>{value}</div>")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(
            "background-color: #232b38; border: 1px solid #3f4b5e; "
            "border-radius: 12px; padding: 10px;"
        )
        label.setMinimumHeight(90)
        return label

    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    @staticmethod
    def _append_row(table: QTableWidget, values: list[str]) -> None:
        row = table.rowCount()
        table.insertRow(row)
        for column, value in enumerate(values):
            table.setItem(row, column, QTableWidgetItem(value))

    def _import_baseline(self) -> None:
        start_dir = self.session.baseline_path or str(self.paths["exports"])
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Прошлая таблица инвентаризации",
            start_dir,
            "Excel (*.xlsx *.xlsm)",
        )
        if not path:
            return
        try:
            rows = parse_baseline(path)
            if not rows:
                QMessageBox.warning(
                    self,
                    "Сверка",
                    "В таблице не найдено ни одной строки с устройствами.\n"
                    "Нужна шапка (S/N, MAC, модель...) и строки данных.",
                )
                return
            self._apply_baseline_rows(rows, path, Path(path).name)
        except FileNotFoundError as exc:
            QMessageBox.warning(self, "Сверка", str(exc))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(
                self, "Сверка", f"Не удалось прочитать прошлую таблицу:\n{exc}"
            )

    def _import_baseline_from_archive(self) -> None:
        """Сверка по завершённой сессии этой же базы — без Excel-файла."""
        sessions = [
            s for s in self.repo.list_sessions(limit=100) if s.id != self.session.id
        ]
        if not sessions:
            QMessageBox.information(
                self,
                "Сверка из архива",
                "В этой базе нет другой сессии для сравнения.\n"
                "Сверка появится, когда есть прошлый обход (Excel или сессия).",
            )
            return
        items = [
            f"{s.object_name} · {s.name} · {s.inspector} · "
            + (
                f"завершена {format_datetime(s.finished_at)}"
                if s.finished_at
                else f"начата {format_datetime(s.started_at)}"
            )
            for s in sessions
        ]
        choice, ok = QInputDialog.getItem(
            self,
            "Сверка из архива",
            "Выберите прошлую сессию для сравнения:",
            items,
            0,
            False,
        )
        if not ok:
            return
        selected = sessions[items.index(choice)]
        rows = session_baseline_rows(self.repo, selected.id)
        usable = sum(
            1 for row in rows if row.get("serial_norm") or row.get("mac_norm")
        )
        if not rows or not usable:
            QMessageBox.warning(
                self,
                "Сверка из архива",
                "В выбранной сессии нет устройств с S/N или MAC —\n"
                "сверить по идентификаторам не получится.",
            )
            return
        label = f"session:{selected.id}"
        name = f"{selected.object_name} (архив, {usable} устр. с S/N или MAC)"
        self._apply_baseline_rows(rows, label, name)

    def _apply_baseline_rows(
        self, rows: list[dict], source_label: str, source_name: str
    ) -> None:
        """Общий для Excel и архива шаг: положить строки в базу и показать статус."""
        try:
            result = self.repo.import_baseline(self.session.id, source_label, rows)
            self.session.baseline_path = source_label
            usable = result["usable"]
            self.baseline_status.setText(
                f"Прошлая таблица: {source_name} ({result['total']} строк, "
                f"с S/N или MAC: {usable})"
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(
                self, "Сверка", f"Не удалось загрузить прошлые данные:\n{exc}"
            )

    def _export(self) -> None:
        try:
            output = suggested_export_path(
                self.paths["exports"], self.session.name, self.session.object_name
            )
            self.exported_path = export_session(self.repo, self.session.id, output)
            backup_database(self.paths["db"], self.paths["backups"])
            prune_backups(self.paths["backups"], keep=30)
            self.export_status.setText(f"Отчёт сохранён: {self.exported_path}")
            self.export_status.setStyleSheet("color: #2ecc71; font-size: 15px;")
            self.open_folder_btn.setEnabled(True)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Экспорт", f"Не удалось сформировать отчёт:\n{exc}")

    def _pdf_export_path(self, prefix: str) -> Path:
        safe = re.sub(
            r"[^\w\-]+", "_",
            f"{self.session.object_name}_{self.session.name}",
            flags=re.UNICODE,
        ).strip("_")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return Path(self.paths["exports"]) / f"{prefix}_{safe or 'session'}_{stamp}.pdf"

    def _export_act(self) -> None:
        try:
            output = self._pdf_export_path("act")
            devices = self.repo.list_devices(self.session.id)
            path = generate_act_pdf(
                self.session, devices, output, repo=self.repo)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(
                self, "Акт PDF", f"Не удалось сформировать акт:\n{exc}")
            return
        self.export_status.setText(f"Акт сохранён: {path}")
        self.export_status.setStyleSheet("color: #2ecc71; font-size: 15px;")
        self.open_folder_btn.setEnabled(True)

    def _export_checklist(self) -> None:
        try:
            output = self._pdf_export_path("dokrutka")
            devices = self.repo.list_devices(self.session.id)
            path = generate_followup_pdf(self.session, devices, output)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(
                self, "Чек-лист докрутки",
                f"Не удалось сформировать чек-лист:\n{exc}")
            return
        items = sum(
            1 for device in devices
            if (not (device.serial or "").strip()
                or not (device.mac or "").strip()
                or device.device_type in ("camera", "ap")
                and not device.upstream_device_id
                or not device.is_verified)
        )
        self.export_status.setText(
            f"Чек-лист сохранён: {path} (позиций: {items})")
        self.export_status.setStyleSheet("color: #2ecc71; font-size: 15px;")
        self.open_folder_btn.setEnabled(True)

    def _open_export_folder(self) -> None:
        if self.exported_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.exported_path.parent)))

    def _finish(self) -> None:
        if not self.exported_path:
            reply = QMessageBox.question(
                self,
                "Завершение",
                "Отчёт ещё не сформирован. Завершить сессию без экспорта?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        else:
            reply = QMessageBox.question(
                self,
                "Завершение",
                "Завершить сессию? После этого добавление устройств будет недоступно.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        try:
            self.repo.finish_session(self.session.id)
            backup_database(self.paths["db"], self.paths["backups"])
            prune_backups(self.paths["backups"], keep=30)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Завершение", f"Не удалось завершить сессию:\n{exc}")
            return
        self.accept()

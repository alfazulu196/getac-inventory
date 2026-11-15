# inventory/ui/net_audit_dialog.py
"""Ревизия сети сессии (v1.6.9): все IP-адреса пачкой, перед выездом.

Кнопка «🌐 Ревизия сети» в списке устройств запускает батч-проверку
ping + сверка MAC по ARP для каждого устройства сессии — одним взглядом
видно, что записано, но не отвечает. Ничего не меняет в базе, только
диагностика. Двойной клик по строке открывает карточку устройства.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from inventory.services.voice import VoiceService
from inventory.ui.device_card import DeviceCardDialog
from inventory.ui.net_workers import NetAuditWorker
from inventory.ui.photo_intel_workers import detach_running
from inventory.ui.widgets import big_button, ensure_field_restyle

_STATUS_TEXT = {
    "alive": "🟢 в сети",
    "mac_mismatch": "🟠 MAC не совпал",
    "unreachable": "🔴 не отвечает",
    "no_ip": "⚪ нет IP",
    "bad_ip": "⚪ кривой IP",
}

_STATUS_COLOR = {
    "alive": "#2ecc71",
    "mac_mismatch": "#e67e22",
    "unreachable": "#e74c3c",
}


class NetAuditDialog(QDialog):
    def __init__(
        self,
        items: list[tuple[str, str, str, str, str]],
        repo=None,
        session_id: str | None = None,
        paths: dict[str, Path] | None = None,
        voice: VoiceService | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.session_id = session_id
        self.paths = paths or {}
        self.voice = voice
        self._worker: NetAuditWorker | None = None
        self._results: list[dict] = []
        self.setWindowTitle("🌐 Ревизия сети")
        self.setMinimumSize(1000, 600)
        self._build_ui(len(items))
        ensure_field_restyle()
        self._worker = NetAuditWorker(items, self)
        self._worker.checked.connect(self._on_checked)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _build_ui(self, total: int) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Ревизия сети сессии")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        self.status_label = QLabel(f"⏳ Проверяю 0 из {total}…")
        self.status_label.setStyleSheet("color: #9fb3c8;")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Модель", "IP", "Сеть", "Причина"]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 110)
        self.table.setColumnWidth(1, 220)
        self.table.setColumnWidth(2, 140)
        self.table.setColumnWidth(3, 170)
        self.table.doubleClicked.connect(self._open_selected)
        layout.addWidget(self.table, 1)

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.summary_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_btn = big_button("Закрыть", "#3b4a5f", height=52, font_size=16)
        self.close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.close_btn, 1)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------
    def _on_progress(self, done: int, total: int, label: str) -> None:
        self.status_label.setText(f"⏳ Проверяю {done} из {total}: {label}")

    def _on_checked(self, info: dict) -> None:
        self._results.append(info)
        result = info["result"]
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = [
            info["display_id"],
            info["model"],
            info["ip"] or "—",
            _STATUS_TEXT.get(result.status, result.status),
            result.detail,
        ]
        color = QColor(_STATUS_COLOR.get(result.status, "#9fb3c8"))
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setForeground(color)
            if column == 0:
                item.setData(Qt.ItemDataRole.UserRole, info["device_id"])
            if column == 4:  # длинную причину читать полностью из tooltip
                item.setToolTip(result.detail)
            self.table.setItem(row, column, item)

    def _on_finished(self) -> None:
        self._worker = None
        total = len(self._results)
        counts: dict[str, int] = {}
        for info in self._results:
            counts[info["result"].status] = counts.get(
                info["result"].status, 0) + 1
        parts = [
            f"Проверено: {total}",
            f"🟢 в сети: {counts.get('alive', 0) + counts.get('mac_mismatch', 0)}",
        ]
        if counts.get("mac_mismatch"):
            parts.append(f"🟠 MAC не совпал: {counts['mac_mismatch']}")
        if counts.get("unreachable"):
            parts.append(f"🔴 не отвечают: {counts['unreachable']}")
        missed = counts.get("no_ip", 0) + counts.get("bad_ip", 0)
        if missed:
            parts.append(f"⚪ без IP/кривой: {missed}")
        self.summary_label.setText(" · ".join(parts))
        self.status_label.setText("✅ Ревизия завершена")

    def _open_selected(self) -> None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        if item is None or self.repo is None:
            return
        device = self.repo.get_device(item.data(Qt.ItemDataRole.UserRole))
        if device is None:
            return
        dialog = DeviceCardDialog(
            self.repo, self.session_id, self.paths, self.voice,
            device=device, parent=self,
        )
        dialog.exec()

    def reject(self) -> None:
        detach_running(self._worker)
        self._worker = None
        super().reject()

    def closeEvent(self, event) -> None:
        detach_running(self._worker)
        self._worker = None
        super().closeEvent(event)

# inventory/ui/tags_dialog.py
"""Диалог печати QR-бирок (v1.6.5): выбор устройств, PDF-A4, PNG одной бирки."""
from __future__ import annotations

import math
import os
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QDialog,
)

from inventory.config import device_type_name
from inventory.core.models import Device
from inventory.core.repository import InventoryRepository
from inventory.services.qr_tags import (
    assign_tag_ids,
    generate_tags_pdf,
    render_label_png,
)
from inventory.ui.widgets import big_button, secondary_button


class TagsDialog(QDialog):
    """Устройства сессии → галочки → PDF-A4 (24 этикетки 65×35 мм) или PNG."""

    def __init__(self, repo: InventoryRepository, session_id: str,
                 paths: dict[str, Path], parent=None):
        super().__init__(parent)
        self.repo = repo
        self.session_id = session_id
        self.paths = paths
        self._devices: list[Device] = repo.list_devices(session_id)
        self._pdf_path: Path | None = None
        self._filling = False  # itemChanged при наполнении списка не считаем

        self.setWindowTitle("🏷 QR-бирки оборудования")
        self.setMinimumSize(760, 560)
        self._build_ui()
        self._fill_list()
        self._refresh_stats()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Печать QR-бирок")
        title.setStyleSheet("font-size: 22px; font-weight: bold;")
        layout.addWidget(title)

        self.stats_label = QLabel("")
        self.stats_label.setWordWrap(True)
        self.stats_label.setStyleSheet("font-size: 15px;")
        layout.addWidget(self.stats_label)

        hint = QLabel(
            "QR кодирует «SN=…;MAC=…» — формат, который приложение уже читает "
            "камерой. Повторный выезд: скан бирки → карточка найдена мгновенно. "
            "Устройствам без S/N и MAC присваивается инвентарный номер "
            "«INV-####» (глобальный реестр) и QR кодирует «INV=…». Печать: "
            "лист A4, 24 этикетки 65×35 мм, по линиям реза."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9fb3c8; font-size: 13px;")
        layout.addWidget(hint)

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("font-size: 14px;")
        self.list_widget.itemChanged.connect(lambda _: self._refresh_stats())
        layout.addWidget(self.list_widget, 1)

        buttons = QHBoxLayout()
        self.save_btn = big_button("💾 Сохранить PDF…", "#2980b9",
                                   height=54, font_size=16)
        self.save_btn.clicked.connect(self._save_pdf)
        buttons.addWidget(self.save_btn)

        self.png_btn = secondary_button("🖼 PNG одной бирки", height=54,
                                        font_size=15)
        self.png_btn.clicked.connect(self._save_png)
        buttons.addWidget(self.png_btn)

        self.open_btn = secondary_button("🖨 Открыть для печати", height=54,
                                         font_size=15)
        self.open_btn.clicked.connect(self._open_pdf)
        self.open_btn.setEnabled(False)
        buttons.addWidget(self.open_btn)
        buttons.addStretch(1)

        self.close_btn = big_button("Закрыть", "#3b4a5f", height=54,
                                    font_size=17)
        self.close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.close_btn, 1)
        layout.addLayout(buttons)

    def _fill_list(self) -> None:
        self._filling = True
        for device in self._devices:
            item = QListWidgetItem(self._describe(device))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, device.id)
            self.list_widget.addItem(item)
        self._filling = False

    def _refresh_texts(self) -> None:
        """После присвоения ИНВ описания строк обновить (появился «ИНВ …»)."""
        self._filling = True
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            device = self._device_by_id(item.data(Qt.ItemDataRole.UserRole))
            if device:
                item.setText(self._describe(device))
        self._filling = False

    @staticmethod
    def _describe(device: Device) -> str:
        brand = (device.manufacturer or "").strip()
        model = (device.model or "").strip()
        name = " ".join(p for p in (brand, model) if p)
        kind = device_type_name(device.device_type)
        ident: list[str] = []
        if (device.serial or "").strip():
            ident.append(f"S/N {device.serial.strip()}")
        if (device.mac or "").strip():
            ident.append(f"MAC {device.mac.strip()}")
        if (device.asset_tag or "").strip():
            ident.append(f"ИНВ {device.asset_tag.strip()}")
        tail = " · ".join(ident) if ident else "без S/N и MAC — получит ИНВ"
        head = f"{device.display_id} · {kind}"
        return f"{head}{' · ' + name if name else ''} · {tail}"

    # --------------------------------------------------------------- данные
    def _device_by_id(self, device_id: str) -> Device | None:
        return next((d for d in self._devices if d.id == device_id), None)

    def _checked_devices(self) -> list[Device]:
        chosen: list[Device] = []
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                device = self._device_by_id(item.data(Qt.ItemDataRole.UserRole))
                if device:
                    chosen.append(device)
        return chosen

    def _refresh_stats(self) -> None:
        if self._filling:
            return
        chosen = self._checked_devices()
        pages = math.ceil(len(chosen) / 24) if chosen else 0
        self.stats_label.setText(
            f"Выбрано бирок: {len(chosen)} из {len(self._devices)}   ·   "
            f"листов A4: {pages}"
        )
        self.png_btn.setEnabled(len(chosen) == 1)

    def _exports_dir(self) -> Path:
        base = Path(self.paths.get("exports", Path("data")))
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _default_pdf_path(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        return self._exports_dir() / f"qr_birki_{stamp}.pdf"

    # --------------------------------------------------------------- кнопки
    def _save_pdf(self) -> None:
        chosen = self._checked_devices()
        if not chosen:
            QMessageBox.information(self, "Бирки",
                                    "Отметьте хотя бы одно устройство")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Куда сохранить PDF с бирками", str(self._default_pdf_path()),
            "PDF (*.pdf)")
        if not path:
            return
        assigned = assign_tag_ids(chosen, self.repo)
        printed, skipped = generate_tags_pdf(chosen, path)
        self._pdf_path = Path(path)
        self.open_btn.setEnabled(True)
        self._refresh_texts()
        self._refresh_stats()
        message = f"Готово: {printed} бирок в {path}"
        if assigned:
            message += f"\nприсвоено инвентарных номеров: {assigned}"
        if skipped:
            message += f"\nпропущено без идентификаторов: {skipped}"
        QMessageBox.information(self, "Бирки", message)

    def _save_png(self) -> None:
        chosen = self._checked_devices()
        if len(chosen) != 1:
            QMessageBox.information(
                self, "Бирка PNG",
                "Отметьте ровно одно устройство — PNG печатает одну бирку\n"
                "(перепечатка утерянной или термо-принтер).")
            return
        device = chosen[0]
        assign_tag_ids([device], self.repo)
        path = self._exports_dir() / f"birka_{device.display_id}.png"
        render_label_png(device, path)
        self._refresh_texts()
        try:
            os.startfile(str(path))  # noqa: S606 — просмотр PNG
        except OSError as exc:
            QMessageBox.warning(self, "Бирка PNG",
                                f"Не удалось открыть: {exc}")

    def _open_pdf(self) -> None:
        if not self._pdf_path or not self._pdf_path.exists():
            return
        try:
            os.startfile(str(self._pdf_path))  # noqa: S606 — просмотр PDF
        except OSError as exc:
            QMessageBox.warning(self, "Бирки",
                                f"Не удалось открыть: {exc}")

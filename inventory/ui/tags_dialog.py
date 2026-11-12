# inventory/ui/tags_dialog.py
"""Диалог печати QR-бирок (v1.6.4): устройства сессии → PDF-A4 с наклейками."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QHBoxLayout,
    QFileDialog,
    QMessageBox,
)

from inventory.core.repository import InventoryRepository
from inventory.services.qr_tags import generate_tags_pdf, tag_code
from inventory.ui.widgets import big_button, secondary_button


class TagsDialog(QDialog):
    """Список устройств сессии → PDF с QR-бирками (печать в офисе)."""

    def __init__(self, repo: InventoryRepository, session_id: str,
                 paths: dict[str, Path], parent=None):
        super().__init__(parent)
        self.repo = repo
        self.session_id = session_id
        self.paths = paths
        self._pdf_path: Path | None = None

        self.setWindowTitle("🏷 QR-бирки оборудования")
        self.setMinimumSize(720, 420)
        self._build_ui()
        self._refresh_stats()

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
            "Бирка кодирует «SN=…;MAC=…» — тот же формат, что приложение уже\n"
            "читает камерой. Повторный выезд: скан бирки → карточка найдена\n"
            "мгновенно, без фото-разбора этикетки. Печать: офисный принтер,\n"
            "лист A4, 24 этикетки 65×35 мм, по линиям реза.\n\n"
            "Устройства без S/N и MAC не попадают на печать — бирку некуда\n"
            "«пришить» между выездами; они появятся после первой переписи."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9fb3c8; font-size: 13px;")
        layout.addWidget(hint)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        self.save_btn = big_button("💾 Сохранить PDF…", "#2980b9",
                                   height=54, font_size=16)
        self.save_btn.clicked.connect(self._save_pdf)
        buttons.addWidget(self.save_btn)

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

    def _refresh_stats(self) -> None:
        devices = self.repo.list_devices(self.session_id)
        printable = [d for d in devices if tag_code(d)]
        self.stats_label.setText(
            f"Устройств в сессии: {len(devices)}   ·   "
            f"бирок будет напечатано: {len(printable)}   ·   "
            f"пропущено без S/N и MAC: {len(devices) - len(printable)}"
        )

    def _default_path(self) -> Path:
        stamps = datetime.now().strftime("%Y%m%d_%H%M")
        base = Path(self.paths.get("exports", Path("data")))
        base.mkdir(parents=True, exist_ok=True)
        return base / f"qr_birki_{stamps}.pdf"

    def _save_pdf(self) -> None:
        default = str(self._default_path())
        path, _ = QFileDialog.getSaveFileName(
            self, "Куда сохранить PDF с бирками", default,
            "PDF (*.pdf)")
        if not path:
            return
        devices = self.repo.list_devices(self.session_id)
        printed, skipped = generate_tags_pdf(devices, path)
        self._pdf_path = Path(path)
        self.open_btn.setEnabled(True)
        QMessageBox.information(
            self, "Бирки",
            f"Готово: {printed} бирок в {path}\n"
            + (f"пропущено без S/N и MAC: {skipped}" if skipped else ""),
        )

    def _open_pdf(self) -> None:
        if not self._pdf_path or not self._pdf_path.exists():
            return
        try:
            os.startfile(str(self._pdf_path))  # noqa: S606 — просмотр PDF
        except OSError as exc:
            QMessageBox.warning(self, "Бирки",
                                f"Не удалось открыть: {exc}")

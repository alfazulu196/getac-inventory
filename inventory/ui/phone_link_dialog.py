# inventory/ui/phone_link_dialog.py
"""Диалог связи с телефоном: QR-адрес страницы приёма фото и журнал."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QVBoxLayout,
    QWidget,
)

from inventory.services.photo_server import PhotoServer
from inventory.ui.widgets import big_button, secondary_button


def render_qr_pixmap(url: str, cell: int = 7) -> QPixmap | None:
    """QR-код адреса (библиотека qrcode; без неё — None и текстовый адрес)."""
    try:
        import qrcode
    except Exception:  # noqa: BLE001
        return None
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    size = len(matrix) * cell
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor("#ffffff"))
    painter = QPainter(pixmap)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#111820"))
    for y, row in enumerate(matrix):
        for x, filled in enumerate(row):
            if filled:
                painter.drawRect(x * cell, y * cell, cell, cell)
    painter.end()
    return pixmap


def _hint_text() -> str:
    qrcode_missing = False
    try:  # noqa: SIM105 — понятная формулировка без логов
        import qrcode  # noqa: F401
    except Exception:  # noqa: BLE001
        qrcode_missing = True
    addendum = (
        "" if not qrcode_missing
        else "QR недоступен (нет пакета qrcode) — введите адрес в браузере телефона вручную."
    )
    return (
        "1. Телефон и ноутбук — в одной Wi-Fi-сети: включите на ноутбуке"
        " «Мобильный хот-спот» или раздайте сеть с телефона.\n"
        "2. Откройте камерой телефона QR (или введите адрес в браузер).\n"
        "3. Кнопки на странице: «Снять фото» — камера телефона, «Из галереи» —"
        " снимок, сделанный заранее.\n"
        "4. Каждое фото привязывается к активному устройству на главном экране.\n"
        + addendum
    )


class PhoneLinkDialog(QDialog):
    """Статус сервера фото, QR-адрес и журнал принятых фотографий."""

    def __init__(self, server: PhotoServer, parent=None):
        super().__init__(parent)
        self.server = server
        self._shown_received = 0
        self._qr_drawn_url: str | None = None
        self.setWindowTitle("Фотографии с телефона")
        self.setMinimumSize(620, 680)
        self._build_ui()
        self._refresh()
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._refresh)
        self._poll.start(1000)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("font-size: 15px;")
        layout.addWidget(self.status_label)

        url_row = QHBoxLayout()
        self.urls_list = QListWidget()
        self.urls_list.setFixedHeight(64)
        self.urls_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        url_row.addWidget(self.urls_list, 1)
        copy_btn = secondary_button("📋 Копировать адрес", height=48, font_size=13)
        copy_btn.clicked.connect(self._copy_selected)
        url_row.addWidget(copy_btn)
        layout.addLayout(url_row)

        self.qr_label = QLabel("")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setFixedSize(252, 252)
        self.qr_label.setStyleSheet(
            "background-color: #ffffff; border-radius: 10px; color: #111820;"
        )
        layout.addWidget(self.qr_label, 0, Qt.AlignmentFlag.AlignHCenter)

        hint = QLabel(_hint_text())
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9fb3c8; font-size: 12px;")
        layout.addWidget(hint)

        layout.addStretch(1)

        log_box = QGroupBox("Принятые фото")
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(8, 10, 8, 8)
        self.log_list = QListWidget()
        self.log_list.setFixedHeight(112)
        log_layout.addWidget(self.log_list)
        layout.addWidget(log_box)

        buttons = QHBoxLayout()
        self.toggle_btn = big_button("🔓 Прервать приём", "#c0392b", height=48, font_size=14)
        self.toggle_btn.clicked.connect(self._toggle_server)
        buttons.addWidget(self.toggle_btn)
        close_btn = secondary_button("Закрыть", height=48, font_size=14)
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------
    def _toggle_server(self) -> None:
        if self.server._server is not None:
            self.server.stop()
        else:
            self.server.urls = self.server.start()
        self._refresh()

    def _copy_selected(self) -> None:
        item = self.urls_list.currentItem()
        if item is None and self.urls_list.count():
            item = self.urls_list.item(0)
        if item is None:
            return
        QApplication.clipboard().setText(item.text())
        self.status_label.setText("Адрес скопирован — вставьте в браузер или сообщение телефона")
        self.status_label.setStyleSheet("font-size: 15px; color: #2ecc71;")

    def _refresh(self) -> None:
        running = getattr(self.server, "_server", None) is not None and self.server.port
        if running:
            self.status_label.setText(
                f"Сервер приёма фото: работает (порт {self.server.port})"
            )
            self.status_label.setStyleSheet("font-size: 15px; color: #2ecc71;")
            self.toggle_btn.setText("⏹ Остановить приём")
        else:
            self.status_label.setText("Сервер приёма фото: остановлен")
            self.status_label.setStyleSheet("font-size: 15px; color: #e67e22;")
            self.toggle_btn.setText("▶ Запустить приём")

        self.urls_list.clear()
        for url in self.server.urls:
            self.urls_list.addItem(url)
        qr_target = self.server.urls[0] if self.server.urls else None
        if not running:
            qr_target = None
        if qr_target != self._qr_drawn_url:
            self._qr_drawn_url = qr_target
            if qr_target is None:
                self.qr_label.clear()
            else:
                pixmap = render_qr_pixmap(qr_target)
                if pixmap is None:
                    self.qr_label.setText(qr_target)
                else:
                    self.qr_label.setPixmap(
                        pixmap.scaled(
                            240,
                            240,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.FastTransformation,
                        )
                    )

        # Журнал принятых файлов.
        received = self.server.recent_received()
        while self._shown_received < len(received):
            record = received[self._shown_received]
            who = record.device_name or "без устройства"
            self.log_list.insertItem(
                0, f"{record.saved_at} · {who} · {Path(record.path).name}"
            )
            self._shown_received += 1

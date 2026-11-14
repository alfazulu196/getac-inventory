# inventory/services/qr_tags.py
"""QR-бирки оборудования (v1.6.4+): печать PDF-A4 с наклейками.

Идея: QR кодирует ТОТ ЖЕ структурированный формат, что приложение уже
парсит камерой — «SN=<serial>;MAC=<mac>». Повторный выезд: скан бирки
→ знакомый конвейер process_code → карточка найдена/создана мгновенно,
без фото-разбора этикетки и без ИИ. Кросс-визитная идентификация — по
S/N+MAC, тот же ключ, что у дубль-детекта.

v1.6.5: бирки и для безымянного оборудования — устройству без S/N и MAC
присваивается инвентарный номер «INV-####» (глобальный реестр, поле
«Инвентарный номер» карточки), QR кодирует «INV=INV-####»; скан такой
бирки находит существующую карточку (дубли ищутся и по ИНН — duplicates).

Бирки печатать в офисе после инвентаризации (поле не трогает).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import (
    QGuiApplication,
    QImage,
    QPainter,
    QPen,
    QFont,
    QColor,
    QPdfWriter,
)

from inventory.config import device_type_name
from inventory.core.models import Device

# Формат листа: A4, 3 колонки × 8 рядов этикеток 65×35 мм (24 на лист).
_COLS, _ROWS = 3, 8
_LABEL_W, _LABEL_H = 65.0, 35.0          # мм
_PAGE_W, _PAGE_H = 210.0, 297.0          # мм
_MARGIN_X = (_PAGE_W - _COLS * _LABEL_W) / 2   # 7.5 мм
_MARGIN_Y = (_PAGE_H - _ROWS * _LABEL_H) / 2   # 8.5 мм


def tag_code(device: Device) -> str | None:
    """Содержимое бирки: 'SN=…;MAC=…' или 'INV=…', None — нечего кодировать."""
    parts: list[str] = []
    if (device.serial or "").strip():
        parts.append(f"SN={device.serial.strip()}")
    if (device.mac or "").strip():
        parts.append(f"MAC={device.mac.strip()}")
    if not parts and (device.asset_tag or "").strip():
        # Безымянное устройство: идентифицируем присвоенным ИНВ (реестр v1.6.5).
        parts.append(f"INV={device.asset_tag.strip()}")
    return ";".join(parts) if parts else None


def assign_tag_ids(devices: list[Device], repo: Any) -> int:
    """Безымянным (без S/N и MAC) присваивает ИНВ «INV-####» и сохраняет в БД.

    Реестр глобальный: repo.next_asset_tag_number() выдаёт следующий номер
    среди ВСЕХ сессий — номер с напечатанной бирки не выдаётся дважды.
    Возвращает число присвоенных номеров.
    """
    need = [
        device for device in devices
        if not (device.serial or "").strip()
        and not (device.mac or "").strip()
        and not (device.asset_tag or "").strip()
    ]
    if not need:
        return 0
    number = repo.next_asset_tag_number()
    for device in need:
        tag = f"INV-{number:04d}"
        repo.update_device(device.id, asset_tag=tag)
        device.asset_tag = tag
        number += 1
    return len(need)


def qr_image(text: str, size_px: int = 320) -> QImage:
    """Текст → чёрно-белый QR (cv2.QRCodeEncoder — без новых зависимостей)."""
    encoder = cv2.QRCodeEncoder.create()
    matrix = encoder.encode(text)
    img = np.asarray(matrix, dtype=np.uint8)
    if img.max() == 0:  # весь чёрный — на всякий случай инвертируем фон
        img = 255 - img
    height, width = img.shape
    buf = img.tobytes()  # QImage ссылается на буфер — .copy() отцепляет
    qimg = QImage(buf, width, height, width, QImage.Format.Format_Grayscale8)
    return qimg.copy().scaled(
        size_px, size_px, Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.FastTransformation)


_FONT_FAMILY = "Segoe UI"  # есть на любой Windows, полная кириллица


def detect_qr_text(image_path: str | Path) -> str | None:
    """QR-код на фото бирки → текст; None — не найден или файл не читается.

    np.fromfile + cv2.imdecode: cv2.imread не открывает пути с кириллицей.
    """
    try:
        data = np.fromfile(str(image_path), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return None
        detector = cv2.QRCodeDetector()
        text, _, _ = detector.detectAndDecode(img)
        if not text:
            # Мелкий/далёкий кадр: увеличение ×2 часто дожимает распознавание.
            big = cv2.resize(img, None, fx=2, fy=2,
                             interpolation=cv2.INTER_CUBIC)
            text, _, _ = detector.detectAndDecode(big)
        return text.strip() or None
    except (OSError, ValueError, cv2.error):
        return None


def _font(point_size: float, *, bold: bool = False) -> QFont:
    """Явное семейство: без него offscreen-рендер рисует кириллицу «тофу»."""
    font = QFont(_FONT_FAMILY)
    font.setPointSizeF(point_size)
    if bold:
        font.setBold(True)
    return font


def _draw_label(painter: QPainter, device: Device, x: float, y: float,
                mm: float) -> None:
    """Одна этикетка: QR слева (24 мм), текст справа, рамка для реза."""
    w, h = _LABEL_W * mm, _LABEL_H * mm
    pen = QPen(QColor(180, 190, 200))
    pen.setWidthF(0.15 * mm)
    painter.setPen(pen)
    painter.drawRoundedRect(QRectF(x, y, w, h), 2.0 * mm, 2.0 * mm)

    # QR слева. Важно: перегрузка drawImage(x, y, img, a, b) трактует
    # a, b как ОБЛАСТЬ ИСТОЧНИКА, а не размеры — масштабирует только
    # перегрузка с целевым QRectF.
    qr_side = 24 * mm
    qr = qr_image(tag_code(device) or "", size_px=int(qr_side))
    painter.drawImage(
        QRectF(x + 2.5 * mm, y + (h - qr_side) / 2, qr_side, qr_side), qr)

    # Текст справа: каждая строка — в своём прямоугольнике (без «тофу»).
    tx = x + 28 * mm
    tw = w - 30 * mm
    brand = (device.manufacturer or "").strip()
    model = (device.model or "").strip()
    name = " ".join(p for p in (brand, model) if p) \
        or device_type_name(device.device_type)
    sn = (device.serial or "").strip()
    mac = (device.mac or "").strip()
    asset = (device.asset_tag or "").strip()
    loc = device.location.short_label() if device.location else ""
    dark = QColor(20, 26, 34)
    mid = QColor(60, 70, 84)
    grey = QColor(120, 130, 144)
    rows = [
        (_font(9.5, bold=True), dark, device.display_id or "", 5.8),
        (_font(7.5), dark, name, 5.4),
        (_font(7.0), mid, f"S/N {sn}" if sn else "", 5.2),
        (_font(7.0), mid, f"MAC {mac}" if mac else "", 5.2),
        # ИНВ показываем, когда он ЕДИНСТВЕННЫЙ идентификатор (безымянное
        # устройство): строка не влезла бы вместе с S/N и MAC.
        (_font(7.0, bold=True), dark,
         f"ИНВ {asset}" if asset and not sn and not mac else "", 5.2),
        (_font(6.5), grey, loc, 5.2),
    ]
    ty = y + 4.4 * mm
    for font, color, text, row_h in rows:
        if not text:
            continue
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(
            QRectF(tx, ty, tw, row_h * mm),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            text)
        ty += row_h * mm


def render_label_png(device: Device, out_path: str | Path, scale: int = 12) -> Path:
    """Одна этикетка в PNG (~300 dpi): перепечатка утерянной, термо-принтер."""
    if QGuiApplication.instance() is None:
        _app = QGuiApplication([])  # QApplication обязателен до QPainter
    img = QImage(int(_LABEL_W * scale) + 20, int(_LABEL_H * scale) + 20,
                 QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    painter = QPainter(img)
    try:
        _draw_label(painter, device, 10, 10, scale)
    finally:
        painter.end()
    img.save(str(out_path))
    return Path(out_path)


def generate_tags_pdf(devices: list[Device], out_path: str | Path,
                      ) -> tuple[int, int]:
    """Печать бирок в PDF → (напечатано, пропущено без S/N и MAC).

    Лист A4, 24 этикетки 65×35 мм; заполнение сверху вниз, колонками.
    """
    printable = [d for d in devices if tag_code(d)]
    skipped = len(devices) - len(printable)
    if not printable:
        return 0, skipped

    if QGuiApplication.instance() is None:
        _app = QGuiApplication([])  # QApplication обязателен до QPdfWriter
    writer = QPdfWriter(str(out_path))
    writer.setResolution(300)  # dpi: 1 мм ≈ 11.81 ед.
    mm = writer.resolution() / 25.4

    painter = QPainter(writer)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    per_page = _COLS * _ROWS
    for index, device in enumerate(printable):
        slot = index % per_page
        if index and slot == 0:
            writer.newPage()  # Qt6: у QPdfWriter новый лист через newPage()
        col, row = slot % _COLS, slot // _COLS
        x = (_MARGIN_X + col * _LABEL_W) * mm
        y = (_MARGIN_Y + row * _LABEL_H) * mm
        _draw_label(painter, device, x, y, mm)
    painter.end()
    return len(printable), skipped

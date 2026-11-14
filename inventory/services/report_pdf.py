# inventory/services/report_pdf.py
"""Печатные документы для заказчика: акт инвентаризации и чек-лист докрутки.

Оба печатаются через QPdfWriter — он уже в бандле (ради QR-бирок v1.6.4),
ноль новых зависимостей. Qt-грабли учтены: явный QFont("Segoe UI") без
«тофу»-кириллицы, newPage() у QPdfWriter (Qt6), drawRoundedRect/drawRect
— только с QRectF.

Акт — документ «для подписи»: шапка объекта, сводка, таблица оборудования,
строка подписей. Чек-лист — план докрутки на следующий выезд: устройства,
у которых не хватает данных (S/N, MAC, фото, подтверждение), с клеткой
для отметки и колонкой «что добрать».
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QPainter,
    QPen,
    QPdfWriter,
)

from inventory.config import device_type_name, status_name
from inventory.core.models import Device

_PAGE_W_MM, _PAGE_H_MM = 210.0, 297.0  # A4
_MARGIN_MM = 12.0
_CONTENT_W_MM = _PAGE_W_MM - 2 * _MARGIN_MM
_FONT_FAMILY = "Segoe UI"  # есть на любой Windows, полная кириллица

_DARK = QColor(20, 26, 34)
_MID = QColor(60, 70, 84)
_GREY = QColor(120, 130, 144)
_HEAD_BG = QColor(226, 232, 240)
_LINE = QColor(160, 172, 186)


def _font(size: float, *, bold: bool = False) -> QFont:
    font = QFont(_FONT_FAMILY)
    font.setPointSizeF(size)
    if bold:
        font.setBold(True)
    return font


def _fmt_dt(value: Any) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(str(value)).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return str(value)


_APP: QGuiApplication | None = None  # держим живым: GC убьёт painter (AV)


def _device_sort_key(device: Device) -> tuple[int, str, int, str]:
    """SW-001 < SW-002 < UPS-001: префикс, потом номер (repo отдаёт свежие первыми)."""
    raw = (device.display_id or "").strip()
    match = re.match(r"^([^\d]*?)[-\s]?(\d+)$", raw)
    if match:
        return (0, match.group(1).upper(), int(match.group(2)), raw)
    if raw:
        return (1, raw.upper(), 0, raw)
    return (2, "", 0, "")


def _new_writer(out_path: str | Path) -> tuple[Any, QPainter, float]:
    """QPdfWriter + painter; мм в единицах документа (300 dpi)."""
    global _APP
    if QGuiApplication.instance() is None:
        # QApplication обязателен до QPainter; локальная переменная гибнет
        # при выходе из функции → «access violation» на первом drawText.
        _APP = QGuiApplication([])
    writer = QPdfWriter(str(out_path))
    writer.setResolution(300)  # dpi: 1 мм ≈ 11.81 ед.
    return writer, QPainter(writer), writer.resolution() / 25.4


def _draw_text(
    painter: QPainter, rect: QRectF, text: str,
    font: QFont, color: QColor,
    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft,
    wrap: bool = False,
) -> None:
    painter.setFont(font)
    painter.setPen(color)
    flags = align | Qt.AlignmentFlag.AlignVCenter
    if wrap:
        flags |= Qt.TextFlag.TextWordWrap
    painter.drawText(rect, int(flags), text)


def _table_row_height(texts: list[str], widths: list[float], mm: float,
                      painter: QPainter) -> float:
    """Высота строки таблицы: по самой высокой обёрнутой ячейке (min 6 мм)."""
    height = 6.0
    for text, width in zip(texts, widths):
        rect = QRectF(0, 0, (width - 1.6) * mm, 100 * mm)
        bounds = painter.boundingRect(
            rect, int(Qt.TextFlag.TextWordWrap), text)
        height = max(height, bounds.height() / mm + 2.2)
    return height


def _draw_table(
    painter: QPainter, writer: Any, mm: float,
    headers: list[str], widths: list[float], rows: list[list[str]],
    *,
    font_size: float = 7.0, row_gap: float = 1.2,
    top_y: float = 0.0, page_end_y: float = 0.0,
) -> float:
    """Таблица с рамками и переносом страниц → y-курсор после последней строки.

    Ширины в мм; сумма должна давать ширину контента (_CONTENT_W_MM).
    """
    x0 = _MARGIN_MM * mm
    total = sum(widths) * mm
    head_h = 7.0 * mm
    pen = QPen(_LINE)
    pen.setWidthF(0.12 * mm)
    painter.setPen(pen)

    def draw_head(y: float) -> float:
        rect = QRectF(x0, y, total, head_h)
        painter.fillRect(rect, _HEAD_BG)
        painter.drawRect(rect)
        cx = x0
        for header, width in zip(headers, widths):
            _draw_text(
                painter, QRectF(cx + 0.8 * mm, y, width * mm - 1.6 * mm, head_h),
                header, _font(font_size, bold=True), _DARK)
            cx += width * mm
        return y + head_h

    y = top_y
    y = draw_head(y)
    body_font = _font(font_size)
    for row in rows:
        row_h = _table_row_height(row, widths, mm, painter) * mm
        if y + row_h > page_end_y:
            writer.newPage()
            y = _MARGIN_MM * mm
            y = draw_head(y)
        cx = x0
        for text, width in zip(row, widths):
            rect = QRectF(cx, y, width * mm, row_h)
            painter.drawRect(rect)
            _draw_text(
                painter, QRectF(cx + 0.8 * mm, y, width * mm - 1.6 * mm, row_h),
                text, body_font, _MID, wrap=True)
            cx += width * mm
        y += row_h + row_gap * mm
    return y


def _header_block(
    painter: QPainter, mm: float, title: str,
    info: list[tuple[str, str]], *,
    y: float = 0.0,
) -> float:
    """Заголовок документа + шапка (пары «метка: значение» в 2 колонки)."""
    y = max(y, _MARGIN_MM * mm)
    _draw_text(
        painter, QRectF(_MARGIN_MM * mm, y, _CONTENT_W_MM * mm, 9 * mm),
        title, _font(15, bold=True), _DARK,
        align=Qt.AlignmentFlag.AlignHCenter)
    y += 11 * mm
    half = (_CONTENT_W_MM - 8) * mm / 2
    value_font = _font(7.5)
    for index, (label, value) in enumerate(info):
        col, row = index % 2, index // 2
        x = _MARGIN_MM * mm + col * (half + 8 * mm)
        rect = QRectF(x, y + row * 5.6 * mm, half, 5.6 * mm)
        _draw_text(
            painter, rect, f"{label}: {value}" if value else f"{label}: —",
            value_font, _MID)
    y += ((len(info) + 1) // 2) * 5.6 * mm + 3 * mm
    return y


def generate_act_pdf(
    session: Any,
    devices: list[Device],
    out_path: str | Path,
    repo: Any = None,
) -> Path:
    """Акт инвентаризации: шапка, сводка, таблица, подписи. → путь PDF."""
    writer, painter, mm = _new_writer(out_path)
    page_end = (_PAGE_H_MM - _MARGIN_MM) * mm
    devices = sorted(devices, key=_device_sort_key)
    try:
        org = ""
        contract = ""
        if repo is not None:
            org = str(repo.get_setting("report_org") or "").strip()
            contract = str(repo.get_setting("report_contract") or "").strip()
        contractor = (getattr(session, "contractor", None) or "").strip()
        info = [
            ("Объект", getattr(session, "object_name", "") or ""),
            ("Сессия", getattr(session, "name", "") or ""),
            ("Инспектор", getattr(session, "inspector", "") or ""),
            ("Подрядчик", contractor or org),
            ("Начат", _fmt_dt(getattr(session, "started_at", ""))),
            ("Завершён", _fmt_dt(getattr(session, "finished_at", ""))
             or "сессия активна"),
        ]
        if contract:
            info.append(("Договор", contract))
        y = _header_block(painter, mm, "АКТ ИНВЕНТАРИЗАЦИИ ОБОРУДОВАНИЯ",
                          info)

        _draw_text(
            painter,
            QRectF(_MARGIN_MM * mm, y, _CONTENT_W_MM * mm, 6 * mm),
            f"Всего позиций: {len(devices)}",
            _font(8, bold=True), _DARK)
        y += 7.5 * mm

        headers = ["ID", "Тип", "Производитель и модель", "S/N", "MAC",
                   "IP", "Место", "Статус"]
        widths = [14.0, 22.0, 44.0, 26.0, 26.0, 18.0, 22.0, 18.0]
        rows = []
        for device in devices:
            location = device.location
            brand = (device.manufacturer or "").strip()
            model = (device.model or "").strip()
            rows.append([
                device.display_id or "",
                device_type_name(device.device_type),
                " ".join(part for part in (brand, model) if part)
                or "—",
                device.serial or "—",
                device.mac or "—",
                device.ip or "",
                location.short_label() if location else "",
                status_name(device.status),
            ])
        y = _draw_table(
            painter, writer, mm, headers, widths, rows,
            top_y=y, page_end_y=page_end)

        # Подписи: на текущей странице, если влезают; иначе — новая.
        if y + 26 * mm > page_end:
            writer.newPage()
            y = _MARGIN_MM * mm
        y += 8 * mm
        line_pen = QPen(_DARK)
        line_pen.setWidthF(0.2 * mm)
        painter.setPen(line_pen)
        for who in ("Инспектор", "Заказчик"):
            painter.setFont(_font(8))
            painter.drawText(
                QRectF(_MARGIN_MM * mm, y, 60 * mm, 5.5 * mm),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                f"{who}:")
            base = y + 5.2 * mm
            painter.drawLine(
                QPointF((_MARGIN_MM + 24) * mm, base),
                QPointF((_MARGIN_MM + 76) * mm, base))
            painter.drawText(
                QRectF((_MARGIN_MM + 82) * mm, y, 50 * mm, 5.5 * mm),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                "Дата:")
            painter.drawLine(
                QPointF((_MARGIN_MM + 94) * mm, base),
                QPointF((_MARGIN_MM + 120) * mm, base))
            y += 10 * mm
    finally:
        painter.end()
    return Path(out_path)


def followup_items(devices: list[Device]) -> list[tuple[Device, str]]:
    """Устройства, требующие докрутки, и что именно добрать.

    Критерии: нет S/N или MAC, камера/AP без связи с коммутатором,
    данные не подтверждены (is_verified=False).
    """
    items: list[tuple[Device, str]] = []
    for device in devices:
        todo: list[str] = []
        sn = (device.serial or "").strip()
        mac = (device.mac or "").strip()
        if not sn:
            todo.append("S/N")
        if not mac:
            todo.append("MAC")
        if device.device_type in ("camera", "ap") and not device.upstream_device_id:
            todo.append("связь с коммутатором")
        if not (device.photo_path or "").strip():
            todo.append("фото")
        if not device.is_verified:
            todo.append("подтвердить данные")
        if todo:
            items.append((device, ", ".join(todo)))
    return items


def generate_followup_pdf(
    session: Any,
    devices: list[Device],
    out_path: str | Path,
) -> Path:
    """Чек-лист докрутки: клетка, ID, место, что добрать. → путь PDF."""
    writer, painter, mm = _new_writer(out_path)
    page_end = (_PAGE_H_MM - _MARGIN_MM) * mm
    devices = sorted(devices, key=_device_sort_key)
    try:
        info = [
            ("Объект", getattr(session, "object_name", "") or ""),
            ("Сессия", getattr(session, "name", "") or ""),
            ("Дата печати", datetime.now().strftime("%d.%m.%Y")),
        ]
        y = _header_block(
            painter, mm, "ЧЕК-ЛИСТ ДОКРУТКИ ОБОРУДОВАНИЯ", info)
        items = followup_items(devices)
        _draw_text(
            painter,
            QRectF(_MARGIN_MM * mm, y, _CONTENT_W_MM * mm, 6 * mm),
            f"Позиций в очереди: {len(items)}",
            _font(8, bold=True), _DARK)
        y += 7.5 * mm

        headers = ["☐", "ID", "Тип", "Место", "Что добрать", "Бренд/модель",
                   "Примечание"]
        widths = [8.0, 14.0, 18.0, 22.0, 40.0, 40.0, 48.0]
        rows = [
            [
                "",
                device.display_id or "",
                device_type_name(device.device_type),
                device.location.short_label() if device.location else "",
                todo,
                " ".join(part for part in
                         ((device.manufacturer or "").strip(),
                          (device.model or "").strip()) if part),
                (device.note or "").strip()[:120],
            ]
            for device, todo in items
        ]
        _draw_table(
            painter, writer, mm, headers, widths, rows,
            top_y=y, page_end_y=page_end)
    finally:
        painter.end()
    return Path(out_path)

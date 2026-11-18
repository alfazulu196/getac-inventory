# tools/labels_pdf.py
r"""Печатный лист наклеек TIA-606 из labels.csv.

A4, 24 этикетки 65×35 мм на лист (3×8) — тот же формат, что у QR-бирок
v1.6.4, чтобы резать одним ножом и клеить в те же окна.
В ячейке: уровень (стойка/порт), крупная метка TIA-606 с автоподбором
кегля и человекочитаемые объект/помещение/стойка.

Запуск:
  python -X utf8 tools\labels_pdf.py
  python -X utf8 tools\labels_pdf.py --csv data\exports\tia606\labels.csv
  python -X utf8 tools\labels_pdf.py --out D:\наклейки.pdf
  python -X utf8 tools\tia606.py --db data\inventory.db --pdf
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QPainter,
    QPdfWriter,
    QPen,
)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # noqa: E402

# Формат листа — как у QR-бирок: A4, 3×8 этикеток 65×35 мм.
COLS, ROWS = 3, 8
LABEL_W, LABEL_H = 65.0, 35.0          # мм
PAGE_W, PAGE_H = 210.0, 297.0          # мм
MARGIN_X = (PAGE_W - COLS * LABEL_W) / 2   # 7.5 мм
MARGIN_Y = (PAGE_H - ROWS * LABEL_H) / 2   # 8.5 мм
FONT_FAMILY = "Segoe UI"               # полная кириллица, есть на Windows

_APP: QGuiApplication | None = None


def _ensure_app() -> None:
    """QGuiApplication живёт в модульной переменной: иначе GC роняет painter."""
    global _APP
    if QGuiApplication.instance() is None:
        _APP = QGuiApplication([])


def read_labels_csv(path: str | Path) -> list[dict[str, str]]:
    """labels.csv → строки словарями; пустой файл → пустой список.

    utf-8-sig: Excel при пересохранении добавляет BOM — без него первым
    ключом станет «\\ufefflabel» и метки молча пропадут.
    """
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _font(point_size: float, *, bold: bool = False) -> QFont:
    font = QFont(FONT_FAMILY)
    font.setPointSizeF(point_size)
    if bold:
        font.setBold(True)
    return font


def _fit_font_size(text: str, max_width: float, device: Any,
                   start: float = 20.0, minimum: float = 6.5) -> float:
    """Подбирает кегль так, чтобы метка влезла в ширину без переноса."""
    size = start
    while size > minimum:
        metrics = QFontMetricsF(_font(size, bold=True), device)
        if metrics.horizontalAdvance(text) <= max_width:
            return size
        size -= 0.5
    return minimum


def _draw_label(painter: QPainter, row: dict[str, str],
                x: float, y: float, mm: float) -> None:
    """Одна наклейка: рамка, уровень, метка крупно, расшифровка снизу."""
    w, h = LABEL_W * mm, LABEL_H * mm
    pen = QPen(QColor(180, 190, 200))
    pen.setWidthF(0.15 * mm)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(x, y, w, h), 2.0 * mm, 2.0 * mm)

    level = (row.get("level") or "rack").strip().lower()
    title = "TIA-606-C · ПОРТ" if level == "port" else "TIA-606-C · СТОЙКА"
    painter.setFont(_font(6.5))
    painter.setPen(QColor(120, 130, 144))
    painter.drawText(
        QRectF(x + 4 * mm, y + 2.2 * mm, w - 8 * mm, 4 * mm),
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        title)

    label = (row.get("label") or "").strip()
    inner_w = w - 8 * mm
    size = _fit_font_size(label, inner_w, painter.device())
    painter.setFont(_font(size, bold=True))
    painter.setPen(QColor(20, 26, 34))
    main_rect = QRectF(x + 4 * mm, y + 6.5 * mm, inner_w, 14 * mm)
    painter.save()
    painter.setClipRect(main_rect)
    painter.drawText(
        main_rect,
        int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
        label)
    painter.restore()

    parts = [row.get(key, "") for key in
             ("site", "room", "rack", "panel", "port")]
    human = " · ".join(p.strip() for p in parts if p and p.strip())
    if human:
        small = _font(6.5)
        metrics = QFontMetricsF(small, painter.device())
        human = metrics.elidedText(
            human, Qt.TextElideMode.ElideRight, inner_w)
        painter.setFont(small)
        painter.setPen(QColor(60, 70, 84))
        painter.drawText(
            QRectF(x + 4 * mm, y + 21.5 * mm, inner_w, 9 * mm),
            int(Qt.AlignmentFlag.AlignHCenter
                | Qt.AlignmentFlag.AlignTop
                | Qt.TextFlag.TextWordWrap),
            human)


def render_labels_pdf(rows: list[dict[str, str]],
                      out_path: str | Path) -> int:
    """Рисует лист(ы) наклеек → число меток; пустой список — файл не нужен."""
    printable = [row for row in rows if (row.get("label") or "").strip()]
    if not printable:
        return 0
    _ensure_app()
    writer = QPdfWriter(str(out_path))
    writer.setResolution(300)  # dpi: 1 мм ≈ 11.81 ед.
    mm = writer.resolution() / 25.4
    painter = QPainter(writer)
    try:
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        per_page = COLS * ROWS
        for index, row in enumerate(printable):
            slot = index % per_page
            if index and slot == 0:
                writer.newPage()
            col, line = slot % COLS, slot // COLS
            x = (MARGIN_X + col * LABEL_W) * mm
            y = (MARGIN_Y + line * LABEL_H) * mm
            _draw_label(painter, row, x, y, mm)
    finally:
        painter.end()
    return len(printable)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Печатный лист наклеек TIA-606 (A4, 65×35 мм)")
    ap.add_argument("--csv", default="data/exports/tia606/labels.csv",
                    help="исходный labels.csv (по умолчанию "
                         "data/exports/tia606/labels.csv)")
    ap.add_argument("--out", default="data/exports/tia606/labels.pdf",
                    help="куда писать PDF (по умолчанию "
                         "data/exports/tia606/labels.pdf)")
    args = ap.parse_args(argv)

    csv_path = Path(args.csv)
    if not csv_path.is_file():
        print(f"[X] Нет файла меток: {csv_path}")
        return 1
    rows = read_labels_csv(csv_path)
    rows = [row for row in rows if (row.get("label") or "").strip()]
    if not rows:
        print(f"[X] {csv_path}: нет строк с метками (нужна колонка label)")
        return 1
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = render_labels_pdf(rows, out_path)
    print(f"[OK] Лист наклеек: {out_path} ({count} шт.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# tools/labels_pdf.py
r"""Печатный лист наклеек TIA-606 из labels.csv.

A4, 24 этикетки 65×35 мм на лист (3×8) — тот же формат, что у QR-бирок
v1.6.4, чтобы резать одним ножом и клеить в те же окна.
В ячейке: QR «TIA=<метка>» (сканер сразу находит локацию в приложении),
крупная метка с автоподбором кегля и человекочитаемые объект/помещение.
На каждом листе — линейка 100 мм и метки реза: масштаб принтера видно
сразу, без внешней линейки.

Запуск:
  python -X utf8 tools\labels_pdf.py
  python -X utf8 tools\labels_pdf.py --csv data\exports\tia606\labels.csv
  python -X utf8 tools\labels_pdf.py --out D:\наклейки.pdf --preview
  python -X utf8 tools\labels_pdf.py --site "Корпус А" --room 101
  python -X utf8 tools\tia606.py --db data\inventory.db --pdf
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QLineF, QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QPainter,
    QPdfWriter,
    QPen,
)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # noqa: E402

from inventory.services.label_layout import (  # noqa: E402
    FONT_FAMILY,
    LABEL_H_MM,
    LABEL_W_MM,
    LABELS_PER_PAGE,
    PAGE_H_MM,
    PAGE_W_MM,
    page_count,
    slot_position,
)
from inventory.services.tia_labels import qr_payload  # noqa: E402

# Обратная совместимость с прежними именами модуля.
LABEL_W, LABEL_H = LABEL_W_MM, LABEL_H_MM
PAGE_W, PAGE_H = PAGE_W_MM, PAGE_H_MM
COLS, ROWS = 3, 8

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
    font.setBold(bold)
    return font


def _fit_font_size(text: str, max_width: float, device: Any,
                   start: float = 20.0, minimum: float = 6.0) -> float:
    """Подбирает кегль так, чтобы метка влезла в ширину без переноса."""
    size = start
    while size > minimum:
        metrics = QFontMetricsF(_font(size, bold=True), device)
        if metrics.horizontalAdvance(text) <= max_width:
            return size
        size -= 0.5
    return minimum


def _draw_qr(painter: QPainter, label: str, x: float, y: float,
             side: float) -> None:
    """QR с префиксом TIA=: приложение узнаёт метку, а не S/N."""
    from inventory.services.qr_tags import qr_image  # cv2 — только при QR

    qr = qr_image(qr_payload(label), size_px=int(side))
    painter.drawImage(QRectF(x, y, side, side), qr)


def _draw_label(painter: QPainter, row: dict[str, str],
                x: float, y: float, mm: float, *,
                with_qr: bool = True) -> None:
    """Одна наклейка: рамка, уровень, QR, метка крупно, расшифровка снизу."""
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
    qr_side = 20 * mm
    if with_qr and label:
        _draw_qr(painter, label, x + 3.5 * mm, y + (h - qr_side) / 2,
                 qr_side)
        tx, tw = x + 26.5 * mm, w - 30.5 * mm
    else:
        tx, tw = x + 4 * mm, w - 8 * mm
    size = _fit_font_size(label, tw, painter.device(),
                          minimum=5.5 if with_qr else 6.0)
    painter.setFont(_font(size, bold=True))
    painter.setPen(QColor(20, 26, 34))
    main_rect = QRectF(tx, y + 6.5 * mm, tw, 14 * mm)
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
        small = _font(6.2)
        metrics = QFontMetricsF(small, painter.device())
        human = metrics.elidedText(
            human, Qt.TextElideMode.ElideRight, tw)
        painter.setFont(small)
        painter.setPen(QColor(60, 70, 84))
        painter.drawText(
            QRectF(tx, y + 21 * mm, tw, 9 * mm),
            int(Qt.AlignmentFlag.AlignHCenter
                | Qt.AlignmentFlag.AlignTop
                | Qt.TextFlag.TextWordWrap),
            human)


def _draw_page_marks(painter: QPainter, mm: float, page: int,
                     pages: int) -> None:
    """Метки реза по углам A4, линейка 100 мм и номер листа.

    Линейка — контроль масштаба: если 0…100 мм не совпало с настоящей,
    принтер растянул PDF, и 65×35 мм наклеек уже не будет.
    """
    pen = QPen(QColor(150, 160, 172))
    pen.setWidthF(0.2 * mm)
    painter.setPen(pen)
    mark = 5 * mm
    # Уголки: по два штриха на каждый угол, внутрь листа.
    for x, dx in ((mark, 1), (PAGE_W * mm - mark, -1)):
        painter.drawLine(QLineF(x, mark, x + dx * 4 * mm, mark))
        painter.drawLine(QLineF(x, mark, x, mark + 4 * mm))
    for x, dx in ((mark, 1), (PAGE_W * mm - mark, -1)):
        painter.drawLine(QLineF(x, PAGE_H * mm - mark,
                                x + dx * 4 * mm, PAGE_H * mm - mark))
        painter.drawLine(QLineF(x, PAGE_H * mm - mark,
                                x, PAGE_H * mm - mark - 4 * mm))
    # Линейка 100 мм снизу, между наклейками и краем листа.
    x0 = 55 * mm
    y_line = (PAGE_H - 4.0) * mm
    painter.drawLine(QLineF(x0, y_line, x0 + 100 * mm, y_line))
    for step in range(0, 101, 10):
        tick = 3 * mm if step % 50 else 4 * mm
        painter.drawLine(QLineF(x0 + step * mm, y_line,
                                x0 + step * mm, y_line - tick))
    painter.setFont(_font(4.5))
    painter.setPen(QColor(90, 100, 112))
    for step in (0, 50, 100):
        painter.drawText(
            QRectF(x0 + (step - 5) * mm, y_line + 0.6 * mm, 10 * mm, 3 * mm),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
            str(step))
    painter.drawText(
        QRectF((PAGE_W - 77) * mm, y_line + 0.6 * mm, 70 * mm, 3 * mm),
        int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop),
        f"стр. {page}/{pages} · A4 · 100%")


def _printable(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if (row.get("label") or "").strip()]


def render_labels_pdf(rows: list[dict[str, str]], out_path: str | Path, *,
                      with_qr: bool = True) -> int:
    """Рисует лист(ы) наклеек → число меток; пустой список — файл не нужен."""
    printable = _printable(rows)
    if not printable:
        return 0
    _ensure_app()
    writer = QPdfWriter(str(out_path))
    writer.setResolution(300)  # dpi: 1 мм ≈ 11.81 ед.
    mm = writer.resolution() / 25.4
    painter = QPainter(writer)
    try:
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        pages = page_count(len(printable))
        current_page = -1
        for index, row in enumerate(printable):
            page = index // LABELS_PER_PAGE
            if page != current_page:
                if current_page >= 0:
                    writer.newPage()
                _draw_page_marks(painter, mm, page + 1, pages)
                current_page = page
            x, y = slot_position(index % LABELS_PER_PAGE, mm)
            _draw_label(painter, row, x, y, mm, with_qr=with_qr)
    finally:
        painter.end()
    return len(printable)


def render_labels_png(rows: list[dict[str, str]], out_path: str | Path, *,
                      dpi: int = 120, with_qr: bool = True) -> Path | None:
    """Первая страница в PNG: проверить вид и масштаб без принтера."""
    printable = _printable(rows)
    if not printable:
        return None
    _ensure_app()
    mm = dpi / 25.4
    image = QImage(round(PAGE_W * mm), round(PAGE_H * mm),
                   QImage.Format.Format_RGB32)
    image.setDotsPerMeterX(round(dpi / 0.0254))
    image.setDotsPerMeterY(round(dpi / 0.0254))
    image.fill(QColor("white"))
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        _draw_page_marks(painter, mm, 1, page_count(len(printable)))
        for index, row in enumerate(printable[:LABELS_PER_PAGE]):
            x, y = slot_position(index, mm)
            _draw_label(painter, row, x, y, mm, with_qr=with_qr)
    finally:
        painter.end()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(out))
    return out


def _filter_rows(rows: list[dict[str, str]], site: str | None,
                 room: str | None) -> list[dict[str, str]]:
    """Фильтр по объекту/помещению: подстрока без учёта регистра."""
    result = rows
    if site:
        needle = site.casefold()
        result = [row for row in result
                  if needle in (row.get("site") or "").casefold()]
    if room:
        needle = room.casefold()
        result = [row for row in result
                  if needle in (row.get("room") or "").casefold()]
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Печатный лист наклеек TIA-606 (A4, 65×35 мм)")
    ap.add_argument("--csv", default="data/exports/tia606/labels.csv",
                    help="исходный labels.csv (по умолчанию "
                         "data/exports/tia606/labels.csv)")
    ap.add_argument("--out", default="data/exports/tia606/labels.pdf",
                    help="куда писать PDF (по умолчанию "
                         "data/exports/tia606/labels.pdf)")
    ap.add_argument("--site", default=None,
                    help="только этот объект (подстрока)")
    ap.add_argument("--room", default=None,
                    help="только это помещение (подстрока)")
    ap.add_argument("--no-qr", action="store_true",
                    help="без QR на наклейке (только текст)")
    ap.add_argument("--preview", nargs="?", const="", default=None,
                    metavar="PNG",
                    help="первая страница в PNG (по умолчанию рядом с PDF)")
    args = ap.parse_args(argv)

    csv_path = Path(args.csv)
    if not csv_path.is_file():
        print(f"[X] Нет файла меток: {csv_path}")
        return 1
    rows = _filter_rows(read_labels_csv(csv_path), args.site, args.room)
    rows = _printable(rows)
    if not rows:
        print(f"[X] {csv_path}: нет строк с метками по фильтру — "
              f"печатать нечего")
        return 1
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = render_labels_pdf(rows, out_path, with_qr=not args.no_qr)
    print(f"[OK] Лист наклеек: {out_path} ({count} шт.)")
    if args.preview is not None:
        preview_path = (Path(args.preview) if args.preview else
                        out_path.with_name(out_path.stem + "_preview.png"))
        render_labels_png(rows, preview_path, with_qr=not args.no_qr)
        print(f"[OK] Предпросмотр: {preview_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

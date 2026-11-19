# inventory/services/label_layout.py
"""Единая раскладка печатных наклеек: A4, 3×8, 65×35 мм.

Один модуль на QR-бирки оборудования и TIA-606-метки: если форматы
разъедутся, листы перестанут резаться одним ножом и клеиться в те же
окна. Здесь только числа и чистая арифметика — без Qt.
"""
from __future__ import annotations

PAGE_W_MM, PAGE_H_MM = 210.0, 297.0          # A4
COLUMNS, ROWS = 3, 8
LABEL_W_MM, LABEL_H_MM = 65.0, 35.0          # мм
MARGIN_X_MM = (PAGE_W_MM - COLUMNS * LABEL_W_MM) / 2   # 7.5 мм
MARGIN_Y_MM = (PAGE_H_MM - ROWS * LABEL_H_MM) / 2      # 8.5 мм
LABELS_PER_PAGE = COLUMNS * ROWS

# Есть на любой Windows, полная кириллица — без «тофу» в PDF.
FONT_FAMILY = "Segoe UI"


def slot_position(slot: int, mm: float) -> tuple[float, float]:
    """Позиция наклейки slot (0..23) в единицах QPdfWriter."""
    col, row = slot % COLUMNS, slot // COLUMNS
    return (
        (MARGIN_X_MM + col * LABEL_W_MM) * mm,
        (MARGIN_Y_MM + row * LABEL_H_MM) * mm,
    )


def page_count(labels: int) -> int:
    """Сколько листов A4 нужно для labels наклеек."""
    return max(1, (labels + LABELS_PER_PAGE - 1) // LABELS_PER_PAGE)

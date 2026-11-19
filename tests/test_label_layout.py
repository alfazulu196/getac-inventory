# tests/test_label_layout.py
"""Единая раскладка наклеек: координаты и число страниц."""
from __future__ import annotations

from inventory.services.label_layout import (
    LABEL_H_MM,
    LABEL_W_MM,
    LABELS_PER_PAGE,
    MARGIN_X_MM,
    MARGIN_Y_MM,
    PAGE_H_MM,
    PAGE_W_MM,
    page_count,
    slot_position,
)


def test_a4_three_by_eight():
    assert LABELS_PER_PAGE == 24
    assert abs(MARGIN_X_MM - 7.5) < 1e-9
    assert abs(MARGIN_Y_MM - 8.5) < 1e-9
    assert PAGE_W_MM == 210.0 and PAGE_H_MM == 297.0
    assert LABEL_W_MM == 65.0 and LABEL_H_MM == 35.0


def test_slot_positions_grid():
    assert slot_position(0, 1.0) == (MARGIN_X_MM, MARGIN_Y_MM)
    x, y = slot_position(1, 1.0)
    assert x == MARGIN_X_MM + LABEL_W_MM and y == MARGIN_Y_MM
    x, y = slot_position(3, 1.0)
    assert x == MARGIN_X_MM and y == MARGIN_Y_MM + LABEL_H_MM
    # Последняя наклейка не выходит за лист.
    x, y = slot_position(23, 1.0)
    assert x + LABEL_W_MM <= PAGE_W_MM
    assert y + LABEL_H_MM <= PAGE_H_MM


def test_page_count():
    assert page_count(0) == 1
    assert page_count(1) == 1
    assert page_count(24) == 1
    assert page_count(25) == 2
    assert page_count(49) == 3

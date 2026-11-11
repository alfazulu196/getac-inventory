# tests/test_debrief.py
"""Тесты счётчика дебрифинга — измерительный прибор пятничного A/B.

Гарантируем: парсер позиций, сравнение моделей и сверкка кадра с эталоном
работают ровно так, как описано в tools/debrief_report.py; эталон
bench/expected.json не разошёлся с генератором макета.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))  # noqa: E402

from debrief_report import (  # noqa: E402
    _model_close,
    expand_positions,
    score_frame,
)


# ---------------------------------------------------------------- позиции
@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("U9-U10", {9, 10}),
        ("U3", {3}),
        ("U5-U8", {5, 6, 7, 8}),
        ("U2-U3, U7", {2, 3, 7}),
        ("", set()),
        ("нижняя треть", set()),
    ],
)
def test_expand_positions(text: str, want: set[int]) -> None:
    assert expand_positions(text) == want


def test_expand_positions_partial_range() -> None:
    """Модель назвала один U из диапазона — пересечение непусто."""
    assert expand_positions("U2-U3") & expand_positions("U3")


# ---------------------------------------------------------------- модели
@pytest.mark.parametrize(
    ("a", "b", "want"),
    [
        ("DES-1210-10/ME", "des 1210 10 me", True),   # регистр/разделители
        ("WS-C2960+24TC-L", "WS-C2960+24TC-L", True),
        ("", "что угодно", True),                     # пустое не противоречит
        ("DES-1210-10/ME", "DES-1210", True),         # обрезанное чтение
        ("TL-SG1016", "TL-SG1024", False),            # разные модели честно
        ("ProLiant DL360 G9", "ProLiant DL380 G9", False),
        ("Smart-UPS SMT750R", "Smar-UPS SMT750R", False),  # опечатка = частично
    ],
)
def test_model_close(a: str, b: str, want: bool) -> None:
    assert _model_close(a, b) == want


# ---------------------------------------------------------------- сверка кадра
_EXP = [
    {"position": "U9-U10", "brand": "Cisco", "model": "WS-C2960+24TC-L",
     "type": "switch"},
    {"position": "U8", "brand": "", "model": "", "type": "patch"},
    {"position": "U1", "brand": "APC", "model": "SMT750R", "type": "ups"},
]


def test_score_full_match() -> None:
    actual = [
        {"type": "switch", "brand": "Cisco", "model": "WS-C2960+24TC-L",
         "position": "U10", "detail": ""},
    ]
    full, partial, missed, notes = score_frame(actual, _EXP[:1])
    assert (full, partial, missed) == (1, 0, 0)
    assert not any("ПРОПУЩЕН" in n for n in notes)


def test_score_neighbor_position_is_partial() -> None:
    """Патч-панель без бренда: подпись в brand, позиция соседняя."""
    actual = [
        {"type": "patch", "brand": "Patch Panel 24", "model": "",
         "position": "U9", "detail": ""},
    ]
    full, partial, missed, notes = score_frame(actual, [_EXP[1]])
    assert (full, partial, missed) == (0, 1, 0)
    assert any("позиция" in n for n in notes)


def test_score_wildcard_brand_requires_same_type() -> None:
    """Бренд-заглушка сверяется по типу: чужой тип не должен маскировать."""
    actual = [
        {"type": "switch", "brand": "TP-LINK", "model": "TL-SG1016",
         "position": "U8", "detail": ""},
    ]
    full, partial, missed, _ = score_frame(actual, [_EXP[1]])
    assert missed == 1  # это switch, а не патч-панель — не «наш» юнит


def test_score_missed_when_no_candidate() -> None:
    full, partial, missed, notes = score_frame([], [_EXP[2]])
    assert (full, partial, missed) == (0, 0, 1)
    assert any("ПРОПУЩЕН" in n for n in notes)


def test_score_prefers_full_match_over_duplicate() -> None:
    """Два ряда на U1 (switch и ups): выбирается совпадающий по типу."""
    actual = [
        {"type": "switch", "brand": "APC", "model": "SMT750R",
         "position": "U1", "detail": ""},
        {"type": "ups", "brand": "APC", "model": "SMT750R",
         "position": "U1", "detail": ""},
    ]
    full, partial, missed, notes = score_frame(actual, [_EXP[2]])
    assert (full, partial, missed) == (1, 0, 0)
    assert any("лишний" in n for n in notes)


def test_score_extra_rows_are_flagged() -> None:
    actual = [
        {"type": "other", "brand": "", "model": "", "position": "",
         "detail": "green indicator"},
    ]
    _, _, missed, notes = score_frame(actual, [_EXP[2]])
    assert missed == 1
    assert any("лишний" in n for n in notes)


# ------------------------------------------------- эталон ↔ генератор макета
def _truth_from_units(units: list[tuple]) -> list[dict]:
    kind2type = {
        "switch24": "switch", "switch16": "switch", "patch": "patch",
        "nvr": "nvr", "pdu": "psu", "router": "router",
        "server": "server", "ups": "ups",
    }
    truth = []
    for (lo, hi), brand, model, _face, _text, _sub, kind in units:
        truth.append(
            {
                "position": f"U{lo}-U{hi}" if hi > lo else f"U{lo}",
                "brand": brand,
                "model": model,
                "type": kind2type[kind],
            }
        )
    return truth


def test_expected_json_matches_generators() -> None:
    """Эталон бенчмарка не разошёлся с тем, что реально нарисовано."""
    sys.path.insert(0, str(ROOT / "tools"))  # noqa: E402

    import make_bench_set as bench
    import make_rack_mock as mrm

    expected = json.loads(
        (ROOT / "tools" / "assets" / "bench" / "expected.json")
        .read_text(encoding="utf-8")
    )
    assert expected["rack_mock.jpg"] == _truth_from_units(mrm.UNITS)
    assert expected["rack_dense15.jpg"] == _truth_from_units(bench.DENSE_UNITS)
    visible = {"U1", "U2-U3", "U4", "U5"}
    partial = [u for u in _truth_from_units(mrm.UNITS) if u["position"] in visible]
    assert expected["rack_partial.jpg"] == partial

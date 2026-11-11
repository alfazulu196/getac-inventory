# tests/test_photo_intel.py
"""Кандидаты полей из строк OCR и штрихкодов (unit — без OCR/камеры)."""
from __future__ import annotations

import pytest

from inventory.services.photo_intel import PhotoIntel, extract_candidates


def test_strict_mac_is_kept():
    result = extract_candidates(["MAC ID: A0AB1B40C3E0"], [])
    assert result["macs"] == ["A0AB1B40C3E0"]


def test_serial_prefix_does_not_spawn_phantom_mac():
    """S/N длиннее 12 hex-символов не должен рождать фантомные MAC."""
    result = extract_candidates(
        ["SN: S3CF1039000588", "Serial B3CF1039000588"],
        [("CODE39", "S3CF1G9900566")],
    )
    serials = result["serials"]
    assert "S3CF1039000588" in serials
    assert "S3CF1G9900566" in serials
    for mac in result["macs"]:
        # Ни один MAC не начинается с «обрезанного» S/N
        assert not mac.startswith("3CF1039")
        assert not mac.startswith("53CF1039")


def test_russian_dirty_mac_normalized():
    # О → 0, В → B, Р → C, Ф → F, З → 3, Е → E
    result = extract_candidates(["МАС АОАВ1В4РФЗЕ0"], [])
    assert result["macs"] == ["A0AB1B4CF3E0"]


def test_brcoded_serial_added():
    result = extract_candidates([], [("CODE39", "S3CF1G9900566")])
    assert result["serials"] == ["S3CF1G9900566"]


def test_barcode_strip_noise():
    """Строка-«полоса» от OCR на штрихкоде не даёт ни MAC, ни S/N."""
    line = "I III II III III I III I i III II i I II II Ill I"
    result = extract_candidates([line], [])
    assert result["macs"] == []
    assert result["serials"] == []


def test_model_from_label_line():
    result = extract_candidates(["DES-1210-10/ME(B1A)"], [])
    assert "DES-1210-10/ME(B1A)" in result["models"]


def test_part_number_from_label():
    result = extract_candidates(["P/N: RES121010MEA.B1G"], [])
    assert result["part_numbers"] == ["RES121010MEA"]


def test_ip_validated():
    result = extract_candidates(["IP 192.168.10.55", "IP 300.1.1.1"], [])
    assert result["ips"] == ["192.168.10.55"]


def test_sn_bracket_style():
    result = extract_candidates(["S/N:CASG54321XYZ"], [])
    assert "CASG54321XYZ" in result["serials"]


def test_duplicate_lines_single_candidate():
    result = extract_candidates(["MAC A0AB1B40C3E0", "MAC A0AB1B40C3E0"], [])
    assert result["macs"].count("A0AB1B40C3E0") == 1


def test_photo_intel_summary():
    intel = PhotoIntel(serials=["SN123"], macs=["A0AB1B40C3E0"])
    assert "SN123" in intel.summary() and "A0AB1B40C3E0" in intel.summary()
    empty = PhotoIntel()
    assert empty.summary() == "Ничего не распознано"
    assert not empty.has_candidates


@pytest.mark.parametrize(
    "token,expected",
    [
        ("A0AB1B40C3E0", "A0AB1B40C3E0"),
        ("а0ав1в4рфзе0", "A0AB1B4CF3E0"),
        ("S3CF1039000588", None),  # 14 знаков — это S/N, не MAC
        ("ZZZZZZZZ:1:2:", None),
        ("00:1A:2B:3C:4D:5E", "001A2B3C4D5E"),
    ],
)
def test_hex_normalize_direct(token, expected):
    from inventory.services.photo_intel import _hex_normalize

    assert _hex_normalize(token) == expected

# tests/test_oui.py
"""Подстановка производителя по MAC: мини-таблица + полная база IEEE."""
from __future__ import annotations

import pytest

from inventory.core.oui import (
    OUI_VENDORS,
    load_full_vendor_db,
    lookup_mac_vendor,
    vendor_db_path,
    vendor_db_status,
)


def test_lookup_with_separators():
    assert lookup_mac_vendor("44:19:B6:12:0A:5F") == "Hikvision"


def test_lookup_compact():
    assert lookup_mac_vendor("4419B60A81F2") == "Hikvision"


def test_lookup_lower():
    assert lookup_mac_vendor("c0-56-e3-aa-bb-cc") == "Hikvision"


def test_lookup_partial():
    """Первые 3 октета достаточно, остаток не анализируется."""
    assert lookup_mac_vendor("00:00:0C") == "Cisco"
    assert lookup_mac_vendor("00000C99") == "Cisco"


def test_unknown_prefix_is_none():
    """Префикс, которого не было ни в мини-таблице, ни в базе IEEE."""
    assert lookup_mac_vendor("11:22:33:44:55:66") is None
    assert lookup_mac_vendor("aa:bb:cc:00:00:00") is None


def test_garbage_is_none():
    """Нечисло hex или слишком короткая строка — производитель не подставляется."""
    assert lookup_mac_vendor("ZZ:19:B6:00:00:00") is None
    assert lookup_mac_vendor("") is None
    assert lookup_mac_vendor(None) is None
    assert lookup_mac_vendor("44:19") is None


# ---------------------------------------------------------------------------
# Полная база IEEE (v1.5.0)
# ---------------------------------------------------------------------------


def test_mini_table_never_dictates_style():
    """Мини-таблица отвечает короткими торговыми именами."""
    for vendor in OUI_VENDORS.values():
        assert "inc" not in vendor.lower()
        assert "ltd" not in vendor.lower()


def test_dlink_from_full_ieee_db():
    """MAC A0AB1B — файл oui.csv; полная база должна отдать D-Link."""
    assert vendor_db_path() is not None and vendor_db_path().is_file()
    count = load_full_vendor_db()
    assert count > 40000, f"полная база слишком мала: {count}"
    assert lookup_mac_vendor("A0-AB-1B-40-C3-E0") == "D-Link"
    assert vendor_db_status().startswith("полная база IEEE")


def test_full_db_does_not_override_mini_table():
    """Мини-таблица главнее: привычные trading names не заменить юрформами."""
    load_full_vendor_db()
    for prefix, vendor in (("0080d2", "Eltex"), ("4419b6", "Hikvision"), ("9002a9", "Dahua")):
        assert lookup_mac_vendor(prefix + "01020304") == vendor


def test_tidy_vendor_name():
    from inventory.core.oui import _tidy_name

    assert _tidy_name("D-Link International") == "D-Link"
    assert _tidy_name("Hikvision Digital Technology Co.,Ltd.") == "Hikvision Digital Technology"
    assert _tidy_name("Cisco Systems, Inc") == "Cisco Systems"
    assert _tidy_name("Apple,  Inc.") == "Apple"
    assert _tidy_name("ASRock Incorporation") == "ASRock"


@pytest.mark.parametrize(
    "mac,expected",
    [
        ("32000:00:00", None),
        ("00:00:0c:9f:f0:ff", "Cisco"),
    ],
)
def test_mixed_inputs(mac, expected):
    assert lookup_mac_vendor(mac) == expected

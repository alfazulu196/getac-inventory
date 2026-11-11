# inventory/core/oui.py
"""Офлайн-подстановка производителя по MAC-адресу (OUI — первые 3 октета).

Два источника:
1) Мини-таблица OUI_VENDORS — только префиксы, надёжно закреплённые за
   вендорами видеонаблюдения (даёт привычные имена: Hikvision, Dahua…).
2) Полная база IEEE (inventory/data/oui.csv, ~40 тыс. записей MA-L) —
   грузится в память при первом обращении; имена слегка причёсываются
   (убираются юрформы: «International», «Inc.», «Co.,Ltd.» и т.п.).
Если префикс нигде не найден — вернётся None, поле заполняет пользователь.
"""
from __future__ import annotations

import re
import sys
import threading
from pathlib import Path

# Ключ — первые 6 hex-символов MAC в нижнем регистре без разделителей.
OUI_VENDORS: dict[str, str] = {
    # Eltex (камеры/коммутаторы в РФ-объектах)
    "0080d2": "Eltex",
    # Cisco (классические OUI коммутаторов и роутеров)
    "00000c": "Cisco",
    "001aa1": "Cisco",
    "002545": "Cisco",
    "002304": "Cisco",
    # TP-LINK
    "50c7bf": "TP-LINK",
    # Ubiquiti Networks
    "24a43c": "Ubiquiti",
    "788a20": "Ubiquiti",
    "f09fc2": "Ubiquiti",
    # MikroTik
    "000c42": "MikroTik",
    "d8cb8a": "MikroTik",
    "488f5a": "MikroTik",
    # D-Link
    "00055d": "D-Link",
    # NETGEAR
    "00095b": "NETGEAR",
    "001b2f": "NETGEAR",
    # Huawei
    "001882": "Huawei",
    # Zyxel
    "0019cb": "Zyxel",
    # Hikvision (камеры/регистраторы)
    "4419b6": "Hikvision",
    "c056e3": "Hikvision",
    # Dahua (камеры/регистраторы)
    "9002a9": "Dahua",
    # AXIS Communications (камеры)
    "00408c": "AXIS",
    # Hanwha Vision (бывш. Samsung Techwin, камеры)
    "0016e6": "Hanwha Techwin",
    # VIVOTEK (камеры)
    "0002d1": "VIVOTEK",
    # Samsung Electronics
    "001632": "Samsung",
    # Apple
    "000393": "Apple",
    "001b63": "Apple",
    # Intel
    "001b21": "Intel",
    # Realtek
    "00e04c": "Realtek",
    # Виртуальные машины (вдруг в таблице окажется «серверная» машина)
    "000c29": "VMware",
    "005056": "VMware",
    "00155d": "Microsoft Hyper-V",
    "00163e": "Xen",
    "080027": "VirtualBox",
    # Raspberry Pi
    "b827eb": "Raspberry Pi",
    "dca632": "Raspberry Pi",
    "e45f01": "Raspberry Pi",
    # Espressif (модули IoT)
    "5ccf7f": "Espressif",
    "246f28": "Espressif",
    "30aea4": "Espressif",
}

_HEX_LOOSE = re.compile(r"[^0-9a-f]")

# Юрформы, отрезаемые с конца имени организации из полной базы IEEE.
_FORM_SUFFIXES = {
    "inc", "inc.", "incorporated", "incorporation", "corporation", "corp",
    "corp.", "ltd", "ltd.", "limited", "llc", "llp", "lp", "plc", "gmbh",
    "kg", "ag", "ab", "as", "co", "co.", "co.,", "co.,ltd", "co.,ltd.",
    "company", "international", "sa", "s.a.", "pty", "c.o.", "ltd.co.",
    "co.ltd.", "co.ltd,,ltd",
}

_full_vendors: dict[str, str] | None = None
_full_load_error: str | None = None
_full_lock = threading.Lock()


def vendor_db_path() -> Path | None:
    """inventory/data/oui.csv: в исходниках — рядом с пакетом, в exe — в _MEIPASS."""
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        bundled = meipass / "inventory" / "data" / "oui.csv"
        if bundled.is_file():
            return bundled
        flat = meipass / "oui.csv"
        return flat if flat.is_file() else None
    here = Path(__file__).resolve().parent      # core/
    return here.parent / "data" / "oui.csv"     # inventory/data/oui.csv


def _tidy_name(raw: str) -> str:
    name = " ".join(str(raw).split())
    tokens = name.split()
    while len(tokens) > 1 and tokens[-1].lower() in _FORM_SUFFIXES:
        tokens.pop()
    cleaned = " ".join(tokens).rstrip(",.")
    return cleaned or name


def load_full_vendor_db() -> int:
    """Загрузить полную базу IEEE. Возвращает число записей (0 — файла нет)."""
    global _full_vendors, _full_load_error
    with _full_lock:
        if _full_vendors is not None:
            return len(_full_vendors)
        path = vendor_db_path()
        if path is None:
            _full_load_error = "файл oui.csv не найден"
            return 0
        table: dict[str, str] = {}
        try:
            import csv

            with open(path, encoding="utf-8-sig", errors="replace", newline="") as handle:
                for row in csv.reader(handle):
                    if len(row) < 3:
                        continue
                    assignment = row[1].strip().lower()
                    if len(assignment) != 6 or any(c not in "0123456789abcdef" for c in assignment):
                        continue
                    table[assignment] = _tidy_name(row[2])
        except OSError as exc:
            _full_load_error = str(exc)
            return 0
        _full_vendors = table
        return len(table)


def vendor_db_status() -> str:
    """Краткий статус полной базы: «готова (40 459)» или причина отсутствия."""
    if _full_vendors is not None:
        return f"полная база IEEE ({len(_full_vendors)})"
    if _full_load_error:
        return f"полная база недоступна: {_full_load_error}"
    path = vendor_db_path()
    return "полная база готова" if path and path.is_file() else "полная база не входит в сборку"


def warmup_vendor_db() -> None:
    """Прогреть полную базу в фоне (не блокирует интерфейс)."""

    def _run() -> None:
        try:
            load_full_vendor_db()
        except Exception:  # noqa: BLE001 — прогрев не должен ронять приложение
            pass

    threading.Thread(target=_run, name="oui-warmup", daemon=True).start()


def lookup_mac_vendor(mac: str | None) -> str | None:
    """Производитель по MAC-адресу любого привычного формата или None."""
    if not mac:
        return None
    digits = _HEX_LOOSE.sub("", str(mac).lower())
    if len(digits) < 6:
        return None
    vendor = OUI_VENDORS.get(digits[:6])
    if vendor:
        return vendor
    if _full_vendors is None:
        load_full_vendor_db()
    full = _full_vendors or {}
    return full.get(digits[:6])

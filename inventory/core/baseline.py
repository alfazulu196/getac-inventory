# inventory/core/baseline.py
"""Сверка с прошлой инвентаризацией.

Читает прежнюю Excel-таблицу (например, getac_ai_inventory.xlsx старого
прототипа), складывает записи о прошлых устройствах в базу (baseline_devices)
и сравнивает с текущим обходом: что совпало, что новое, что пропало,
у каких устройств изменились поля.

Сопоставление — по нормализованному S/N и MAC (как в проверке дублей).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from inventory.core.models import Device
from inventory.core.parser import normalize_mac_compact, normalize_serial

HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "display_id": ("id", "устройство", "ид", "камера", "код", "объект", "номер"),
    "device_type": ("тип", "тип устройства", "категория", "группа", "type"),
    "manufacturer": ("производитель", "бренд", "vendor"),
    "model": ("модель", "model"),
    "serial": ("serial", "s/n", "s\\n", "sн", "сн", "серийный", "серийный номер",
               "серийник", "serial_number", "serialno"),
    "mac": ("mac", "мак", "mac-адрес", "mac адрес", "macaddr"),
    "ip": ("ip", "ip-адрес", "ip адрес", "ipaddress"),
    "location_label": ("локация", "место", "местоположение", "расположение",
                       "адрес установки", "этаж", "помещение", "куст", "сектор",
                       "полигон", "объект установки"),
    "status": ("статус", "состояние", "state"),
}


@dataclass
class ComparisonResult:
    matched: list[tuple[Device, dict[str, Any], list[str]]] = field(default_factory=list)
    changed: list[tuple[Device, dict[str, Any], list[str]]] = field(default_factory=list)
    new: list[Device] = field(default_factory=list)
    missing: list[dict[str, Any]] = field(default_factory=list)
    unmatched_baseline: list[dict[str, Any]] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)


def _header_key(header: Any) -> str | None:
    text = str(header or "").strip().lower().replace(" ", " ").replace("ё", "е")
    text = text.replace("-", " ").replace("_", " ")
    text = " ".join(text.split())
    for key, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            if text == alias or text.endswith(":" + alias) or text == alias.replace(" ", " "):
                return key
        for alias in aliases:
            if alias and alias in text:
                return key
    return None


def parse_baseline(path: str | Path) -> list[dict[str, Any]]:
    """Читает таблицу прошлой инвентаризации и возвращает нормализованные строки."""
    from openpyxl import load_workbook

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")
    workbook = load_workbook(str(path), read_only=True, data_only=True)
    sheet = None
    for candidate in ("Устройства", "Devices", "Данные"):
        if candidate in workbook.sheetnames:
            sheet = workbook[candidate]
            break
    if sheet is None:
        sheet = workbook.worksheets[0]

    rows_raw = list(sheet.iter_rows(values_only=True))
    workbook.close()
    if not rows_raw:
        return []

    header: dict[int, str] = {}
    for index, value in enumerate(rows_raw[0]):
        key = _header_key(value)
        if key and key not in header.values():
            header[index] = key

    result: list[dict[str, Any]] = []
    for values in rows_raw[1:]:
        record: dict[str, Any] = {}
        for index, key in header.items():
            if index < len(values):
                record[key] = values[index]
        serial = str(record.get("serial") or "").strip() or None
        mac = str(record.get("mac") or "").strip() or None
        display = str(record.get("display_id") or "").strip() or None
        if not (serial or mac or display):
            continue
        record["serial"] = serial
        record["mac"] = mac
        record["display_id"] = display
        record["serial_norm"] = normalize_serial(serial)
        record["mac_norm"] = normalize_mac_compact(mac)
        if isinstance(record.get("ip"), float) and record["ip"].is_integer():
            record["ip"] = str(int(record["ip"]))
        else:
            record["ip"] = str(record.get("ip") or "").strip() or None
        for field_name in ("device_type", "manufacturer", "model",
                           "location_label", "status"):
            record[field_name] = str(record.get(field_name) or "").strip() or None
        result.append(record)
    return result


def session_baseline_rows(repo, source_session_id: str) -> list[dict[str, Any]]:
    """Строки «прошлой таблицы» из архивной сессии этой же базы.

    Каждая строка повторяет формат parse_baseline: ключи display_id,
    device_type, manufacturer, model, serial, mac, ip, location_label, status
    плюс нормализованные serial_norm/mac_norm. Тогда сверка с архивной
    сессией работает через compare_baseline без Excel-файла.
    """
    rows: list[dict[str, Any]] = []
    for device in repo.list_devices(source_session_id):
        rows.append(
            {
                "display_id": device.display_id,
                "device_type": device.device_type,
                "manufacturer": device.manufacturer,
                "model": device.model,
                "serial": device.serial,
                "mac": device.mac,
                "ip": device.ip,
                "location_label": (
                    device.location.label() if device.location else None
                ),
                "status": device.status,
                "serial_norm": normalize_serial(device.serial),
                "mac_norm": normalize_mac_compact(device.mac),
            }
        )
    return rows


def _device_ident(device: Device) -> tuple[str, str]:
    return device.serial_norm or "", device.mac_norm or ""


def compare_baseline(
    baseline_rows: list[dict[str, Any]], devices: list[Device]
) -> ComparisonResult:
    """Разбирает устройства текущего обхода против baseline."""
    result = ComparisonResult()
    baseline_by_serial: dict[str, dict[str, Any]] = {}
    baseline_by_mac: dict[str, dict[str, Any]] = {}
    for row in baseline_rows:
        if row.get("serial_norm"):
            baseline_by_serial.setdefault(row["serial_norm"], row)
        if row.get("mac_norm"):
            baseline_by_mac.setdefault(row["mac_norm"], row)

    used_baseline_keys: set[tuple[str, str]] = set()
    for device in devices:
        serial_norm, mac_norm = _device_ident(device)
        baseline_row = None
        if serial_norm and serial_norm in baseline_by_serial:
            baseline_row = baseline_by_serial[serial_norm]
        elif mac_norm and mac_norm in baseline_by_mac:
            baseline_row = baseline_by_mac[mac_norm]
        if baseline_row is None:
            if serial_norm or mac_norm:
                result.new.append(device)
            continue
        used_baseline_keys.add(
            (baseline_row.get("serial_norm") or "", baseline_row.get("mac_norm") or "")
        )
        changes: list[str] = []
        device_location = device.location.label() if device.location else ""
        pairs = (
            ("Модель", baseline_row.get("model"), device.model),
            ("IP", baseline_row.get("ip"), device.ip),
            ("Локация", baseline_row.get("location_label"), device_location),
        )
        for label, old, new in pairs:
            old_text = str(old or "").strip()
            new_text = str(new or "").strip()
            if old_text and new_text and old_text.lower() != new_text.lower():
                changes.append(f"{label}: было «{old_text}», стало «{new_text}»")
        if changes:
            result.changed.append((device, baseline_row, changes))
        else:
            result.matched.append((device, baseline_row, []))

    for row in baseline_rows:
        key = (row.get("serial_norm") or "", row.get("mac_norm") or "")
        if row.get("serial_norm") or row.get("mac_norm"):
            if key not in used_baseline_keys:
                result.missing.append(row)
        else:
            result.unmatched_baseline.append(row)
    result.counts["matched"] = len(result.matched)
    result.counts["changed"] = len(result.changed)
    result.counts["new"] = len(result.new)
    result.counts["missing"] = len(result.missing)
    result.counts["no_baseline_id"] = len(result.unmatched_baseline)
    result.counts["baseline_rows"] = len(baseline_rows)
    result.counts["no_device_id"] = sum(
        1 for d in devices if not (_device_ident(d)[0] or _device_ident(d)[1])
    )
    return result

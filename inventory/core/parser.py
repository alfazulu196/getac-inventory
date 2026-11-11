# inventory/core/parser.py
"""Локальный разбор строк сканера: MAC, S/N, EAN, IP, структурированный QR."""
from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import parse_qs, urlparse


class CodeType(str, Enum):
    MAC = "mac"
    SERIAL = "serial"
    EAN = "ean"
    IP = "ip"
    QR = "qr"
    UNKNOWN = "unknown"


@dataclass
class ParsedCode:
    raw: str
    code_type: CodeType
    value: str | None = None
    fields: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0
    note: str = ""

    @property
    def serial(self) -> str | None:
        return self.fields.get("serial")

    @property
    def mac(self) -> str | None:
        return self.fields.get("mac")

    @property
    def ean(self) -> str | None:
        return self.fields.get("ean")

    @property
    def ip(self) -> str | None:
        return self.fields.get("ip")

    def describe(self) -> str:
        labels = {
            CodeType.MAC: "MAC-адрес",
            CodeType.SERIAL: "Серийный номер",
            CodeType.EAN: "Товарный код (EAN)",
            CodeType.IP: "IP-адрес",
            CodeType.QR: "Структурированный QR",
            CodeType.UNKNOWN: "Не распознано",
        }
        text = labels[self.code_type]
        if self.note:
            text += f" — {self.note}"
        return text


def clean_raw(raw: str | None) -> str:
    """Убирает управляющие символы, сохраняя переводы строк для QR."""
    if raw is None:
        return ""
    text = str(raw).replace("\r\n", "\n").replace("\r", "\n")
    chars = []
    for ch in text:
        if ch in ("\n", "\t") or ord(ch) >= 32:
            chars.append(ch)
    return "".join(chars).strip()


def normalize_mac(value: str | None) -> str | None:
    """Приводит MAC к виду AA:BB:CC:DD:EE:FF."""
    if not value:
        return None
    compact = re.sub(r"[\s:.\-]", "", str(value)).upper()
    if re.fullmatch(r"[0-9A-F]{12}", compact):
        return ":".join(compact[i : i + 2] for i in range(0, 12, 2))
    return None


def normalize_mac_compact(value: str | None) -> str | None:
    mac = normalize_mac(value)
    return mac.replace(":", "") if mac else None


def normalize_serial(value: str | None) -> str | None:
    """Нормализует S/N для сравнения: без пробелов, в верхнем регистре."""
    if not value:
        return None
    serial = re.sub(r"\s+", "", str(value)).strip().upper()
    return serial or None


def clean_serial(value: str | None) -> str | None:
    if not value:
        return None
    serial = str(value).strip()
    return serial or None


def is_valid_ean(value: str | None) -> bool:
    """Проверяет контрольную сумму EAN-8/12/13/14 (GTIN)."""
    if not value:
        return False
    digits = re.sub(r"[\s\-]", "", str(value))
    if not digits.isdigit() or len(digits) not in (8, 12, 13, 14):
        return False
    body = [int(c) for c in digits[:-1]]
    check = int(digits[-1])
    total = 0
    for index, digit in enumerate(reversed(body)):
        total += digit * (3 if index % 2 == 0 else 1)
    return (10 - total % 10) % 10 == check


def is_valid_ipv4(value: str | None) -> bool:
    if not value:
        return False
    try:
        ipaddress.IPv4Address(str(value).strip())
        return True
    except ValueError:
        return False


def is_valid_serial(value: str | None) -> bool:
    if not value:
        return False
    text = str(value).strip()
    if not 5 <= len(text) <= 32:
        return False
    if not re.fullmatch(r"[A-Za-z0-9._/\-+#]+", text):
        return False
    return bool(re.search(r"[A-Za-z]", text)) and bool(re.search(r"\d", text))


def _canonical_key(key: str) -> str | None:
    normalized = re.sub(r"[\s_\-./]", "", key.strip().lower())
    aliases = {
        "sn": "serial",
        "serial": "serial",
        "serialnumber": "serial",
        "serialno": "serial",
        "сн": "serial",
        "серийник": "serial",
        "серийныйномер": "serial",
        "mac": "mac",
        "macaddress": "mac",
        "macaddr": "mac",
        "мак": "mac",
        "макадрес": "mac",
        "ip": "ip",
        "ipaddress": "ip",
        "ipaddr": "ip",
        "ип": "ip",
        "ипадрес": "ip",
        "model": "model",
        "modelname": "model",
        "modelno": "model",
        "модель": "model",
        "manufacturer": "manufacturer",
        "vendor": "manufacturer",
        "brand": "manufacturer",
        "производитель": "manufacturer",
        "вендор": "manufacturer",
        "asset": "asset_tag",
        "assettag": "asset_tag",
        "inventory": "asset_tag",
        "inventorynumber": "asset_tag",
        "inv": "asset_tag",
        "инв": "asset_tag",
        "инвентарный": "asset_tag",
        "инвентарныйномер": "asset_tag",
        "hostname": "hostname",
    }
    return aliases.get(normalized)


def _normalize_field(key: str, value: Any) -> tuple[str, str] | None:
    canonical = _canonical_key(key)
    if not canonical:
        return None
    text = str(value).strip()
    if not text:
        return None
    if canonical == "mac":
        normalized = normalize_mac(text)
        return canonical, normalized or text
    if canonical == "serial":
        return canonical, clean_serial(text) or text
    if canonical == "ip":
        return canonical, text
    return canonical, text


def _flatten_dict(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in data.items():
        full_key = f"{prefix}{key}"
        if isinstance(value, dict):
            result.update(_flatten_dict(value, prefix=full_key + "."))
        else:
            result[full_key] = value
    return result


def _structured_from_json(raw: str) -> dict[str, str]:
    text = raw.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    fields: dict[str, str] = {}
    for key, value in _flatten_dict(data).items():
        # Для вложенных ключей используем последнюю часть: device.sn -> sn
        normalized = _normalize_field(key.rsplit(".", 1)[-1], value)
        if normalized:
            fields[normalized[0]] = normalized[1]
    return fields


def _structured_from_query(raw: str) -> dict[str, str]:
    text = raw.strip()
    if "=" not in text:
        return {}
    # Пары через ';' или перевод строки разбирает _structured_from_pairs.
    # Здесь — только URL-query или строка с '&'.
    if "?" in text:
        query = urlparse(text).query
    elif "&" in text:
        query = text
    else:
        return {}
    if not query:
        return {}
    fields: dict[str, str] = {}
    for key, values in parse_qs(query, keep_blank_values=False).items():
        if values:
            normalized = _normalize_field(key, values[0])
            if normalized:
                fields[normalized[0]] = normalized[1]
    return fields


_KEY_VALUE_RE = re.compile(
    r"(?:^|[;&,\n|])\s*([A-Za-zА-Яа-яЁё0-9/_\s.\-]{2,24}?)\s*[:=]\s*([^;&,\n|]+)"
)


def _structured_from_pairs(raw: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in _KEY_VALUE_RE.finditer(raw):
        key, value = match.group(1), match.group(2)
        normalized = _normalize_field(key, value)
        if normalized:
            fields[normalized[0]] = normalized[1]
    return fields


def parse_structured_fields(raw: str) -> dict[str, str]:
    """Извлекает поля из JSON, URL-query или пар ключ=значение."""
    for parser in (_structured_from_json, _structured_from_query, _structured_from_pairs):
        fields = parser(raw)
        if fields:
            return fields
    return {}


def parse_code(raw: str | None) -> ParsedCode:
    """Разбирает строку сканера и возвращает типизированный результат."""
    text = clean_raw(raw)
    if not text:
        return ParsedCode("", CodeType.UNKNOWN, note="Пустой код")

    structured = parse_structured_fields(text)
    if structured:
        return ParsedCode(
            raw=text,
            code_type=CodeType.QR,
            value=text,
            fields=structured,
            confidence=0.95,
            note=f"Полей: {len(structured)}",
        )

    mac = normalize_mac(text)
    if mac:
        has_separators = bool(re.search(r"[:.\-]", text))
        confidence = 0.95 if has_separators else 0.65
        note = "" if has_separators else "12 hex-символов: может быть и серийным номером"
        return ParsedCode(
            raw=text,
            code_type=CodeType.MAC,
            value=mac,
            fields={"mac": mac},
            confidence=confidence,
            note=note,
        )

    if is_valid_ipv4(text):
        return ParsedCode(
            raw=text,
            code_type=CodeType.IP,
            value=text,
            fields={"ip": text},
            confidence=0.9,
        )

    if is_valid_ean(text):
        digits = re.sub(r"[\s\-]", "", text)
        return ParsedCode(
            raw=text,
            code_type=CodeType.EAN,
            value=digits,
            fields={"ean": digits},
            confidence=0.95,
            note="Товарный код, не серийный номер",
        )

    if is_valid_serial(text):
        serial = clean_serial(text) or text
        return ParsedCode(
            raw=text,
            code_type=CodeType.SERIAL,
            value=serial,
            fields={"serial": serial},
            confidence=0.8,
        )

    note = "Код не распознан"
    if text.isdigit():
        note = "Только цифры: проверьте, это EAN или S/N"
    elif len(text) > 32:
        note = "Слишком длинный код для S/N"
    return ParsedCode(raw=text, code_type=CodeType.UNKNOWN, value=text, note=note)

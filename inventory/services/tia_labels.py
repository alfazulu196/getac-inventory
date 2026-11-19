# inventory/services/tia_labels.py
"""Метки TIA-606-C: чистая логика без Qt, БД и ollama.

Схема заморожена: <SITE>-<ROOM>-<RACK> для стойки и
<SITE>-<ROOM>-<RACK>-<PANEL>-<PORT> для порта. Сегменты — ASCII A-Z0-9,
верхний регистр; кириллица транслитерируется, длинные сегменты усекаются.

Модуль живёт в пакете приложения, потому что по метке со сканера нужно
находить локацию текущей сессии. Инструменты печати импортируют его же —
иначе генератор CSV и приложение разойдутся в транслите или лимитах.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from inventory.core.translit import TRANSLIT

# Лимиты сегментов: метка живёт на стикере и должна быть короткой.
SEGMENT_LIMITS = {"site": 8, "room": 12, "rack": 8, "panel": 8, "port": 4}
SEGMENT_ORDER = ("site", "room", "rack", "panel", "port")

# Префикс QR-кода на наклейке. Явный ключ отделяет метку от S/N и MAC:
# голый «A-101-SH1» парсер принял бы за серийный номер.
QR_PREFIX = "TIA="


@dataclass(frozen=True)
class LabelSpec:
    """Состав иерархической метки: 3 сегмента (стойка) или 5 (порт)."""

    site: str
    room: str
    rack: str
    panel: str = ""
    port: str = ""


def _segment(name: str, text: str) -> str:
    """Нормализует сегмент: транслит кириллицы, A-Z0-9, лимит длины."""
    limit = SEGMENT_LIMITS[name]
    buf = []
    for ch in (text or "").lower():
        if ch in TRANSLIT:
            buf.append(TRANSLIT[ch])
        elif ch.isascii() and ch.isalnum():
            buf.append(ch.upper())
    seg = "".join(buf)[:limit].upper()
    if not seg:
        raise ValueError(f"сегмент {name} пуст после нормализации: «{text}»")
    return seg


def generate(spec: LabelSpec) -> str:
    """Собирает метку из спецификации: 3 сегмента — стойка, 5 — порт."""
    parts = [_segment("site", spec.site), _segment("room", spec.room),
             _segment("rack", spec.rack)]
    if spec.panel or spec.port:
        parts += [_segment("panel", spec.panel), _segment("port", spec.port)]
    return "-".join(parts)


def validate(label: str) -> list[str]:
    """Список ошибок метки; пустой список — метка валидна.

    Регистр не важен (стикер печатается в верхнем, но сверяем без
    учёта регистра). Валидатор строгий по алфавиту: сегменты только
    A-Z 0-9, автотранслит НЕ применяется — что на стикере, то и
    проверяем.
    """
    parts = (label or "").strip().upper().split("-")
    if len(parts) not in (3, 5):
        return [f"ожидается 3 сегмента (метка стойки) или 5 (метка порта), "
                f"сейчас {len(parts)}"]
    errors: list[str] = []
    for name, part in zip(SEGMENT_ORDER, parts):
        limit = SEGMENT_LIMITS[name]
        if not part:
            errors.append(f"сегмент {name} пуст")
            continue
        if len(part) > limit:
            errors.append(
                f"сегмент {name} «{part}» длиннее {limit} символов")
            continue
        bad = "".join(ch for ch in part if not (ch.isascii() and ch.isalnum()))
        if bad:
            shown = bad if len(bad) <= 4 else bad[:4] + "…"
            errors.append(
                f"сегмент {name} «{part}»: недопустимые символы «{shown}» "
                f"(допустимы A-Z 0-9)")
    return errors


def parse(label: str) -> LabelSpec:
    """Разбирает метку в спецификацию; ошибка — ValueError."""
    errors = validate(label)
    if errors:
        raise ValueError("; ".join(errors))
    parts = (label or "").strip().upper().split("-")
    return LabelSpec(*parts)


def room_segment(building: str, floor: str, room: str) -> str:
    """Сегмент помещения — тот же, что в NetBox-локации.

    «Корпус А · 1 · 101» и «Корпус А - 1 - 101» обязаны дать один и
    тот же сегмент, иначе наклейка и поиск локации разойдутся.
    """
    parts = [building or "", floor or "", room or ""]
    name = " - ".join(p.strip() for p in parts if p.strip())
    return name or "Без помещения"


def rack_label_for_location(location: Any, site: str, *,
                            prefix: bool = False) -> str | None:
    """Метка стойки для объекта Location; None — если стойка не задана.

    site — имя объекта сессии (в CSV это колонка site), а НЕ building:
    помещение собирается из building/floor/room, как в NetBox-экспорте.
    """
    rack = (getattr(location, "rack", "") or "").strip()
    if not rack or not (site or "").strip():
        return None
    try:
        label = generate(LabelSpec(
            site=site,
            room=room_segment(
                getattr(location, "building", "") or "",
                getattr(location, "floor", "") or "",
                getattr(location, "room", "") or "",
            ),
            rack=rack,
        ))
    except ValueError:
        return None
    return f"{QR_PREFIX}{label}" if prefix else label


def qr_payload(label: str) -> str:
    """Содержимое QR на наклейке: явный префикс TIA=."""
    return f"{QR_PREFIX}{(label or '').strip().upper()}"


def match_locations(locations: Iterable[Any], label: str, site: str) -> list[Any]:
    """Локации, чья метка стойки совпадает с меткой со сканера.

    site — имя объекта сессии. Сравнение без учёта регистра; префикс
    TIA= допускается. Возвращает все совпадения: при дублях меток
    приложение предупредит, а не молча переключит локацию.
    """
    wanted = (label or "").strip().upper()
    if wanted.startswith(QR_PREFIX):
        wanted = wanted[len(QR_PREFIX):]
    if not wanted:
        return []
    return [
        loc for loc in locations
        if (rack_label_for_location(loc, site) or "").upper() == wanted
    ]


def natural_key(text: str) -> tuple:
    """Ключ сортировки, где «Ш-2» раньше «Ш-10».

    Каждая часть — (0, число) или (1, строка): иначе «1» и «A» в одном
    поле дают TypeError при сравнении int со str.
    """
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", text or "")
    )

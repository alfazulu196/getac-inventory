# tools/tia606.py
r"""TIA-606-C: генератор и валидатор иерархических меток.

Реализует общепринятую иерархическую схему ANSI/TIA-606-C (текст
стандарата платный, номера пунктов не цитирую — схема по публичным
обзорам стандарта):
    <SITE>-<ROOM>-<RACK>                    метка стойки/шкафа
    <SITE>-<ROOM>-<RACK>-<PANEL>-<PORT>     метка порта панели
Сегменты — ASCII A-Z 0-9, верхний регистр, без дефисов и пробелов
внутри сегмента (дефис — только разделитель). Кириллица
транслитерируется автоматически при генерации; валидатор строгий —
метка на стикере обязана быть ASCII (кириллица в стикере = ошибка).

Запуск:
  python -X utf8 tools\tia606.py --check "A-101-SH1-A1-P01"
  python -X utf8 tools\tia606.py --db data\inventory.db
  python -X utf8 tools\tia606.py --db data\inventory.db --out data\exports\tia606
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # noqa: E402
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))  # noqa: E402

from export_netbox import TRANSLIT, build_export, collect  # noqa: E402

# Лимиты сегментов: метка живёт на стикере и должна быть короткой.
# Длинные имена объектов усекаются при генерации (полные имена
# остаются в NetBox-экспорте и в БД).
SEGMENT_LIMITS = {"site": 8, "room": 12, "rack": 8, "panel": 8, "port": 4}
SEGMENT_ORDER = ("site", "room", "rack", "panel", "port")

LABEL_HEADERS = ["label", "level", "site", "room", "rack", "panel", "port"]


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


def rack_labels(data: dict) -> tuple[list[dict], list[str]]:
    """Метки стоек из данных collect(): строки CSV + предупреждения.

    Помещения/стойки группируются тем же кодом, что и NetBox-экспорт
    (build_export), поэтому labels.csv согласован с racks.csv.
    """
    rows: list[dict] = []
    warnings: list[str] = []
    seen: dict[str, str] = {}
    for rack in build_export(data)["tables"]["racks.csv"]:
        try:
            label = generate(LabelSpec(site=rack["site"],
                                       room=rack["location"],
                                       rack=rack["name"]))
        except ValueError as exc:
            warnings.append(f"{rack['site']} / {rack['name']}: {exc}")
            continue
        if label in seen:
            warnings.append(
                f"дубликат метки {label}: «{seen[label]}» и «{rack['name']}»")
        else:
            seen[label] = rack["name"]
        rows.append({"label": label, "level": "rack", "site": rack["site"],
                     "room": rack["location"], "rack": rack["name"],
                     "panel": "", "port": ""})
    return rows, warnings


def write_labels_csv(out_dir: Path, rows: list[dict]) -> Path:
    """Пишет labels.csv; возвращает путь."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "labels.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(LABEL_HEADERS)
        for row in rows:
            writer.writerow([row.get(col, "") for col in LABEL_HEADERS])
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="TIA-606-C метки: генератор и валидатор")
    ap.add_argument("--check", metavar="МЕТКА",
                    help="проверить метку, например A-101-SH1")
    ap.add_argument("--db", default="data/inventory.db",
                    help="база SQLite (по умолчанию data/inventory.db)")
    ap.add_argument("--out", default="data/exports/tia606",
                    help="папка вывода labels.csv (по умолчанию "
                         "data/exports/tia606)")
    args = ap.parse_args(argv)

    if args.check:
        errors = validate(args.check)
        label = args.check.strip().upper()
        if errors:
            print(f"[X] {label} — метка невалидна:")
            for e in errors:
                print(f"    - {e}")
            return 1
        print(f"[OK] {label} — валидная метка TIA-606")
        return 0

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"[X] База не найдена: {db_path}")
        return 1
    rows, warnings = rack_labels(collect(db_path))
    if not rows:
        print("[X] Стоек не найдено — генерировать нечего")
        return 1
    path = write_labels_csv(Path(args.out), rows)
    print(f"[OK] Метки TIA-606: {path} ({len(rows)} стоек)")
    for w in warnings:
        print(f"[!] {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

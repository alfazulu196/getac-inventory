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

Сами метки генерирует inventory.services.tia_labels — тот же модуль,
которым приложение ищет локацию по QR со сканера. Здесь — слой над
базой, CSV и печатью.

Запуск:
  python -X utf8 tools\tia606.py --check "A-101-SH1-A1-P01"
  python -X utf8 tools\tia606.py --db data\inventory.db
  python -X utf8 tools\tia606.py --db data\inventory.db --out data\exports\tia606
  python -X utf8 tools\tia606.py --db data\inventory.db --pdf
  python -X utf8 tools\tia606.py --db data\inventory.db --pdf --preview
  python -X utf8 tools\tia606.py --db data\inventory.db --ports data\ports.csv --pdf
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # noqa: E402
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))  # noqa: E402

from export_netbox import build_export, collect  # noqa: E402
from inventory.services.tia_labels import (  # noqa: E402
    LabelSpec,
    generate,
    natural_key,
    parse,  # noqa: F401 — публичный реэкспорт для тестов и внешних вызовов
    validate,
)

LABEL_HEADERS = ["label", "level", "site", "room", "rack", "panel", "port"]
PORT_HEADERS = ["site", "room", "rack", "panel", "port"]


def _add_row(rows: list[dict], seen: dict[str, str], row: dict,
             warnings: list[str], *, allow_duplicates: bool,
             kind: str) -> None:
    """Добавляет строку, если метка не повторяет уже напечатанную.

    Две одинаковые наклейки на разные стойки/порты хуже, чем ни одной:
    по ним нельзя однозначно найти место. Поэтому по умолчанию дубликат
    не печатается, а не просто предупреждается.
    """
    label = row["label"]
    if label in seen:
        where = f"«{seen[label]}» и «{row['rack']}»"
        if allow_duplicates:
            warnings.append(f"дубликат метки {label}: {where} (оставлен)")
        else:
            warnings.append(
                f"дубликат метки {label}: {where} — {kind} не напечатан "
                f"(--allow-duplicates, чтобы оставить)")
            return
    else:
        seen[label] = row["rack"]
    rows.append(row)


def rack_labels(data: dict, *, allow_duplicates: bool = False,
                ) -> tuple[list[dict], list[str]]:
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
        _add_row(rows, seen, {
            "label": label, "level": "rack", "site": rack["site"],
            "room": rack["location"], "rack": rack["name"],
            "panel": "", "port": "",
        }, warnings, allow_duplicates=allow_duplicates, kind="стойка")
    return rows, warnings


def read_ports_csv(path: str | Path) -> list[dict[str, str]]:
    """CSV портов: site,room,rack,panel,port (utf-8-sig — Excel BOM).

    Формат намеренно тот же, что у labels.csv: один файл можно
    подготовить в Excel и напечатать вместе со стойками. Строки без
    обязательных полей отбрасываются с предупреждением.
    """
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return [
            {key: (row.get(key) or "").strip() for key in PORT_HEADERS}
            for row in reader
        ]


def port_labels(rows: list[dict[str, str]], *,
                allow_duplicates: bool = False,
                ) -> tuple[list[dict], list[str]]:
    """Метки портов из подготовленного CSV; ошибки — предупреждения."""
    result: list[dict] = []
    warnings: list[str] = []
    seen: dict[str, str] = {}
    for index, row in enumerate(rows, 1):
        missing = [key for key in PORT_HEADERS if not row.get(key)]
        if missing:
            warnings.append(
                f"порты, строка {index}: нет полей {', '.join(missing)} — "
                f"пропущена")
            continue
        try:
            label = generate(LabelSpec(
                site=row["site"], room=row["room"], rack=row["rack"],
                panel=row["panel"], port=row["port"]))
        except ValueError as exc:
            warnings.append(f"порты, строка {index}: {exc}")
            continue
        _add_row(result, seen, {
            "label": label, "level": "port", "site": row["site"],
            "room": row["room"], "rack": row["rack"],
            "panel": row["panel"], "port": row["port"],
        }, warnings, allow_duplicates=allow_duplicates, kind="порт")
    return result, warnings


def sort_labels(rows: list[dict]) -> list[dict]:
    """Объект → помещение → стойка → панель → порт, числа по порядку.

    Порядок нужен для наклейки: листы идут подряд по помещениям, а не
    в порядке появления записей в базе.
    """
    return sorted(rows, key=lambda row: (
        natural_key(row.get("site", "")),
        natural_key(row.get("room", "")),
        natural_key(row.get("rack", "")),
        natural_key(row.get("panel", "")),
        natural_key(row.get("port", "")),
    ))


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
    ap.add_argument("--ports", default=None,
                    help="CSV портов (site,room,rack,panel,port) — "
                         "допечатать метки портов")
    ap.add_argument("--allow-duplicates", action="store_true",
                    help="печатать и одинаковые метки (по умолчанию "
                         "дубликат исключается)")
    ap.add_argument("--pdf", action="store_true",
                    help="собрать печатный лист наклеек labels.pdf "
                         "(A4, 65×35 мм)")
    ap.add_argument("--pdf-out", default=None,
                    help="путь PDF (по умолчанию рядом с labels.csv)")
    ap.add_argument("--no-qr", action="store_true",
                    help="без QR на наклейке (только текст)")
    ap.add_argument("--preview", nargs="?", const="", default=None,
                    metavar="PNG",
                    help="нарисовать первую страницу в PNG для проверки "
                         "без печати")
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
    rows, warnings = rack_labels(collect(db_path),
                                 allow_duplicates=args.allow_duplicates)
    if args.ports:
        ports_path = Path(args.ports)
        if not ports_path.is_file():
            print(f"[X] Нет файла портов: {ports_path}")
            return 1
        port_rows, port_warnings = port_labels(
            read_ports_csv(ports_path),
            allow_duplicates=args.allow_duplicates)
        rows += port_rows
        warnings += port_warnings
    rows = sort_labels(rows)
    if not rows:
        print("[X] Стоек и портов не найдено — генерировать нечего")
        return 1
    path = write_labels_csv(Path(args.out), rows)
    racks = sum(1 for row in rows if row.get("level") == "rack")
    ports = sum(1 for row in rows if row.get("level") == "port")
    print(f"[OK] Метки TIA-606: {path} "
          f"({racks} стоек, {ports} портов)")
    for w in warnings:
        print(f"[!] {w}")
    if args.pdf:
        try:
            # Импорт здесь: проверка меток и CSV не должны тянуть Qt.
            from labels_pdf import read_labels_csv, render_labels_pdf
        except ImportError as exc:
            print(f"[X] PDF-лист недоступен: {exc}")
            return 1
        pdf_path = Path(args.pdf_out) if args.pdf_out else path.with_suffix(".pdf")
        count = render_labels_pdf(read_labels_csv(path), pdf_path,
                                  with_qr=not args.no_qr)
        print(f"[OK] Лист наклеек: {pdf_path} ({count} шт.)")
    if args.preview is not None:
        try:
            from labels_pdf import render_labels_png
        except ImportError as exc:
            print(f"[X] Предпросмотр недоступен: {exc}")
            return 1
        preview_path = (Path(args.preview) if args.preview else
                        path.with_name("labels_preview.png"))
        render_labels_png(read_labels_csv(path), preview_path,
                          with_qr=not args.no_qr)
        print(f"[OK] Предпросмотр: {preview_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

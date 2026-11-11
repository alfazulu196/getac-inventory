# tools/rehearsal_check.py
# -*- coding: utf-8 -*-
"""Сухая репетиция на демо-данных: сверяем, что обещает РЕПЕТИЦИЯ.md.

Не трогает data\\inventory.db — демо строится в отдельной базе
data\\rehearsal_check.db. Запуск:
    python tools/rehearsal_check.py
Скрипт не меняет код приложения: только прогоняет описанный сценарий
(сверка → экспорт → проверка листов и цифр) и печатает отклонения.
"""
from __future__ import annotations

import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openpyxl import load_workbook  # noqa: E402

from inventory.core.baseline import parse_baseline  # noqa: E402
from inventory.core.db import Database  # noqa: E402
from inventory.core.export_excel import export_session  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402

DB_PATH = ROOT / "data" / "rehearsal_check.db"

FAILURES: list[str] = []


def ok(label: str, expected, actual) -> None:
    if expected == actual:
        print(f"  ✔ {label}: {actual}")
    else:
        FAILURES.append(f"{label}: ожидалось {expected!r}, получено {actual!r}")
        print(f"  ✘ {label}: ожидалось {expected!r}, получено {actual!r}")


def close(label: str, expected_min: int, expected_max: int, actual: int) -> None:
    if expected_min <= actual <= expected_max:
        print(f"  ✔ {label}: {actual} (радиус {expected_min}–{expected_max})")
    else:
        FAILURES.append(f"{label}: ожидалось {expected_min}–{expected_max}, получено {actual}")
        print(f"  ✘ {label}: ожидалось {expected_min}–{expected_max}, получено {actual}")


def main() -> int:
    print("== 1. Демо-база ==")
    state = subprocess.run(
        [sys.executable, "tools/make_demo.py", "--db", str(DB_PATH)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    if state.returncode != 0:
        print("make_demo упал:\n", state.stdout[-2000:], state.stderr[-2000:])
        return 1

    db = Database(DB_PATH)
    repo = InventoryRepository(db)
    session = repo.get_active_session()
    if not session:
        print("Активной сессии нет — демо не создалось")
        return 1
    devices = repo.list_devices(session.id)
    locations = repo.list_locations(session.id)
    summary = repo.get_summary(session.id)

    print(f"Сессия: {session.object_name} / {session.name}")
    close("Устройств", 25, 40, summary["total"])
    ok("Без S/N и MAC", 2, summary["no_serial_mac"])
    close("Локаций", 5, 8, len(locations))

    by_type = Counter(d.device_type for d in devices)
    print("  По типам:", dict(by_type))
    for needed in ("camera", "switch", "nvr", "ap", "ups", "patch"):
        if by_type.get(needed, 0) == 0:
            FAILURES.append(f"демо не содержит тип {needed}")
    if by_type.get("nvr", 0) < 2:
        FAILURES.append("регистраторов меньше двух (лист «Регистраторы» проверяется на одном)")

    statuses = Counter(d.status for d in devices)
    print("  По статусам:", dict(statuses))

    print("== 2. Сверка («прошлая инвентаризация») ==")
    baseline_path = DB_PATH.parent / "rehearsal" / "прошлая_инвентаризация.xlsx"
    if not baseline_path.exists():
        print(f"Нет файла прошлой таблицы: {baseline_path}")
        return 1
    rows = parse_baseline(baseline_path)
    result = repo.import_baseline(session.id, baseline_path, rows)
    print(f"  Строк в прошлой таблице: {result['total']}, с S/N или MAC: {result['usable']}")

    print("== 3. Экспорт Excel ==")
    exports_dir = DB_PATH.parent / "rehearsal_check" / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    output = exports_dir / "rehearsal_check_report.xlsx"
    export_session(repo, session.id, output)
    wb = load_workbook(output)
    print("  Листы:", wb.sheetnames)
    ok(
        "Порядок листов",
        ["Титул", "Устройства", "Связи", "Регистраторы", "Сводка", "Сверка", "Проблемы"],
        wb.sheetnames,
    )

    titul = wb["Титул"]
    titul_labels = [titul.cell(row=r, column=1).value for r in range(1, titul.max_row + 1)]
    for needed in ("Инспектор", "Сессия", "ИТОГИ", "Всего устройств"):
        if needed not in titul_labels:
            FAILURES.append(f"титуле нет строки «{needed}»")
    print("  Титул: строки", [x for x in titul_labels if x])

    nvr_ws = wb["Регистраторы"]
    print(f"  «Регистраторы»: строк данных {nvr_ws.max_row - 1}")

    compare_ws = wb["Сверка"]
    verdicts = Counter(
        str(compare_ws.cell(row=r, column=1).value or "").strip()
        for r in range(2, compare_ws.max_row + 1)
    )
    print("  Вердикты «Сверки»:", dict(verdicts))
    close("Совпадает", 17, 20, verdicts.get("Совпадает", 0))
    ok("Изменился", 4, verdicts.get("Изменился", 0))
    close("Новое", 5, 12, verdicts.get("Новое", 0))
    ok("Пропало", 5, verdicts.get("Пропало", 0))
    ok("Не сверено", 2, verdicts.get("Не сверяется", verdicts.get("Не сверено", 0)))

    titul_counts = {
        str(titul.cell(row=r, column=1).value or ""): titul.cell(row=r, column=2).value
        for r in range(1, titul.max_row + 1)
    }
    ok("Титул: сверка-совпадает = листу", verdicts.get("Совпадает", 0),
       titul_counts.get("Сверка: совпадает"))

    print("== 4. Состояние данных ==")
    from inventory.core.duplicates import find_duplicate_groups
    groups = find_duplicate_groups(repo, session.id)
    print(f"  Групп дубликатов в демо: {len(groups)}")

    db.close()
    print()
    if FAILURES:
        print("ОТКЛОНЕНИЯ ОТ ОЖИДАНИЙ РЕПЕТИЦИЯ.md:")
        for failure in FAILURES:
            print("  -", failure)
        return 1
    print("Сухая репетиция целиком сошлась с описанием в РЕПЕТИЦИЯ.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

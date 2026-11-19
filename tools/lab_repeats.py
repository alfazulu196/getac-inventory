# tools/lab_repeats.py
r"""Сводка повторов prompt_lab: разброс по кадрам, без ручных скриптов.

Читает артефакты lab_results*.json, берёт строки с полями full/partial/
missed и показывает по каждому кадру: сколько прогонов, среднюю полноту
и диапазон min–max. Нужен, чтобы отличать свойство промпта от шума
модели: кадр, который повторяется 2/4/2 каждый раз, — не случайность.

Запуск:
  python -X utf8 tools\lab_repeats.py
  python -X utf8 tools\lab_repeats.py tools\assets\bench\lab_results_combo_7b.json
  python -X utf8 tools\lab_repeats.py --variant combo --json data\exports\lab.json
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "tools" / "assets" / "bench"
DEFAULT_GLOB = str(BENCH / "lab_results_*_7b*.json")


def completeness(full: int, partial: int, missed: int) -> float:
    return 100.0 * (full + 0.5 * partial) / max(full + partial + missed, 1)


def load_runs(paths: list[str | Path]) -> list[dict]:
    """Все удачные прогоны из файлов; сбойные строки пропускаются."""
    runs: list[dict] = []
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for run in data.get("runs", []):
            if "full" not in run:
                continue
            row = dict(run)
            row["source"] = Path(path).name
            runs.append(row)
    return runs


def summarize(runs: list[dict]) -> list[dict]:
    """variant/frame → n, средняя полнота, min–max, сумма счётчиков."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for run in runs:
        groups.setdefault((run["variant"], run["frame"]), []).append(run)
    result: list[dict] = []
    for (variant, frame), items in sorted(groups.items()):
        values = [
            completeness(r["full"], r["partial"], r["missed"])
            for r in items
        ]
        result.append({
            "variant": variant,
            "frame": frame,
            "n": len(items),
            "mean": round(sum(values) / len(values), 1),
            "min": round(min(values), 1),
            "max": round(max(values), 1),
            "full": sum(r["full"] for r in items),
            "partial": sum(r["partial"] for r in items),
            "missed": sum(r["missed"] for r in items),
            "extra": sum(r.get("extra", 0) for r in items),
        })
    return result


def print_table(rows: list[dict]) -> None:
    print(f"{'вариант':<12}{'кадр':<15}{'n':>3}{'средняя':>9}"
          f"{'min–max':>12}{'точно':>7}{'част':>6}{'проп':>6}{'лишн':>6}")
    for row in rows:
        print(f"{row['variant']:<12}{row['frame']:<15}{row['n']:>3}"
              f"{row['mean']:>8.0f}%{row['min']:>6.0f}–{row['max']:<5.0f}"
              f"{row['full']:>7}{row['partial']:>6}{row['missed']:>6}"
              f"{row['extra']:>6}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Сводка повторов prompt_lab")
    ap.add_argument("paths", nargs="*",
                    help="файлы lab_results*.json (по умолчанию — "
                         "артефакты *_7b*.json)")
    ap.add_argument("--variant", default=None,
                    help="только этот вариант (combo, ranges, ...)")
    ap.add_argument("--frame", default=None,
                    help="только этот кадр (rack_dark, ...)")
    ap.add_argument("--json", default=None,
                    help="сохранить сводку в JSON")
    args = ap.parse_args(argv)

    paths = args.paths or sorted(glob.glob(DEFAULT_GLOB))
    if not paths:
        print("[X] Нет файлов lab_results*.json — сначала prompt_lab")
        return 1
    missing = [str(path) for path in paths if not Path(path).is_file()]
    if missing:
        print(f"[X] Нет файла: {missing[0]}")
        return 1
    runs = load_runs(paths)
    if args.variant:
        runs = [r for r in runs if r["variant"] == args.variant]
    if args.frame:
        runs = [r for r in runs if r["frame"] == args.frame]
    if not runs:
        print("[X] В файлах нет удачных прогонов по фильтру")
        return 1
    rows = summarize(runs)
    print_table(rows)
    files = len({r["source"] for r in runs})
    print(f"\nПрогонов: {len(runs)} из {files} файлов")
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        print(f"Сводка: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# tools/prompt_lab.py
"""A/B-стенд промпта распознавания стойки — без касания замороженного кода.

Подменяет rack_ai._PROMPT в памяти процесса (файл inventory/services/rack_ai.py
остаётся нетронутым), прогоняет кадры бенчмарка полным конвейером приложения
(все деградации: пересжатие, отказ format, перебор моделей) и оценивает
результат счётчиком debrief_report.score_frame против эталона.

База для сравнения берётся из журнала data/logs/rack_ai_log.jsonl — те же
кадры, снятые производственным промптом, считаются тем же счётчиком.

Запуск:
  python -X utf8 tools/prompt_lab.py                 # все варианты
  python -X utf8 tools/prompt_lab.py --variant combo --frame rack_glare
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # noqa: E402  (пакет inventory)
sys.path.insert(0, str(ROOT / "tools"))  # noqa: E402  (соседние скрипты)

import inventory.services.rack_ai as rai  # noqa: E402
import debrief_report as dr  # noqa: E402

ASSETS = ROOT / "tools" / "assets"
BENCH = ASSETS / "bench"
EXPECTED_PATH = BENCH / "expected.json"
JOURNAL = ROOT / "data" / "logs" / "rack_ai_log.jsonl"

# Кадры бенчмарка: имя → путь (mock лежит рядом, остальные в bench/).
FRAMES: dict[str, Path] = {
    "rack_mock": ASSETS / "rack_mock.jpg",
    "rack_dark": BENCH / "rack_dark.jpg",
    "rack_glare": BENCH / "rack_glare.jpg",
    "rack_partial": BENCH / "rack_partial.jpg",
    "rack_dense15": BENCH / "rack_dense15.jpg",
}

# Добавки — те же константы, что в приложении (PROMPT_ADDITIONS_V163):
# расхождение лабы и приложения исключено по построению.
_BASE_PROMPT_V162 = rai._PROMPT_V162  # база v1.6.2 — источник правды: приложение
_ADD_ANTI_REPEAT, _ADD_RANGES, _ADD_TYPES = rai.PROMPT_ADDITIONS_V163

VARIANTS: dict[str, str] = {
    "anti_repeat": _BASE_PROMPT_V162 + _ADD_ANTI_REPEAT,
    "ranges": _BASE_PROMPT_V162 + _ADD_RANGES,
    "types": _BASE_PROMPT_V162 + _ADD_TYPES,
    "combo": _BASE_PROMPT_V162 + "".join(rai.PROMPT_ADDITIONS_V163),
}


def devices_to_dicts(devices: list[rai.RackDevice]) -> list[dict]:
    """RackDevice → формат счётчика debrief_report."""
    return [
        {"type": d.type_code, "brand": d.brand, "model": d.model,
         "position": d.position, "detail": d.detail}
        for d in devices
    ]


def score(actual: list[dict], expected: list[dict]) -> tuple[int, int, int, int]:
    """(точно, частично, пропущено, лишних) — для таблицы и полноты."""
    full, partial, missed, notes = dr.score_frame(actual, expected)
    extra = sum(1 for n in notes if "лишний" in n)
    return full, partial, missed, extra


def baseline_from_journal() -> dict[str, tuple[int, int, int, int, int]]:
    """База: последние записи журнала по кадрам бенчмарка, тот же счётчик."""
    entries = dr.load_entries(JOURNAL)
    latest: dict[str, dict] = {}
    for e in entries:  # журнал append-only → последнее вхождение и есть база
        if e.get("photo") in {f"{name}.jpg" for name in FRAMES} and e.get("ok"):
            latest[e["photo"]] = e
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    result = {}
    for photo, e in latest.items():
        rows = score(e.get("devices", []), expected[photo])
        result[photo] = (rows[0], rows[1], rows[2], rows[3],
                         round(e.get("elapsed_s", 0)))
    return result


def run_variant(name: str, frames: list[str]) -> list[dict]:
    """Прогон варианта по кадрам; результаты — строки отчёта."""
    rai._PROMPT = VARIANTS[name]  # подмена в памяти: файл не меняется
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    rows = []
    for frame in frames:
        path = FRAMES[frame]
        t0 = time.perf_counter()
        try:
            analysis = rai.analyse_rack_photo(path)  # без журнала поля
        except Exception as exc:  # noqa: BLE001 — сбои лабы не роняют прогон
            rows.append({"variant": name, "frame": frame, "error": str(exc),
                         "elapsed_s": round(time.perf_counter() - t0)})
            print(f"  {frame}: СБОЙ {exc}", flush=True)
            continue
        elapsed = round(analysis.elapsed_s or (time.perf_counter() - t0))
        if not analysis.ok:
            rows.append({"variant": name, "frame": frame,
                         "error": analysis.error or "?", "elapsed_s": elapsed})
            print(f"  {frame}: ОШИБКА {analysis.error}", flush=True)
            continue
        actual = devices_to_dicts(analysis.devices)
        full, partial, missed, extra = score(actual, expected[f"{frame}.jpg"])
        rows.append({"variant": name, "frame": frame, "full": full,
                     "partial": partial, "missed": missed, "extra": extra,
                     "n_rows": len(actual), "elapsed_s": elapsed})
        print(f"  {frame}: точно {full}, частично {partial}, "
              f"проп {missed}, лишних {extra}, строк {len(actual)}, "
              f"{elapsed} с", flush=True)
    return rows


def completeness(full: int, partial: int, missed: int) -> float:
    return 100.0 * (full + 0.5 * partial) / max(full + partial + missed, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=(*VARIANTS, "all"), default="all")
    parser.add_argument("--frame", choices=(*FRAMES, "all"), default="all")
    args = parser.parse_args()
    frames = list(FRAMES) if args.frame == "all" else [args.frame]
    variants = list(VARIANTS) if args.variant == "all" else [args.variant]

    base = baseline_from_journal()
    b_full = sum(v[0] for v in base.values())
    b_part = sum(v[1] for v in base.values())
    b_miss = sum(v[2] for v in base.values())
    print(f"БАЗА (журнал, {len(base)} кадров): точно {b_full}, "
          f"частично {b_part}, проп {b_miss} → "
          f"полнота {completeness(b_full, b_part, b_miss):.0f}%\n",
          flush=True)

    all_rows: list[dict] = []
    for name in variants:
        print(f"ВАРИАНТ {name}:", flush=True)
        all_rows.extend(run_variant(name, frames))

    # Сводка по вариантам против базы.
    print("\n=== СВОДКА ===")
    print(f"{'вариант':<12}{'точно':>7}{'част':>6}{'проп':>6}{'лишн':>6}"
          f"{'время':>8}{'полнота':>9}")
    print(f"{'база':<12}{b_full:>7}{b_part:>6}{b_miss:>6}"
          f"{'':>6}{'':>8}{completeness(b_full, b_part, b_miss):>8.0f}%")
    for name in variants:
        rows = [r for r in all_rows if r["variant"] == name and "full" in r]
        f = sum(r["full"] for r in rows)
        p = sum(r["partial"] for r in rows)
        m = sum(r["missed"] for r in rows)
        x = sum(r["extra"] for r in rows)
        t = sum(r["elapsed_s"] for r in rows)
        print(f"{name:<12}{f:>7}{p:>6}{m:>6}{x:>6}{t:>7}с"
              f"{completeness(f, p, m):>8.0f}%")

    report = {"baseline": {k: list(v) for k, v in base.items()},
              "runs": all_rows}
    out = BENCH / "lab_results.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"\nОтчёт: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

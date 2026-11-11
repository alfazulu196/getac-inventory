# tools/debrief_report.py
"""Разбор журнала распознавания стойки (rack_ai_log.jsonl).

Печатает: сводку по всем кадрам (время, модель, ошибки), список устройств
каждого кадра и итоги. Режим --expected сверяет кадры бенчмарка
(tools/assets/bench/) с эталоном — объективная оценка для A/B-тестов
промпта: что модель нашла/частично/пропустила против правды.

Запуск:
  python -X utf8 tools/debrief_report.py
  python -X utf8 tools/debrief_report.py data/logs/rack_ai_log.jsonl
  python -X utf8 tools/debrief_report.py --expected tools/assets/bench/expected.json
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = ROOT / "data" / "logs" / "rack_ai_log.jsonl"


def load_entries(path: Path) -> list[dict]:
    """Читает jsonl; повреждённые строки не роняют отчёт."""
    if not path.exists():
        print(f"Журнал не найден: {path}")
        print("Разборы стойки ещё не выполнялись (или журнал ещё не скопирован).")
        return []
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def expand_positions(text: str) -> set[int]:
    """'U9-U10' → {9, 10}; 'U3' → {3}; пусто/мусор → пустое множество."""
    result: set[int] = set()
    clean = (text or "").upper().replace("Ю", "U")
    for lo, hi in re.findall(r"U?\s*(\d+)\s*[-–]\s*U?\s*(\d+)", clean):
        result.update(range(int(lo), int(hi) + 1))
    for n in re.findall(r"U?\s*(\d+)", clean):
        result.add(int(n))
    return result


def _model_close(expected: str, actual: str) -> bool:
    """Совпадение модели: полное либо нетривиальный префикс (≥4 симв.).

    «DES-1210-10/ME» против прочитанного «DES-1210» — попадание (модель
    обрезана при чтении); «TL-SG1016» и «TL-SG1024» — разные модели,
    различаются честно; пустая модель с одной из сторон не противоречит.
    """
    norm = lambda s: re.sub(r"[\s._/\\+-]+", "", (s or "").upper())  # noqa: E731
    a, b = norm(expected), norm(actual)
    if not a or not b:
        return True
    if a == b:
        return True
    return (a.startswith(b) or b.startswith(a)) and min(len(a), len(b)) >= 4


def _center(units: set[int]) -> float:
    """Центр диапазона юнитов: {2,3} → 2.5."""
    return (min(units) + max(units)) / 2 if units else 0.0


def score_frame(actual: list[dict], expected: list[dict]) -> tuple[int, int, int, list[str]]:
    """Сверка кадра с эталоном → (точно, частично, пропущено, примечания).

    Правила: бренд обязателен, если он задан в эталоне; бренд-заглушка («»)
    сверяется только внутри своего типа. Позиция: пересечение диапазонов —
    точное попадание, соседний ряд (центры ≤ 1.5 U) — «частично (позиция)».
    Среди кандидатов предпочитается полное совпадение модели и типа.
    """
    used: set[int] = set()
    full = partial = missed = 0
    notes: list[str] = []
    for exp in expected:
        exp_pos = expand_positions(exp["position"])
        exp_brand = (exp.get("brand") or "").casefold()
        exp_type = exp.get("type", "")
        best: tuple[int, int, int] | None = None  # (q, несовпадения, i)
        for i, act in enumerate(actual):
            if i in used:
                continue
            act_type = act.get("type", "")
            if exp_brand:
                if exp_brand != (act.get("brand") or "").casefold():
                    continue
            elif exp_type != act_type:
                continue  # бренд в эталоне не задан — совпадаем по типу
            act_pos = expand_positions(act.get("position", ""))
            if exp_pos and act_pos:
                if exp_pos & act_pos:
                    q = 0
                elif abs(_center(exp_pos) - _center(act_pos)) <= 1.5:
                    q = 1
                else:
                    continue
            else:
                q = 2  # позиция не читаема — не штрафуем
            model_ok = _model_close(exp.get("model", ""), act.get("model") or "")
            type_ok = exp_type == act_type
            cand = (q, 0 if (model_ok and type_ok) else 1, i)
            if best is None or cand < best:
                best = cand
        label = f"{exp_brand or 'без бренда'} {exp['model']} [{exp['position']}]"
        if best is None:
            missed += 1
            notes.append(f"  ✗ ПРОПУЩЕН: {label}")
            continue
        used.add(best[2])
        act = actual[best[2]]
        model_ok = _model_close(exp.get("model", ""), act.get("model") or "")
        type_ok = exp_type == act.get("type", "")
        if best[0] == 0 and model_ok and type_ok:
            full += 1
        else:
            partial += 1
            why = []
            if best[0] != 0:
                why.append("позиция")
            if not model_ok:
                why.append("модель")
            if not type_ok:
                why.append("тип")
            notes.append(f"  ≈ частично ({', '.join(why)}): {label}")
    for i, act in enumerate(actual):
        if i not in used:
            who = act.get("brand") or act.get("detail") or "?"
            notes.append(f"  ? лишний: {who} [{act.get('position', '')}]")
    return full, partial, missed, notes


# ----------------------------------------------------------------------------
def print_frames(entries: list[dict]) -> None:
    print("\n=== По кадрам (в хронологическом порядке) ===")
    for i, e in enumerate(entries, 1):
        status = "OK " if e.get("ok") else "ОШИБКА"
        elapsed = f"{e.get('elapsed_s', 0):.0f}с"
        print(f"#{i:>3} {e.get('ts', '?')} | {e.get('photo', '?')} | {status} | "
              f"{elapsed:>6} | юнитов: {e.get('n_devices', 0)} | {e.get('model', '?')}")
        if not e.get("ok"):
            print(f"      причина: {e.get('error')}")
        for d in e.get("devices", []):
            detail = (d.get("detail") or "")[:40]
            print(f"      · {d.get('type', '?'):>10} | {d.get('brand', ''):<12} "
                  f"{d.get('model', ''):<22} [{d.get('position', '')}] {detail}")


def print_summary(entries: list[dict]) -> None:
    ok_frames = [e for e in entries if e.get("ok")]
    times = [e.get("elapsed_s", 0) for e in ok_frames if e.get("elapsed_s")]
    units = sum(e.get("n_devices", 0) for e in entries)
    models: dict[str, int] = {}
    for e in entries:
        models[e.get("model", "?")] = models.get(e.get("model", "?"), 0) + 1
    errors: dict[str, int] = {}
    for e in entries:
        if not e.get("ok") and e.get("error"):
            key = e["error"][:60]
            errors[key] = errors.get(key, 0) + 1

    print("=== Сводка ===")
    print(f"Кадров: {len(entries)}, успешных: {len(ok_frames)} "
          f"({100 * len(ok_frames) / len(entries):.0f}%)")
    if times:
        print(f"Время кадра: медиана {statistics.median(times):.0f} с, "
              f"мин {min(times):.0f} с, макс {max(times):.0f} с")
    print(f"Всего распознано юнитов: {units}")
    print(f"Модели: {models}")
    if errors:
        print("Ошибки:")
        for err, cnt in errors.items():
            print(f"  {cnt}x {err}")


def print_bench(entries: list[dict], expected_path: Path) -> None:
    """Сверка кадров бенчмарка с эталоном expected.json."""
    expected_map = json.loads(expected_path.read_text(encoding="utf-8"))
    print(f"\n=== Сверка с эталоном ({expected_path.name}) ===")
    tot_full = tot_partial = tot_missed = 0
    for e in entries:
        name = e.get("photo", "")
        if name not in expected_map or not e.get("ok"):
            continue
        full, partial, missed, notes = score_frame(
            e.get("devices", []), expected_map[name]
        )
        tot_full, tot_partial, tot_missed = tot_full + full, tot_partial + partial, tot_missed + missed
        total = len(expected_map[name])
        print(f"{name}: точно {full}/{total}, частично {partial}, "
              f"пропущено {missed}")
        for note in notes:
            print(note)
    overall = tot_full + tot_partial
    if overall:
        print(f"\nИтого по бенчмарку: точно {tot_full}, частично {tot_partial}, "
              f"пропущено {tot_missed} → полнота "
              f"{100 * (tot_full + 0.5 * tot_partial) / (tot_full + tot_partial + tot_missed):.0f}%")


def main() -> int:
    args = sys.argv[1:]
    expected_path: Path | None = None
    if "--expected" in args:
        i = args.index("--expected")
        expected_path = Path(args[i + 1])
        del args[i:i + 2]
    log_path = Path(args[0]) if args else DEFAULT_LOG

    entries = load_entries(log_path)
    if not entries:
        return 1
    print_summary(entries)
    print_frames(entries)
    if expected_path:
        print_bench(entries, expected_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

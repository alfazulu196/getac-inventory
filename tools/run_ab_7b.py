# tools/run_ab_7b.py
r"""Пер-вариантный A/B промпта на qwen2.5vl:7b — детached-драйвер.

Прогоняет каждый вариант лабы по всем 5 кадрам бенчмарка на 7b и
сохраняет результаты после каждого прогона: prompt_lab перезаписывает
lab_results.json на выходе, поэтому без копий поздние прогоны затёрли
бы ранние. Последним идёт retry кадра rack_dense15 для combo — в
вердиктном прогоне ollama не ответила за отведённое время.

Запуск (отсоединённо — переживает рестарт сервера):
  Start-Process cmd.exe '/c',
  'python -X utf8 tools\run_ab_7b.py > "%TEMP%\opencode\ab_7b_out.txt" 2>&1'
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "tools" / "assets" / "bench"
MODEL = "qwen2.5vl:7b"

# (вариант, кадр или None = все кадры)
JOBS: list[tuple[str, str | None]] = [
    ("anti_repeat", None),
    ("ranges", None),
    ("types", None),
    ("combo", "rack_dense15"),  # retry TimeoutError вердиктного прогона
]


def main() -> int:
    failures = 0
    for variant, frame in JOBS:
        cmd = [sys.executable, "-X", "utf8",
               str(ROOT / "tools" / "prompt_lab.py"),
               "--variant", variant, "--model", MODEL]
        if frame:
            cmd += ["--frame", frame]
        title = f"{variant} / {frame or 'все кадры'}"
        print(f"=== {title} ===", flush=True)
        code = subprocess.run(cmd, cwd=str(ROOT)).returncode
        if code != 0:
            failures += 1
            print(f"[!] {title}: код выхода {code}", flush=True)
        suffix = f"{variant}_{frame}" if frame else variant
        dest = BENCH / f"lab_results_{suffix}_7b.json"
        shutil.copyfile(BENCH / "lab_results.json", dest)
        print(f"сохранено: {dest.name}", flush=True)
    print(f"Готово, сбоев: {failures}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

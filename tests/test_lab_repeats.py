# tests/test_lab_repeats.py
"""Сводка повторов prompt_lab: разброс по кадрам и JSON-выгрузка."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))  # noqa: E402

from lab_repeats import (  # noqa: E402
    completeness,
    load_runs,
    main,
    summarize,
)


def _artifact(tmp_path: Path, name: str, runs: list[dict]) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"runs": runs}, ensure_ascii=False),
                    encoding="utf-8")
    return path


def test_completeness_counts_partial_as_half():
    assert completeness(2, 2, 0) == 75.0
    assert completeness(0, 0, 0) == 0.0


def test_load_runs_skips_failures(tmp_path: Path):
    path = _artifact(tmp_path, "a.json", [
        {"variant": "combo", "frame": "rack_mock", "full": 2,
         "partial": 4, "missed": 2, "extra": 0},
        {"variant": "combo", "frame": "rack_dark", "error": "сбой"},
    ])
    runs = load_runs([path])
    assert len(runs) == 1
    assert runs[0]["source"] == "a.json"


def test_summarize_spread(tmp_path: Path):
    runs = load_runs([
        _artifact(tmp_path, "a.json", [
            {"variant": "combo", "frame": "rack_mock", "full": 2,
             "partial": 4, "missed": 2, "extra": 0},
        ]),
        _artifact(tmp_path, "b.json", [
            {"variant": "combo", "frame": "rack_mock", "full": 3,
             "partial": 3, "missed": 2, "extra": 0},
        ]),
    ])
    row = summarize(runs)[0]
    assert row["n"] == 2
    assert row["mean"] == 53.1  # 50% и 56.25% → среднее 53.1
    assert row["min"] == 50.0 and row["max"] == 56.2
    assert (row["full"], row["partial"], row["missed"]) == (5, 7, 4)


def test_main_writes_json(tmp_path: Path):
    path = _artifact(tmp_path, "a.json", [
        {"variant": "ranges", "frame": "rack_dark", "full": 5,
         "partial": 2, "missed": 1, "extra": 0},
    ])
    out = tmp_path / "summary.json"
    assert main([str(path), "--json", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data[0]["variant"] == "ranges"
    assert data[0]["n"] == 1


def test_main_no_files(tmp_path: Path):
    assert main([str(tmp_path / "none.json")]) == 1

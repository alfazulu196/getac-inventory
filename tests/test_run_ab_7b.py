# tests/test_run_ab_7b.py
"""Драйвер A/B 7b: прогон из одних ошибок не выдаётся за артефакт."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))  # noqa: E402

from run_ab_7b import all_failed  # noqa: E402


def _report(tmp_path: Path, runs: list[dict]) -> Path:
    path = tmp_path / "lab_results.json"
    path.write_text(json.dumps({"baseline": {}, "runs": runs}),
                    encoding="utf-8")
    return path


def test_all_failed(tmp_path):
    assert all_failed(_report(tmp_path, [
        {"frame": "rack_mock", "error": "TimeoutError"},
        {"frame": "rack_dark", "error": "TimeoutError"},
    ]))


def test_partial_success_is_not_all_failed(tmp_path):
    assert not all_failed(_report(tmp_path, [
        {"frame": "rack_mock", "error": "TimeoutError"},
        {"frame": "rack_dark", "full": 3, "partial": 4, "missed": 1},
    ]))


def test_empty_or_missing_is_failure(tmp_path):
    assert all_failed(_report(tmp_path, []))
    assert all_failed(tmp_path / "нет-файла.json")

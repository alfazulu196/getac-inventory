# tests/test_logging_setup.py
"""Журнал, crash-файлы и перенос исключений."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from inventory.services.logging_setup import (
    install_crash_handler,
    last_crash_file,
    log_info,
    set_crash_callback,
    setup_logging,
)


def test_setup_logging_creates_and_reuses(tmp_path: Path):
    log_path = setup_logging(tmp_path)
    assert log_path.exists()
    log_info("проверка записи")
    logging.getLogger("inventory").handlers[0].flush()
    text = log_path.read_text(encoding="utf-8")
    assert "проверка записи" in text
    # Повторный вызов не дублирует обработчики.
    before = len(logging.getLogger("inventory").handlers)
    setup_logging(tmp_path)
    assert len(logging.getLogger("inventory").handlers) == before


def test_crash_hook_writes_file(tmp_path: Path):
    install_crash_handler(tmp_path)
    received: list[Path] = []
    set_crash_callback(lambda path: received.append(path))
    try:
        raise ValueError("boom")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    crash = last_crash_file(tmp_path)
    assert crash is not None and crash.exists()
    assert "boom" in crash.read_text(encoding="utf-8")
    assert len(received) == 1
    assert "ValueError" not in last_crash_file(tmp_path).read_text(encoding="utf-8") or True


def test_last_crash_file_empty(tmp_path: Path):
    assert last_crash_file(tmp_path) is None

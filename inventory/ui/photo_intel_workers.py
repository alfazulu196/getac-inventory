# inventory/ui/photo_intel_workers.py
"""Фоновые задачи разбора фото этикетки, чтобы не блокировать интерфейс."""
from __future__ import annotations

from functools import partial
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

from inventory.services.photo_intel import PhotoIntel, analyse_photo

# Воркеры, «выпущенные на волю» при закрытии диалога: дорабатывают и удаляются
# сами. Ссылки держим здесь, чтобы питон не собрал работающий QThread.
_ORPHANS: list[Any] = []


def detach_running(worker: QThread | None) -> None:
    """Аккуратно отпустить воркер: отключить колбэки и поставить на удаление.

    Вызывать при закрытии диалога или замене воркера, пока тот ещё работает.
    """
    if worker is None:
        return
    try:
        worker.analysed.disconnect()
    except TypeError:
        pass
    if worker.isRunning():
        _ORPHANS.append(worker)
        worker.finished.connect(partial(_ORPHANS.remove, worker))
        worker.finished.connect(worker.deleteLater)
        worker.setParent(None)
    else:
        worker.deleteLater()


class PhotoIntelWorker(QThread):
    """Разбор фото этикетки в фоне (конвейер занимает 2–5 секунд)."""

    analysed = pyqtSignal(object, str)  # PhotoIntel, путь к фото

    def __init__(self, photo_path: str, parent=None):
        super().__init__(parent)
        self.photo_path = photo_path

    def run(self) -> None:
        result: PhotoIntel
        try:
            result = analyse_photo(self.photo_path)
        except Exception as exc:  # noqa: BLE001 — конвейер обычно сам гасит ошибки
            result = PhotoIntel(ok=False, error=str(exc))
        self.analysed.emit(result, self.photo_path)

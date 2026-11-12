# inventory/ui/rack_workers.py
"""Фоновые задачи распознавания стойки, чтобы не блокировать интерфейс."""
from __future__ import annotations

from functools import partial
import time
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

# «Выпущенные» воркеры дорабатывают и удаляются сами (см. photo_intel_workers).
_ORPHANS: list[Any] = []


def detach_running(worker: QThread | None) -> None:
    """Отстегнуть сигналы воркера и выпустить его на доработку."""
    if worker is None:
        return
    for signal_name in ("analysed", "progress", "warmed"):
        signal = getattr(worker, signal_name, None)
        if signal is None:
            continue
        try:
            signal.disconnect()
        except TypeError:
            pass
    if worker.isRunning():
        _ORPHANS.append(worker)
        worker.finished.connect(partial(_ORPHANS.remove, worker))
        worker.finished.connect(worker.deleteLater)
        worker.setParent(None)
    else:
        worker.deleteLater()


class RackAiWorker(QThread):
    """Разбор фото стойки в фоне (CPU-инференс, занимает до 2–3 минут).

    Стрим (v1.6.6): по мере генерации шлёт progress («символов, записей»),
    а cancel() прерывает генерацию на ближайшем чанке — не через минуты.
    """

    analysed = pyqtSignal(object, str)  # RackAnalysis, путь к фото
    progress = pyqtSignal(str)          # живой статус генерации

    def __init__(
        self,
        photo_path: str,
        parent=None,
        log_dir: str | Path | None = None,
    ):
        super().__init__(parent)
        self.photo_path = photo_path
        self.log_dir = log_dir
        self._cancel = False

    def cancel(self) -> None:
        """Просить обрыв: сработает на ближайшем чанке стрима (сотни мс)."""
        self._cancel = True

    def run(self) -> None:
        from inventory.services.rack_ai import RackAnalysis, analyse_rack_photo

        started = time.monotonic()

        def on_progress(n_chars: int, n_records: int) -> None:
            if self._cancel:
                return  # статус уже не важен, обрыв на следующем чанке
            tail = f" · записей: {n_records}" if n_records else ""
            self.progress.emit(
                f"🖥 Ищу юниты на фото… · {time.monotonic() - started:.0f} с "
                f"· {n_chars} символов{tail}"
            )

        try:
            result = analyse_rack_photo(
                self.photo_path,
                log_dir=self.log_dir,
                cancel_check=lambda: self._cancel,
                on_progress=on_progress,
            )
        except Exception as exc:  # noqa: BLE001 — сервис обычно ловит сам
            result = RackAnalysis(ok=False, error=str(exc))
        self.analysed.emit(result, self.photo_path)


class WarmupWorker(QThread):
    """Тихий запрос при открытии диалога: модель загружается в память заранее.

    Так первый кадр не «висит» полминуты с загрузкой 3.2 ГБ модели с диска.
    """

    warmed = pyqtSignal(str, str, bool)  # модель, ошибка ('' = ок), была холодной

    def __init__(self, model: str, parent=None):
        super().__init__(parent)
        self.model = model

    def run(self) -> None:
        from inventory.services.rack_ai import loaded_models, warm_up_model

        cold = self.model not in loaded_models()
        error = warm_up_model(self.model)
        self.warmed.emit(self.model, error, cold)

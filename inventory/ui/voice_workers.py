# inventory/ui/voice_workers.py
"""Фоновые задачи голосового ввода, чтобы не блокировать интерфейс."""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from inventory.services.voice import VoiceService


class VoiceLoadWorker(QThread):
    loaded = pyqtSignal(bool, str)

    def __init__(self, service: VoiceService, parent=None):
        super().__init__(parent)
        self.service = service

    def run(self) -> None:
        ok = self.service.load()
        self.loaded.emit(ok, self.service.status)


class VoiceRecognizeWorker(QThread):
    recognized = pyqtSignal(str, str)  # текст, статус

    def __init__(self, service: VoiceService, parent=None):
        super().__init__(parent)
        self.service = service

    def run(self) -> None:
        text = self.service.stop_and_recognize()
        self.recognized.emit(text, self.service.status)

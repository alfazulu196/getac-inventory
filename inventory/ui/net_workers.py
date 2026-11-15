# inventory/ui/net_workers.py
"""Фоновая сетевая автопроверка (v1.6.8): ping до ~1,2 с — не в GUI-потоке.

Отвязка воркера — общий `detach_running` из photo_intel_workers
(отсоединяет все известные сигналы, включая `checked`).
"""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from inventory.services.net_check import check_device


class NetCheckWorker(QThread):
    checked = pyqtSignal(object)  # NetCheckResult

    def __init__(self, ip: str, mac: str, parent=None):
        super().__init__(parent)
        self.ip = ip
        self.mac = mac

    def run(self) -> None:
        self.checked.emit(check_device(self.ip, self.mac))

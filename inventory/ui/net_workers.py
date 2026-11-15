# inventory/ui/net_workers.py
"""Фоновая сетевая автопроверка (v1.6.8): ping до ~1,2 с — не в GUI-потоке.

Отвязка воркеров — общий `detach_running` из photo_intel_workers
(отсоединяет все известные сигналы, включая `checked` и `progress`).
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


class NetAuditWorker(QThread):
    """Ревизия сети сессии (v1.6.9): все устройства пачкой.

    items — (device_id, display_id, model, ip, mac); для каждого
    дергается check_device (ping + ARP), результаты — по одному
    сигналом checked(dict), прогресс — progress(готово, всего, подпись).
    """

    progress = pyqtSignal(int, int, str)
    checked = pyqtSignal(object)

    def __init__(self, items: list[tuple[str, str, str, str, str]], parent=None):
        super().__init__(parent)
        self.items = items

    def run(self) -> None:
        total = len(self.items)
        for index, (device_id, display_id, model, ip, mac) in enumerate(
            self.items, 1
        ):
            result = check_device(ip, mac)
            self.checked.emit({
                "device_id": device_id,
                "display_id": display_id,
                "model": model,
                "ip": ip,
                "result": result,
            })
            self.progress.emit(index, total, display_id)

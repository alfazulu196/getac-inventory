# inventory/services/preflight.py
"""Предстартовая проверка готовности Getac к обходу."""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from inventory.config import AppConfig, VOSK_MODEL_DIR, resolve_path
from inventory.services.scanner import ComScanner, list_serial_ports


@dataclass
class CheckResult:
    key: str
    title: str
    status: str  # ok | warning | error
    message: str
    critical: bool = False

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"

    @property
    def blocks_start(self) -> bool:
        return self.critical and self.status == "error"


class PreflightService:
    def __init__(self, config: AppConfig, paths: dict[str, Path]):
        self.config = config
        self.paths = paths

    def run_all(self) -> list[CheckResult]:
        return [
            self.check_battery(),
            self.check_disk(),
            self.check_database(),
            self.check_export_dir(),
            self.check_vosk(),
            self.check_microphone(),
            self.check_scanner(),
            self.check_camera(),
            self.check_photo_port(),
            self.check_datetime(),
        ]

    def check_battery(self) -> CheckResult:
        try:
            import psutil

            battery = psutil.sensors_battery()
            if battery is None:
                return CheckResult(
                    "battery", "Батарея", "warning", "Не удалось определить заряд (питание от сети)"
                )
            percent = int(battery.percent)
            if percent <= self.config.low_battery_critical:
                return CheckResult(
                    "battery",
                    "Батарея",
                    "error",
                    f"Критический заряд: {percent}%. Подключите питание",
                    critical=True,
                )
            if percent <= self.config.low_battery_warning:
                return CheckResult(
                    "battery",
                    "Батарея",
                    "warning",
                    f"Низкий заряд: {percent}%. Рекомендуется подзарядить",
                )
            return CheckResult("battery", "Батарея", "ok", f"{percent}%")
        except Exception as exc:  # noqa: BLE001
            return CheckResult("battery", "Батарея", "warning", f"Проверка недоступна: {exc}")

    def check_disk(self) -> CheckResult:
        try:
            usage = shutil.disk_usage(self.paths["data"])
            free_gb = usage.free / (1024**3)
            if free_gb < 0.5:
                return CheckResult(
                    "disk",
                    "Диск",
                    "error",
                    f"Свободно {free_gb:.2f} ГБ — недостаточно",
                    critical=True,
                )
            if free_gb < 1:
                return CheckResult(
                    "disk", "Диск", "warning", f"Свободно {free_gb:.2f} ГБ — мало"
                )
            return CheckResult("disk", "Диск", "ok", f"Свободно {free_gb:.1f} ГБ")
        except Exception as exc:  # noqa: BLE001
            return CheckResult("disk", "Диск", "error", f"Ошибка проверки: {exc}", critical=True)

    def check_database(self) -> CheckResult:
        db_path = self.paths["db"]
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            if not os.access(db_path.parent, os.W_OK):
                return CheckResult(
                    "database", "База данных", "error", "Папка данных недоступна для записи", critical=True
                )
            connection = sqlite3.connect(str(db_path))
            try:
                row = connection.execute("PRAGMA quick_check").fetchone()
                result = row[0] if row else "unknown"
            finally:
                connection.close()
            if result != "ok":
                return CheckResult(
                    "database", "База данных", "error", f"Проверка не пройдена: {result}", critical=True
                )
            return CheckResult("database", "База данных", "ok", str(db_path))
        except Exception as exc:  # noqa: BLE001
            return CheckResult("database", "База данных", "error", str(exc), critical=True)

    def check_export_dir(self) -> CheckResult:
        exports = self.paths["exports"]
        try:
            exports.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=exports, prefix=".write_test_", delete=True):
                pass
            return CheckResult("export", "Папка экспорта", "ok", str(exports))
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                "export", "Папка экспорта", "error", f"Нет доступа: {exc}", critical=True
            )

    def check_vosk(self) -> CheckResult:
        if not self.config.voice.enabled:
            return CheckResult("vosk", "Голос (Vosk)", "ok", "Отключён в настройках")
        model_path = resolve_path(self.config.voice.model_path)
        if not model_path.is_dir():
            return CheckResult(
                "vosk",
                "Голос (Vosk)",
                "warning",
                f"Папка модели не найдена: {model_path}",
            )
        required = [model_path / "am" / "final.mdl", model_path / "conf" / "mfcc.conf"]
        missing = [str(path.name) for path in required if not path.exists()]
        if missing:
            return CheckResult(
                "vosk",
                "Голос (Vosk)",
                "warning",
                f"В модели нет файлов: {', '.join(missing)}",
            )
        return CheckResult("vosk", "Голос (Vosk)", "ok", "Модель найдена")

    def check_microphone(self) -> CheckResult:
        if not self.config.voice.enabled:
            return CheckResult("microphone", "Микрофон", "ok", "Голос отключён")
        try:
            import sounddevice as sd

            devices = [
                device
                for device in sd.query_devices()
                if device.get("max_input_channels", 0) > 0
            ]
            if not devices:
                return CheckResult(
                    "microphone", "Микрофон", "warning", "Устройства ввода не найдены"
                )
            default_input = sd.default.device[0] if sd.default.device else None
            all_devices = list(sd.query_devices())
            name = devices[0].get("name", "микрофон")
            if default_input is not None and 0 <= default_input < len(all_devices):
                default_device = all_devices[default_input]
                if default_device.get("max_input_channels", 0) > 0:
                    name = default_device.get("name", name)
            return CheckResult("microphone", "Микрофон", "ok", str(name))
        except Exception as exc:  # noqa: BLE001
            return CheckResult("microphone", "Микрофон", "warning", f"Проверка недоступна: {exc}")

    def check_scanner(self) -> CheckResult:
        mode = self.config.scanner.mode
        if mode != "com":
            return CheckResult("scanner", "Сканер", "ok", f"Режим: {mode}")
        ports = list_serial_ports()
        scanner = ComScanner(self.config.scanner)
        try:
            scanner.open()
            scanner.close()
            return CheckResult(
                "scanner", "Сканер", "ok", f"{self.config.scanner.port} открыт"
            )
        except Exception as exc:  # noqa: BLE001
            available = ", ".join(port.device for port in ports) or "нет"
            return CheckResult(
                "scanner",
                "Сканер",
                "warning",
                f"{self.config.scanner.port} недоступен ({exc}); доступные порты: {available}",
            )
        finally:
            scanner.close()

    def check_camera(self) -> CheckResult:
        try:
            import cv2  # type: ignore
        except ImportError:
            return CheckResult(
                "camera", "Камера", "warning", "Модуль фото не установлен (фото необязательны)"
            )
        try:
            capture = cv2.VideoCapture(0)
            opened = capture.isOpened()
            capture.release()
            if opened:
                return CheckResult("camera", "Камера", "ok", "Доступна")
            return CheckResult("camera", "Камера", "warning", "Камера не открылась")
        except Exception as exc:  # noqa: BLE001
            return CheckResult("camera", "Камера", "warning", f"Ошибка: {exc}")

    def check_photo_port(self) -> CheckResult:
        """Порт приёма фото с телефона: свободен / будет взят другой / все заняты."""
        try:
            from inventory.services.photo_server import DEFAULT_PORT, find_free_port

            port = find_free_port()
        except Exception as exc:  # noqa: BLE001 — проверка не должна ронять preflight
            return CheckResult(
                "photo_port", "Порт приёма фото", "warning", f"Не удалось проверить: {exc}"
            )
        if port == DEFAULT_PORT:
            return CheckResult(
                "photo_port", "Порт приёма фото", "ok",
                f"Порт {DEFAULT_PORT} свободен — приём фото с телефона без сюрпризов",
            )
        if port is not None:
            return CheckResult(
                "photo_port", "Порт приёма фото", "warning",
                f"Порт {DEFAULT_PORT} занят — приложение возьмёт {port}, "
                "адрес в окне «📱 Телефон» будет другим",
            )
        return CheckResult(
            "photo_port", "Порт приёма фото", "error",
            f"Все порты {DEFAULT_PORT}–{DEFAULT_PORT + 9} заняты — приём фото работать не будет",
        )

    def check_datetime(self) -> CheckResult:
        return CheckResult(
            "datetime",
            "Дата и время",
            "ok",
            datetime.now().astimezone().strftime("%d.%m.%Y %H:%M %Z"),
        )

    @staticmethod
    def can_start(checks: list[CheckResult]) -> bool:
        return not any(check.blocks_start for check in checks)

    @staticmethod
    def summary(checks: list[CheckResult]) -> dict[str, int]:
        return {
            "ok": sum(1 for check in checks if check.status == "ok"),
            "warning": sum(1 for check in checks if check.status == "warning"),
            "error": sum(1 for check in checks if check.status == "error"),
        }

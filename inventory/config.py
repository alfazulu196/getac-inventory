# inventory/config.py
"""Настройки приложения и справочники."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from inventory.core.models import DeviceType

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def app_base_dir() -> Path:
    """Каталог запуска: рядом с exe в замороженной сборке, иначе корень проекта."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT


APP_BASE = app_base_dir()
CONFIG_PATH = APP_BASE / "config.json"
VOSK_MODEL_DIR = APP_BASE / "model"

# Версия приложения: видна в заголовке окна, настройках и в ПРОЧТИ_МЕНЯ сборки.
APP_VERSION = "1.6.5"


@dataclass
class ScannerConfig:
    mode: str = "com"  # com | hid | manual
    port: str = "COM3"
    baudrate: int = 9600
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 1
    encoding: str = "utf-8"
    timeout: float = 0.1
    laser_keys: list[str] = field(default_factory=lambda: ["F11", "F12"])
    laser_delay: float = 0.05
    dedup_seconds: float = 1.0
    strip_prefix: str = ""
    strip_suffix: str = ""


@dataclass
class VoiceConfig:
    enabled: bool = True
    model_path: str = "model"
    sample_rate: int = 16000
    channels: int = 1
    device: int | None = None
    dictionary: dict[str, str] = field(
        default_factory=lambda: {
            "камутатор": "коммутатор",
            "камутатора": "коммутатора",
            "камира": "камера",
            "камиру": "камеру",
            "регистратор": "регистратор",
            "регистратора": "регистратора",
            "ибп": "ИБП",
            "поэ": "PoE",
            "по е": "PoE",
            "витая пара": "витая пара",
        }
    )


@dataclass
class AppConfig:
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    data_dir: str = "data"
    last_session_id: str | None = None
    last_location_id: str | None = None
    last_device_type: str = "camera"
    low_battery_warning: int = 20
    low_battery_critical: int = 10


DEVICE_TYPES: list[DeviceType] = [
    DeviceType("camera", "IP-камера", "CAM", "PoE", "📷"),
    DeviceType("switch", "Коммутатор", "SW", "220V", "🔀"),
    DeviceType("nvr", "Видеорегистратор", "NVR", "220V", "💾"),
    DeviceType("dvr", "Аналоговый регистратор", "DVR", "220V", "📼"),
    DeviceType("router", "Маршрутизатор", "RTR", "220V", "🌐"),
    DeviceType("ap", "Точка доступа", "AP", "PoE", "📶"),
    DeviceType("ups", "ИБП", "UPS", "220V", "🔋"),
    DeviceType("psu", "Блок питания", "PSU", "220V", "🔌"),
    DeviceType("patch", "Патч-панель", "PP", "", "🧰"),
    DeviceType("server", "Сервер", "SRV", "220V", "🖥"),
    DeviceType("workstation", "Рабочая станция", "WS", "220V", "💻"),
    DeviceType("other", "Прочее", "OTH", "", "📦"),
]

DEVICE_TYPE_BY_CODE: dict[str, DeviceType] = {t.code: t for t in DEVICE_TYPES}

STATUSES: list[dict[str, str]] = [
    {"code": "installed", "name": "Установлено", "color": "#2ecc71"},
    {"code": "faulty", "name": "Не работает", "color": "#e74c3c"},
    {"code": "replace", "name": "Требует замены", "color": "#e67e22"},
    {"code": "spare", "name": "Резерв", "color": "#3498db"},
    {"code": "missing", "name": "Не найдено", "color": "#95a5a6"},
    {"code": "removed", "name": "Демонтировано", "color": "#7f8c8d"},
]
STATUS_BY_CODE: dict[str, dict[str, str]] = {s["code"]: s for s in STATUSES}

POWER_TYPES: list[str] = [
    "220V",
    "220V (ИБП)",
    "PoE (802.3af)",
    "PoE+ (802.3at)",
    "12V",
    "24V",
    "Аккумулятор",
    "Неизвестно",
]


def _load_section(cls: type, data: dict[str, Any] | None):
    base = asdict(cls())
    if data:
        base.update(data)
    return cls(**base)


def load_config(path: Path | str = CONFIG_PATH) -> AppConfig:
    """Загружает config.json, дополняя недостающие поля значениями по умолчанию."""
    path = Path(path)
    if not path.exists():
        cfg = AppConfig()
        save_config(cfg, path)
        return cfg
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppConfig()

    cfg = AppConfig()
    if isinstance(raw.get("scanner"), dict):
        cfg.scanner = _load_section(ScannerConfig, raw["scanner"])
    if isinstance(raw.get("voice"), dict):
        cfg.voice = _load_section(VoiceConfig, raw["voice"])
    cfg.data_dir = raw.get("data_dir", cfg.data_dir)
    cfg.last_session_id = raw.get("last_session_id")
    cfg.last_location_id = raw.get("last_location_id")
    cfg.last_device_type = raw.get("last_device_type", cfg.last_device_type)
    cfg.low_battery_warning = int(raw.get("low_battery_warning", cfg.low_battery_warning))
    cfg.low_battery_critical = int(raw.get("low_battery_critical", cfg.low_battery_critical))
    return cfg


def save_config(config: AppConfig, path: Path | str = CONFIG_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def resolve_path(value: str | Path, base: Path | None = None) -> Path:
    """Относительный путь считается от каталога запуска (или от заданного base)."""
    p = Path(value)
    if p.is_absolute():
        return p
    return (base or APP_BASE) / p


def data_paths(config: AppConfig) -> dict[str, Path]:
    """Возвращает пути данных и создает папки."""
    data_dir = resolve_path(config.data_dir)
    paths = {
        "data": data_dir,
        "db": data_dir / "inventory.db",
        "photos": data_dir / "photos",
        "exports": data_dir / "exports",
        "backups": data_dir / "backups",
        "logs": data_dir / "logs",
    }
    for key, value in paths.items():
        # db — это файл, а не папка: создаём только его родителя.
        target = value.parent if key == "db" else value
        target.mkdir(parents=True, exist_ok=True)
    return paths


def device_type_name(code: str) -> str:
    device_type = DEVICE_TYPE_BY_CODE.get(code)
    return device_type.name if device_type else code


def device_type_prefix(code: str) -> str:
    device_type = DEVICE_TYPE_BY_CODE.get(code)
    return device_type.prefix if device_type else "DEV"


def status_name(code: str) -> str:
    status = STATUS_BY_CODE.get(code)
    return status["name"] if status else code


def status_color(code: str) -> str:
    status = STATUS_BY_CODE.get(code)
    return status["color"] if status else "#95a5a6"

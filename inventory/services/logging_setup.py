# inventory/services/logging_setup.py
"""Журналы и парашют аварийных завершений.

Пишет data/logs/app.log (ротация по размеру) и, при необработанном исключении,
отдельный data/logs/crash-<дата>.txt с полным трейсбеком. При следующем старте
приложение может попросить посмотреть последний crash-файл (см. last_crash_file).
"""
from __future__ import annotations

import logging
import logging.handlers
import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("inventory")
_crash_logs_dir: Path | None = None


def setup_logging(logs_dir: Path) -> Path:
    """Подключает файловый журнал (ротация 5×2 МБ). Возвращает путь к app.log."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "app.log"
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers):
        return log_path
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return log_path


def install_crash_handler(logs_dir: Path) -> None:
    """sys.excepthook → crash-файл с трейсбеком, затем поведение по умолчанию."""
    global _crash_logs_dir
    _crash_logs_dir = logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)

    def _hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        crash_path = logs_dir / f"crash-{stamp}.txt"
        try:
            with open(crash_path, "w", encoding="utf-8") as fh:
                fh.write(
                    "Аварийное завершение приложения инвентаризации\n"
                    f"Время: {datetime.now().isoformat(timespec='seconds')}\n"
                    f"Система: {platform.system()} {platform.release()} / "
                    f"Python {sys.version.split()[0]}\n"
                    f"Рабочая папка: {Path.cwd()}\n"
                    "\n--- Трейсбек ---\n"
                )
                traceback.print_exception(exc_type, exc, tb, file=fh)
        except OSError:
            crash_path = None  # type: ignore[assignment]
        logger.critical(
            "Необработанное исключение: %s (%s)",
            exc_type.__name__,
            crash_path.name if crash_path else "crash-файл не записан",
        )
        sys.__excepthook__(exc_type, exc, tb)
        if crash_path is not None:
            on_crash = getattr(_hook, "callback", None)
            if on_crash:
                try:
                    on_crash(crash_path)
                except Exception:  # noqa: BLE001 — падение уже случилось, не усугубляем
                    pass

    sys.excepthook = _hook


def set_crash_callback(callback) -> None:
    """Колбэк(path) вызывается после записи crash-файла (например, диалог в UI)."""
    hook = sys.excepthook
    if callable(hook):
        hook.callback = callback


def last_crash_file(logs_dir: Path) -> Path | None:
    """Самый свежий crash-файл (если есть) — для вопроса при следующем старте."""
    logs_dir = Path(logs_dir)
    if not logs_dir.is_dir():
        return None
    candidates = sorted(logs_dir.glob("crash-*.txt"), key=lambda p: p.name)
    return candidates[-1] if candidates else None


def log_exception(context: str) -> None:
    """Записать текущее исключение в журнал (для except-блоков с навесным текстом)."""
    logger.error("Ошибка в %s:\n%s", context, traceback.format_exc())


def log_info(message: str, *args) -> None:
    logger.info(message, *args)

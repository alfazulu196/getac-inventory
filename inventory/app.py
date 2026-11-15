# inventory/app.py
"""Точка входа новой версии приложения."""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from inventory.config import APP_VERSION, data_paths, load_config
from inventory.core.db import Database
from inventory.core.oui import warmup_vendor_db
from inventory.core.repository import InventoryRepository
from inventory.services.logging_setup import (
    install_crash_handler,
    last_crash_file,
    log_info,
    set_crash_callback,
    setup_logging,
)
from inventory.services.voice import VoiceService
from inventory.ui.main_window import MainWindow
from inventory.ui.widgets import apply_app_style, ensure_field_restyle


def open_database_with_recovery(paths: dict[str, Path]) -> Database | None:
    """База не открывается (побита/подменена)? → восстановление из копии.

    v1.6.10: FR-60 закрыл восстановление в настройках; здесь закрывается
    сценарий «приложение вообще не стартует». Предлагаем выбрать копию из
    data\backups, восстанавливаем (WAL-safe) и пробуем открыть снова —
    цикл продолжается, пока пользователь не откажется или база не
    откроется. Битый файл сохраняется как safety-копия before_restore_*.
    """
    from inventory.services.backup import restore_database  # noqa: PLC0415

    while True:
        try:
            return Database(paths["db"])
        except Exception as exc:  # noqa: BLE001 — sqlite может упасть по-разному
            backups = Path(paths["backups"])
            copies = sorted(backups.glob("*.db")) if backups.exists() else []
            if not copies:
                QMessageBox.critical(
                    None,
                    "База данных",
                    f"Не удалось открыть базу:\n{paths['db']}\n\n{exc}\n\n"
                    f"Резервных копий в {backups} нет — восстановление "
                    "невозможно.",
                )
                return None
            answer = QMessageBox.question(
                None,
                "База повреждена",
                f"База не открывается:\n{paths['db']}\n\n{exc}\n\n"
                f"В папке копий: {len(copies)} файл(ов).\n"
                "Выбрать копию для восстановления?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return None
            chosen, _f = QFileDialog.getOpenFileName(
                None, "Выберите копию базы", str(backups), "Копии базы (*.db)"
            )
            if not chosen:
                return None
            try:
                restore_database(chosen, paths["db"], safety_dir=backups)
            except Exception as restore_exc:  # noqa: BLE001
                QMessageBox.critical(
                    None,
                    "Восстановление базы",
                    f"Не удалось восстановить из {chosen}:\n{restore_exc}\n\n"
                    "Попробуйте другую копию или откажитесь.",
                )


def main() -> int:
    config = load_config()
    paths = data_paths(config)
    setup_logging(paths["logs"])
    install_crash_handler(paths["logs"])

    app = QApplication(sys.argv)
    app.setApplicationName("Инвентаризация объекта")
    app.setOrganizationName("Getac Inventory")
    app.setApplicationVersion(APP_VERSION)
    apply_app_style(app, field_mode=config.field_mode)

    # Прошлая аварийная работа? Подскажем файл, чтобы поле могло разобраться.
    last_crash = last_crash_file(paths["logs"])
    if last_crash is not None:
        answer = QMessageBox.question(
            None,
            "Предыдущий запуск",
            "В прошлый раз приложение завершилось аварийно.\n"
            f"Файл разбора: {last_crash.name}\n\nОткрыть папку с разбором?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths["logs"])))

    # При новом падении — показать путь к crash-файлу сразу.
    set_crash_callback(
        lambda path: QMessageBox.warning(
            None, "Авария", f"Произошла необработанная ошибка.\nРазбор: {path}"
        )
    )

    log_info(
        "Приложение запущено: версия %s (сборка=%s)",
        APP_VERSION,
        "exe" if getattr(sys, "frozen", False) else "src",
    )

    db = open_database_with_recovery(paths)
    if db is None:
        return 1

    repo = InventoryRepository(db)
    voice = VoiceService(config.voice)

    # Полная база производителей (IEEE OUI) грузится в фоне — к моменту,
    # когда пользователь введёт MAC, подсказка вендора будет готова.
    warmup_vendor_db()

    window = MainWindow(config, paths, repo, voice)
    window.showMaximized()
    ensure_field_restyle()  # окна уже созданы — перекрасить их подписи
    try:
        return app.exec()
    finally:
        log_info("Приложение закрыто")
        voice.close()
        db.close()


if __name__ == "__main__":
    sys.exit(main())

# inventory/ui/main_window.py
"""Главное окно: старт → проверка → обход."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMainWindow, QStackedWidget, QMessageBox

from inventory.config import APP_VERSION, AppConfig
from inventory.core.models import Session
from inventory.core.repository import InventoryRepository
from inventory.services.backup import backup_database
from inventory.services.voice import VoiceService
from inventory.ui.preflight_dialog import PreflightDialog
from inventory.ui.settings_dialog import SettingsDialog
from inventory.ui.start_screen import StartScreen
from inventory.ui.survey_screen import SurveyScreen


class MainWindow(QMainWindow):
    def __init__(
        self,
        config: AppConfig,
        paths: dict[str, Path],
        repo: InventoryRepository,
        voice: VoiceService,
        parent=None,
    ):
        super().__init__(parent)
        self.config = config
        self.paths = paths
        self.repo = repo
        self.voice = voice
        self.survey_screen: SurveyScreen | None = None

        self.setWindowTitle(f"Инвентаризация объекта — Getac V110 (v{APP_VERSION})")
        self.setMinimumSize(1100, 650)
        self.resize(1360, 740)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.start_screen = StartScreen(repo, paths)
        self.start_screen.session_selected.connect(self.start_session)
        self.start_screen.settings_requested.connect(self.open_settings)
        self.start_screen.quit_requested.connect(self.close)
        self.stack.addWidget(self.start_screen)
        self.stack.setCurrentWidget(self.start_screen)

    def start_session(self, session: Session) -> None:
        try:
            backup_database(self.paths["db"], self.paths["backups"])
        except Exception:  # noqa: BLE001 — бэкап не должен блокировать старт
            pass
        dialog = PreflightDialog(self.config, self.paths, session, self)
        if dialog.exec() == PreflightDialog.DialogCode.Accepted:
            self.open_survey(session)
        else:
            self.start_screen.refresh()
            self.stack.setCurrentWidget(self.start_screen)

    def open_survey(self, session: Session) -> None:
        if self.survey_screen:
            self.survey_screen.stop_scanner()
            self.survey_screen.detach_intel_worker()
            self.survey_screen.close()
            self.stack.removeWidget(self.survey_screen)
            self.survey_screen.deleteLater()
            self.survey_screen = None
        self.survey_screen = SurveyScreen(
            self.repo,
            self.config,
            self.paths,
            session,
            self.voice,
            self,
        )
        self.survey_screen.session_finished.connect(self._on_session_finished)
        self.stack.addWidget(self.survey_screen)
        self.stack.setCurrentWidget(self.survey_screen)
        self.survey_screen.setFocus()

    def _on_session_finished(self) -> None:
        self.show_start()

    def show_start(self) -> None:
        if self.survey_screen:
            self.survey_screen.stop_scanner()
            self.survey_screen.detach_intel_worker()
            self.survey_screen.close()
            self.stack.removeWidget(self.survey_screen)
            self.survey_screen.deleteLater()
            self.survey_screen = None
        self.start_screen.refresh()
        self.stack.setCurrentWidget(self.start_screen)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self.paths, self.voice, self.repo, self)
        dialog.exec()

    def closeEvent(self, event) -> None:
        if self.survey_screen:
            self.survey_screen.stop_scanner()
            self.survey_screen.detach_intel_worker()
            if self.survey_screen._voice_recording:
                self.voice.cancel_recording()
        try:
            backup_database(self.paths["db"], self.paths["backups"])
        except Exception:  # noqa: BLE001
            pass
        self.voice.close()
        self.repo.db.close()
        super().closeEvent(event)

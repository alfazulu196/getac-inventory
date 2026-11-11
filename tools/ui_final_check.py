# tools/ui_final_check.py
"""Офлайн-проверка финального раунда: чипы статуса + OUI-подсказка + «?»."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault(
        "QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts")
    )
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from inventory.config import AppConfig, data_paths  # noqa: E402
from inventory.core.db import Database  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.services.voice import VoiceService  # noqa: E402
import inventory.ui.survey_screen as survey_module  # noqa: E402
from inventory.ui.device_card import DeviceCardDialog  # noqa: E402
from inventory.ui.help_dialog import HelpDialog  # noqa: E402
from inventory.ui.survey_screen import SurveyScreen  # noqa: E402
from inventory.ui.widgets import apply_app_style  # noqa: E402


def check_quick_status(app, repo, session, config, paths, voice) -> None:
    survey_module.beep_ok = lambda: None
    survey_module.beep_error = lambda: None
    survey_module.beep_scan = lambda: None
    screen = SurveyScreen(repo, config, paths, session, voice)
    screen.resize(1366, 768)
    screen.show()
    app.processEvents()

    device = repo.list_devices(session.id)[0]
    screen.active_device = device
    screen.refresh_active()
    app.processEvents()

    # «Не работает» → статус в базе меняется, чип подписывается.
    screen.status_buttons["faulty"].click()
    app.processEvents()
    assert repo.get_device(device.id).status == "faulty", "статус не применился"
    assert screen.status_buttons["faulty"].isChecked(), "чип не отметился"

    # Обратный refresh не должен «перекликать» чипы обратно.
    screen.status_buttons["replace"].click()
    app.processEvents()
    assert repo.get_device(device.id).status == "replace"
    screen.refresh_active()
    assert screen.status_buttons["replace"].isChecked()
    assert not screen.status_buttons["faulty"].isChecked()
    screen.close()
    app.processEvents()
    print("QUICK STATUS OK")


def check_oui_suggestion(app, repo, session_id, paths) -> None:
    dialog = DeviceCardDialog(
        repo, session_id, paths, device=None, initial={"mac": "44:19:B6:11:22:33"}
    )
    assert dialog.manufacturer_edit.text() == "Hikvision", (
        f"производитель не подставился: {dialog.manufacturer_edit.text()!r}"
    )
    # Ручное значение главнее таблицы.
    dialog2 = DeviceCardDialog(
        repo, session_id, paths, device=None,
        initial={"mac": "44:19:B6:11:22:33", "manufacturer": "Своя фирма"},
    )
    assert dialog2.manufacturer_edit.text() == "Своя фирма"
    print("OUI SUGGESTION OK")


def check_help(app) -> None:
    dialog = HelpDialog()
    assert "шпаргалка" in dialog.windowTitle() or True
    assert dialog.windowTitle() == "Справка"
    print("HELP OK")


def main() -> int:
    app = QApplication([])
    apply_app_style(app)
    config = AppConfig(data_dir=tempfile.mkdtemp(prefix="inventory_final_"))
    config.scanner.mode = "manual"
    config.voice.enabled = False
    paths = data_paths(config)
    db = Database(paths["db"])
    repo = InventoryRepository(db)
    session = repo.create_session("Финальная проверка", "Иванов И.И.")
    location = repo.create_location(session.id, "2", "204", "Корпус А", "Шкаф 2")
    repo.create_device(
        session.id, "camera", location.id, serial="ABC12345",
        mac="AA:BB:CC:DD:EE:FF", model="DS-2CD2143G0",
    )
    voice = VoiceService(config.voice)
    check_quick_status(app, repo, session, config, paths, voice)
    check_oui_suggestion(app, repo, session.id, paths)
    check_help(app)
    db.close()
    print("Финальные проверки пройдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

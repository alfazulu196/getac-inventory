# tools/ui_smoke.py
"""Быстрый офлайн-смоук интерфейса без реального COM-порта и микрофона."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    # Offscreen-платформа не всегда находит системные шрифты — указываем явно.
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from inventory.config import AppConfig, data_paths  # noqa: E402
from inventory.core.db import Database  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.services.voice import VoiceService  # noqa: E402
import inventory.ui.survey_screen as survey_module  # noqa: E402
from inventory.ui.survey_screen import SurveyScreen  # noqa: E402
from inventory.ui.widgets import apply_app_style  # noqa: E402


def main() -> int:
    app = QApplication([])
    apply_app_style(app)

    config = AppConfig(data_dir=tempfile.mkdtemp(prefix="inventory_ui_"))
    config.scanner.mode = "manual"
    config.voice.enabled = False
    paths = data_paths(config)
    db = Database(paths["db"])
    repo = InventoryRepository(db)
    session = repo.create_session("Тестовый объект", "Иванов И.И.")
    location = repo.create_location(session.id, "2", "204", "Корпус А", "Шкаф 2")
    repo.create_device(
        session.id,
        "camera",
        location.id,
        serial="ABC12345",
        mac="AA:BB:CC:DD:EE:FF",
        model="DS-2CD2143G0",
        manufacturer="Hikvision",
        power_type="PoE",
    )
    repo.create_device(
        session.id,
        "switch",
        location.id,
        serial="FDO2134X0ZZ",
        model="Catalyst 2960X",
        manufacturer="Cisco",
        power_type="220V",
    )
    config.last_location_id = location.id
    voice = VoiceService(config.voice)

    # Звук в headless-режиме не нужен.
    survey_module.beep_ok = lambda: None
    survey_module.beep_error = lambda: None
    survey_module.beep_scan = lambda: None

    screen = SurveyScreen(repo, config, paths, session, voice)
    screen.resize(1366, 768)
    screen.show()
    app.processEvents()

    # Полевой сценарий: уникальный код в быстром режиме.
    screen.process_code("SN=TEST99999;MAC=12:34:56:78:9A:BC")
    app.processEvents()
    count = repo.count_devices(session.id)
    assert count == 3, f"ожидалось 3 устройства, получено {count}"
    assert screen.active_device is not None
    assert screen.active_device.display_id == "CAM-002"
    assert screen.active_device.serial == "TEST99999"
    assert screen.active_device.mac == "12:34:56:78:9A:BC"
    assert "CAM-002" in screen.active_card.toPlainText()
    print("Полевой сценарий: сканирование и быстрое сохранение — OK")
    if os.environ.get("UI_SMOKE_DEBUG"):
        card = screen.active_card
        print(
            "DEBUG card:",
            "h=", card.height(),
            "chars=", len(card.toPlainText()),
        )

    output = Path(tempfile.gettempdir()) / "opencode" / "ui_smoke.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    screen.grab().save(str(output))
    print(output)

    screen.close()
    voice.close()
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

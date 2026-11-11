# tools/ui_dialogs_smoke.py
"""Офлайн-проверка всех диалогов: создание, отображение, скриншоты."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault(
        "QT_QPA_FONTDIR",
        str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"),
    )
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from inventory.config import AppConfig, data_paths  # noqa: E402
from inventory.core.db import Database  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.services.voice import VoiceService  # noqa: E402
from inventory.ui.device_card import DeviceCardDialog  # noqa: E402
from inventory.ui.device_list import DeviceListDialog  # noqa: E402
from inventory.ui.duplicates_dialog import DuplicatesDialog  # noqa: E402
from inventory.ui.finish_dialog import FinishDialog  # noqa: E402
from inventory.ui.help_dialog import HelpDialog  # noqa: E402
from inventory.ui.import_session_dialog import ImportSessionDialog  # noqa: E402
from inventory.ui.location_dialog import LocationDialog  # noqa: E402
from inventory.services.photo_server import PhotoServer, ReceivedPhoto  # noqa: E402
from inventory.ui.phone_link_dialog import PhoneLinkDialog  # noqa: E402
from inventory.ui.photo_dialog import PhotoCaptureDialog  # noqa: E402
from inventory.ui.preflight_dialog import PreflightDialog  # noqa: E402
from inventory.ui.settings_dialog import SettingsDialog  # noqa: E402
from inventory.ui.start_screen import SessionHistoryDialog, StartScreen  # noqa: E402
from inventory.ui.widgets import apply_app_style  # noqa: E402

OUTPUT_DIR = Path(tempfile.gettempdir()) / "opencode" / "ui_dialogs"
SIZE = (1366, 768)


def grab(app: QApplication, widget, name: str) -> Path:
    widget.resize(*SIZE)
    widget.show()
    app.processEvents()
    path = OUTPUT_DIR / f"{name}.png"
    widget.grab().save(str(path))
    widget.close()
    app.processEvents()
    print(f"{name}: {path}")
    return path


def main() -> int:
    app = QApplication([])
    apply_app_style(app)

    config = AppConfig(data_dir=tempfile.mkdtemp(prefix="inventory_dialogs_"))
    config.scanner.mode = "manual"
    config.voice.enabled = False
    paths = data_paths(config)
    db = Database(paths["db"])
    repo = InventoryRepository(db)

    session = repo.create_session("Бизнес-центр «Север»", "Иванов И.И.")
    location = repo.create_location(session.id, "2", "204", "Корпус А", "Шкаф 2")
    camera = repo.create_device(
        session.id,
        "camera",
        location.id,
        serial="ABC12345",
        mac="AA:BB:CC:DD:EE:FF",
        ip="192.168.10.51",
        model="DS-2CD2143G0",
        manufacturer="Hikvision",
        power_type="PoE",
        status="faulty",
        note="Помутнела оптика, требуется чистка.",
    )
    switch = repo.create_device(
        session.id, "switch", location.id, serial="FDO2134X0ZZ", model="Catalyst 2960X",
        manufacturer="Cisco", power_type="220V", is_verified=True,
    )
    # Дубль камеры по S/N — для демонстрации «Дубликатов».
    repo.create_device(
        session.id, "camera", location.id, serial="ABC12345",
        ip="192.168.10.52", model="DS-2CD2143G0 (второй экземпляр)",
    )
    repo.set_connection(camera.id, switch.id, "Gi0/12", "UTP")

    voice = VoiceService(config.voice)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    start = StartScreen(repo, paths)
    grab(app, start, "start_screen")

    history = SessionHistoryDialog(repo, paths)
    history.table.selectRow(0)
    grab(app, history, "session_history")

    preflight = PreflightDialog(config, paths, session)
    grab(app, preflight, "preflight")

    location_dialog = LocationDialog(repo, session.id, location)
    grab(app, location_dialog, "location")

    card = DeviceCardDialog(
        repo, session.id, paths, voice, device=camera
    )
    grab(app, card, "device_card_edit")

    new_card = DeviceCardDialog(
        repo,
        session.id,
        paths,
        voice,
        location=location,
        initial={
            "device_type": "camera",
            "serial": "NEW12345",
            "mac": "11:22:33:44:55:66",
            "scanned_code": "NEW12345",
            "code_type": "serial",
        },
    )
    grab(app, new_card, "device_card_new")

    device_list = DeviceListDialog(repo, session.id, paths, voice, location)
    grab(app, device_list, "device_list")

    duplicates = DuplicatesDialog(repo, session.id, paths, voice)
    duplicates.table.selectRow(0)
    grab(app, duplicates, "duplicates")

    # Диалог импорта: нужна «база напарника» рядом (файл + папка photos).
    partner_dir = Path(tempfile.mkdtemp(prefix="inventory_partner_"))
    partner_db = Database(partner_dir / "inventory.db")
    partner_repo = InventoryRepository(partner_db)
    partner_session = partner_repo.create_session("Объект-напарник", "Петров П.П.")
    partner_loc = partner_repo.create_location(partner_session.id, "1", "301", "Корпус Б", "Щ-2")
    partner_repo.create_device(
        partner_session.id, "camera", partner_loc.id, serial="PRTC0001",
        mac="AA:BB:CC:00:00:01", model="DS-2CD2143G0", note="Со второго ноутбука",
    )
    (partner_dir / "photos").mkdir()
    partner_db.close()
    import_dialog = ImportSessionDialog(repo, paths, session)
    import_dialog._load_file(partner_dir / "inventory.db")
    grab(app, import_dialog, "import_session")

    finish = FinishDialog(repo, session, paths)
    grab(app, finish, "finish")

    settings = SettingsDialog(config, paths, voice)
    grab(app, settings, "settings")

    help_dialog = HelpDialog()
    grab(app, help_dialog, "help")

    photo = PhotoCaptureDialog(paths["photos"])
    grab(app, photo, "photo")

    phone_server = PhotoServer(paths["photos"])
    phone_server.start()
    phone_server.urls = ["http://192.168.137.1:8790/demo12345"]
    phone_server.received.append(
        ReceivedPhoto(
            path="C:/demo/CAM-007_20261105_142033.jpg",
            device_name="CAM-007",
            location_label="Этаж 2 · Помещение 204",
            saved_at="14:20:33",
        )
    )
    try:
        phone_link = PhoneLinkDialog(phone_server)
        grab(app, phone_link, "phone_link")
    finally:
        phone_server.stop()

    voice.close()
    db.close()
    print("Все диалоги созданы и отрисованы.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

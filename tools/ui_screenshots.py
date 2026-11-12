# tools/ui_screenshots.py
"""Скриншот-тур для README: интерфейс снимается офскрин, без запуска GUI.

Готовит PNG в tools/assets/screenshots/:
  01_obhod.png     — экран обхода (таблица устройств, карточка)
  02_stoyka.png    — диалог «Стойка по фото (ИИ)» с реальным черновиком
                     из журнала бенчмарка (rack_mock.jpg, 8 юнитов)
  03_kartochka.png — карточка устройства

Запуск: python -X utf8 tools/ui_screenshots.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# Рендерим на настоящей платформе (windows): в offscreen комбобоксы внутри
# QTableWidget теряют текст — артефакт виртуального экрана. Окна на пару
# секунд мелькнут на рабочем столе — это ожидаемо. Для headless-прогона:
#   UI_SHOTS_OFFSCREEN=1 python -X utf8 tools/ui_screenshots.py
if os.environ.get("UI_SHOTS_OFFSCREEN"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        # Offscreen-платформа не всегда находит системные шрифты — указываем.
        os.environ.setdefault(
            "QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts")
        )
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # noqa: E402

from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402

import inventory.ui.rack_dialog as rack_module  # noqa: E402
import inventory.ui.survey_screen as survey_module  # noqa: E402
from inventory.config import AppConfig, data_paths  # noqa: E402
from inventory.core.db import Database  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.services.rack_ai import RackAnalysis, RackDevice  # noqa: E402
from inventory.services.voice import VoiceService  # noqa: E402
from inventory.ui.rack_dialog import RackDialog  # noqa: E402
from inventory.ui.survey_screen import SurveyScreen  # noqa: E402
from inventory.ui.widgets import apply_app_style  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tools" / "assets" / "screenshots"
JOURNAL = ROOT / "data" / "logs" / "rack_ai_log.jsonl"

MOCK_TRUTH = [  # значения, из которых соберём демо-данные экрана обхода
    ("camera", "Hikvision", "DS-2CD2143G0", "PoE", "ABC12345"),
    ("switch", "Cisco", "Catalyst 2960X", "220V", "FDO2134X0ZZ"),
    ("server", "HP", "ProLiant DL380 G9", "220V", ""),
    ("ups", "APC", "Smart-UPS SMT750R", "220V", ""),
    ("router", "MikroTik", "RB2011UiAS-IN", "PoE", ""),
    ("nvr", "Hikvision", "DS-7608NI-K2", "220V", ""),
]


def latest_mock_analysis() -> RackAnalysis:
    """Реальный черновик стойки из журнала (rack_mock.jpg, последний)."""
    if JOURNAL.exists():
        for line in reversed(
            JOURNAL.read_text(encoding="utf-8").splitlines()
        ):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("photo") == "rack_mock.jpg" and entry.get("ok"):
                devices = [
                    RackDevice(
                        type_code=d.get("type", "other"),
                        brand=d.get("brand", ""),
                        model=d.get("model", ""),
                        position=d.get("position", ""),
                        detail=d.get("detail", ""),
                    )
                    for d in entry.get("devices", [])
                ]
                return RackAnalysis(
                    ok=True, model_used=entry.get("model", ""),
                    elapsed_s=entry.get("elapsed_s", 0), devices=devices,
                )
    return RackAnalysis(ok=True, model_used="qwen2.5vl:3b", elapsed_s=95.0, devices=[])


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    apply_app_style(app)
    # Офскрин: всплывающие окна не блокируют поток снимков.
    survey_module.beep_ok = lambda: None
    survey_module.beep_error = lambda: None
    survey_module.beep_scan = lambda: None
    survey_module.QMessageBox.information = lambda *a, **k: None
    survey_module.QMessageBox.warning = lambda *a, **k: None
    survey_module.QMessageBox.critical = lambda *a, **k: None
    rack_module.QMessageBox.information = lambda *a, **k: None
    rack_module.QMessageBox.warning = lambda *a, **k: None

    class _FakeCapture(QDialog):
        """Заглушка диалога камеры: закрылся без фото (как в ui_rack_smoke)."""

        def __init__(self, *a, **k):
            super().__init__()
            self.photo_path = None

        def exec(self) -> int:
            return QDialog.DialogCode.Rejected.value

    rack_module.PhotoCaptureDialog = _FakeCapture

    config = AppConfig(data_dir=tempfile.mkdtemp(prefix="inventory_shots_"))
    config.scanner.mode = "manual"
    config.voice.enabled = False
    paths = data_paths(config)
    db = Database(paths["db"])
    repo = InventoryRepository(db)
    session = repo.create_session("Демо-объект «Северный»", "Иванов И. И.")
    location = repo.create_location(
        session.id, "2", "204", "Корпус А", "Шкаф серверный 2"
    )
    for type_code, brand, model, power, serial in MOCK_TRUTH:
        repo.create_device(
            session.id, type_code, location.id, serial=serial,
            model=model, manufacturer=brand, power_type=power,
        )
    repo.set_setting("contractors", "[]")
    config.last_location_id = location.id
    voice = VoiceService(config.voice)

    screen = SurveyScreen(repo, config, paths, session, voice)
    print("экран обхода создан", flush=True)
    screen.resize(1366, 768)
    screen.show()
    app.processEvents()
    # Активная карточка: полевой сценарий сканирования (проверенный путь).
    screen.process_code("SN=TEST99999;MAC=12:34:56:78:9A:BC")
    app.processEvents()
    print("карточка активирована", flush=True)
    screen.grab().save(str(OUT / "01_obhod.png"))
    print(f"01_obhod.png ({(OUT / '01_obhod.png').stat().st_size // 1024} КБ)",
          flush=True)

    print("диалог стойки: создание…", flush=True)
    dialog = RackDialog(repo, session.id, paths, location=location)
    print("диалог создан", flush=True)
    dialog._on_analysis(latest_mock_analysis(), "rack_mock.jpg")
    dialog.resize(1080, 700)
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(OUT / "02_stoyka.png"))
    print(f"02_stoyka.png ({(OUT / '02_stoyka.png').stat().st_size // 1024} КБ)",
          flush=True)
    dialog.close()
    print("диалог закрыт", flush=True)

    card = screen.active_card
    if card is not None:
        card.grab().save(str(OUT / "03_kartochka.png"))
        print(f"03_kartochka.png "
              f"({(OUT / '03_kartochka.png').stat().st_size // 1024} КБ)",
              flush=True)

    screen.close()
    voice.close()
    db.close()
    print(f"Готово: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

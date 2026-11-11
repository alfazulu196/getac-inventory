# tools/ui_photo_intel_smoke.py
"""Офскрин-проверка новых потоков v1.5.0: карточка с фото-этикеткой."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault(
        "QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts")
    )
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

import inventory.ui.device_card as card_module  # noqa: E402
from inventory.config import AppConfig, data_paths  # noqa: E402
from inventory.core.db import Database  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.services.photo_intel import PhotoIntel  # noqa: E402
from inventory.ui.device_card import DeviceCardDialog  # noqa: E402

# Реальное фото этикетки D-Link из полевого теста (рядом с корнем проекта).
ROOT = Path(__file__).resolve().parent.parent
REAL_LABEL = ROOT / "Polevoj test" / "1794127057029.jpg"


def main() -> int:
    app = QApplication([])
    # Офскрин: всплывающие окна не блокируют.
    card_module.QMessageBox.information = lambda *a, **k: None

    config = AppConfig(data_dir=tempfile.mkdtemp(prefix="inventory_pi_"))
    paths = data_paths(config)
    db = Database(paths["db"])
    repo = InventoryRepository(db)
    session = repo.create_session("Тест", "Тестировщик")
    location = repo.create_location(session.id, "1", "101", "Корпус", "Шкаф")
    repo.set_setting("contractors", "[]")

    photo = paths["photos"] / "smoke_etiket.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xd9")  # минимальный JPEG

    # 1) Новая карточка: initial с фото этикетки по сценарию «с телефона».
    initial = {
        "device_type": "camera",
        "serial": "SNTEST777",
        "mac": "A0AB1B40C3E0",
        "model": "DES-1210",
        "photo_path": str(photo),
        "note_text": "Создано по фото этикетки (телефон) — проверьте поля.",
    }
    card = DeviceCardDialog(repo, session.id, paths, location=location, initial=initial)
    assert card.serial_edit.text() == "SNTEST777"
    assert card.mac_edit.text() == "A0AB1B40C3E0"
    assert card.model_edit.text() == "DES-1210"
    assert card.manufacturer_edit.text() == "D-Link", card.manufacturer_edit.text()  # по MAC из полной базы
    assert str(photo) in card._pending_photos, card._pending_photos
    assert "фото этикетки" in card.note_edit.toPlainText()
    assert card.label_btn.isEnabled()
    print("1) Карточка по фото с телефона: initial → поля/фото/вендор — OK")
    card.close()

    # 2) Кнопка «🔍 Этикетка»: подменяем диалог съёмки, анализ — настоящий
    #    PhotoIntelWorker на реальном фото D-Link.
    class FakeCapture(QDialog):
        def __init__(self, *a, **k):
            super().__init__()
            self.photo_path = str(REAL_LABEL)

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted.value

    card2 = DeviceCardDialog(
        repo, session.id, paths, location=location, initial={"device_type": "camera"}
    )
    card2.serial_edit.setText("РУЧНОЙ")
    card_module.PhotoCaptureDialog = FakeCapture
    card2._label_photo()
    assert str(REAL_LABEL) in card2._pending_photos
    assert card2._intel_worker is not None
    card2._intel_worker.wait(120_000)
    # Сигнал приходит в очередь основного потока — прокачиваем её.
    deadline = time.time() + 15
    while time.time() < deadline and card2._intel_worker is not None:
        app.processEvents()
        time.sleep(0.05)
    app.processEvents()
    assert card2._intel_worker is None, "воркер не завершился"
    assert card2.serial_edit.text() == "РУЧНОЙ"  # ручной ввод не перезаписан
    assert card2.mac_edit.text() == "A0AB1B4003E0", card2.mac_edit.text()
    assert card2.manufacturer_edit.text() == "D-Link", card2.manufacturer_edit.text()
    assert card2.label_btn.isEnabled()
    print("2) Кнопка «🔍 Этикетка» (реальный анализ фото): заполнение — OK")

    # 3) Ничего не распознано → сообщение, поля не тронуты.
    card_module.QMessageBox.information = lambda *a, **k: None
    card3 = DeviceCardDialog(
        repo, session.id, paths, location=location, initial={"device_type": "camera"}
    )
    card3.mac_edit.setText("existing")
    card3._on_label_intel(PhotoIntel(ok=True), str(photo))
    assert card3.mac_edit.text() == "existing"
    assert card3.photo_label.text()  # подпись-подсказка показана
    print("3) Пустой результат: без изменений полей — OK")
    for c in (card2, card3):
        c.close()

    # 4) Сценарий «фото с телефона → создать устройство» на SurveyScreen.
    import inventory.ui.survey_screen as survey_module
    from inventory.services.voice import VoiceService
    from inventory.ui.survey_screen import SurveyScreen

    class FakeCardOK(QDialog):
        instances: list["FakeCardOK"] = []

        def __init__(self, repo_, session_id_, paths_, voice_=None, **kwargs):
            super().__init__()
            self.repo = repo_
            self.session_id = session_id_
            self.initial = kwargs.get("initial") or {}
            self.device = None
            FakeCardOK.instances.append(self)

        def exec(self) -> int:
            # Создаём устройство так, как это делает настоящая карточка.
            self.device = self.repo.create_device(
                self.session_id,
                self.initial.get("device_type", "camera"),
                location.id,
                serial=self.initial.get("serial"),
                mac=self.initial.get("mac"),
                photo_path=self.initial.get("photo_path"),
                note=self.initial.get("note_text"),
            )
            return QDialog.DialogCode.Accepted.value

    survey_module.beep_ok = lambda: None
    survey_module.beep_error = lambda: None
    survey_module.beep_scan = lambda: None
    survey_module.QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    survey_module.DeviceCardDialog = FakeCardOK

    config.last_location_id = location.id
    config.voice.enabled = False
    voice = VoiceService(config.voice)
    screen = SurveyScreen(repo, config, paths, session, voice)
    screen.resize(1366, 768)
    screen.show()
    app.processEvents()

    intel = PhotoIntel(
        serials=["S3CF1G9900566"],
        macs=["A0AB1B4003E0"],
        ok=True,
    )
    count_before = repo.count_devices(session.id)
    screen._on_photo_creation_intel(intel, str(REAL_LABEL))
    app.processEvents()
    assert repo.count_devices(session.id) == count_before + 1
    from inventory.core.parser import normalize_mac

    created = next(
        d for d in repo.list_devices(session.id)
        if d.mac == normalize_mac("A0AB1B4003E0") and d.serial == "S3CF1G9900566"
    )
    assert screen.active_device is not None and screen.active_device.id == created.id
    assert str(REAL_LABEL) in (created.photo_path or "")
    FakeCardOK.instances.clear()

    # 4b) Пустой разбор: устройство НЕ создаётся.
    screen2 = SurveyScreen(repo, config, paths, session, voice)
    count_before_b = repo.count_devices(session.id)
    screen2._on_photo_creation_intel(PhotoIntel(ok=True), str(REAL_LABEL))
    app.processEvents()
    assert repo.count_devices(session.id) == count_before_b
    screen.close()
    screen2.close()
    print("4) «Фотола с телефона → создание устройства» на экране обхода — OK")

    voice.close()
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# tools/ui_rack_smoke.py
"""Офскрин-проверка v1.6.0: диалог «Стойка по фото (ИИ)» и создание пачкой."""
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

from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402

import inventory.ui.rack_dialog as rack_module  # noqa: E402
import inventory.ui.survey_screen as survey_module  # noqa: E402
from inventory.config import AppConfig, data_paths  # noqa: E402
from inventory.core.db import Database  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.services.rack_ai import RackAnalysis, RackDevice  # noqa: E402
from inventory.ui.rack_dialog import RackDialog  # noqa: E402

CANNED = RackAnalysis(
    ok=True,
    model_used="qwen2.5vl:3b",
    elapsed_s=95.0,
    devices=[
        RackDevice(
            type_code="switch", brand="D-Link",
            model="DES-1210-10/ME/B1A", position="U5", detail="24 порта PoE",
        ),
        RackDevice(
            type_code="ups", brand="APC", model="", position="U9",
            detail="корпус с индикаторами",
        ),
    ],
)


def main() -> int:
    app = QApplication([])
    # Офскрин: всплывающие окна не блокируют поток проверки.
    rack_module.QMessageBox.information = lambda *a, **k: None
    rack_module.QMessageBox.warning = lambda *a, **k: None

    config = AppConfig(data_dir=tempfile.mkdtemp(prefix="inventory_rack_"))
    paths = data_paths(config)
    db = Database(paths["db"])
    repo = InventoryRepository(db)
    session = repo.create_session("Тест", "Тестировщик")
    location = repo.create_location(session.id, "2", "204", "Корпус", "Шкаф-секция")
    repo.set_setting("contractors", "[]")

    # 1) Диалог открывается: статус ollama живой (модель установлена в системе).
    class FakeCaptureClosed(QDialog):
        def __init__(self, *a, **k):
            super().__init__()
            self.photo_path = None

        def exec(self) -> int:
            return QDialog.DialogCode.Rejected.value

    rack_module.PhotoCaptureDialog = FakeCaptureClosed
    dialog = RackDialog(repo, session.id, paths, location=location)
    status = dialog.status_label.text()
    assert "готова" in status, status  # ollama + vision-модель найдены
    assert "qwen2.5vl" in status, status
    assert dialog.table.rowCount() == 0
    print("1) Диалог стойки: ollama найдена, статус «готова» — OK")

    # 2) Инъекция canned-результата → таблица заполнилась.
    dialog._on_analysis(CANNED, "canned.jpg")
    assert dialog._worker is None
    assert dialog.table.rowCount() == 2
    assert dialog.create_btn.isEnabled()
    assert "Создать выбранные (2)" in dialog.create_btn.text()
    combo = dialog.table.cellWidget(0, 1)
    assert combo.currentData() == "switch"
    assert dialog.table.item(0, 2).text() == "D-Link DES-1210-10/ME/B1A"
    # Позиция сохранена как есть (U5, без съеденного U).
    assert dialog.table.item(0, 3).text() == "U5"
    print("2) Canned-разбор → таблица из 2 строк, чекбоксы и подпись кнопки — OK")

    # 3) Сняли галочку со второй строки → создаётся одно устройство.
    from PyQt6.QtCore import Qt

    dialog.table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
    count_before = repo.count_devices(session.id)
    dialog._create_selected()
    assert repo.count_devices(session.id) == count_before + 1
    assert dialog.created == 1
    created_device = dialog.created_devices[0]
    assert created_device.device_type == "switch"
    assert created_device.manufacturer == "D-Link", created_device.manufacturer
    assert created_device.model == "DES-1210-10/ME/B1A", created_device.model
    assert created_device.power_type == "220V", created_device.power_type
    assert created_device.is_verified == 0
    assert "В стойке: U5" in created_device.note
    assert "24 порта PoE" in created_device.note
    assert "ИИ-черновик" in created_device.note
    assert dialog.result() == QDialog.DialogCode.Accepted.value
    print("3) Пачка: снята галочка → созданное устройство с брендом/моделью/note — OK")
    dialog.close()

    # 4) Ручная строка со свободной подписью: модель не записывается…
    dialog2 = RackDialog(repo, session.id, paths, location=location)
    dialog2._on_analysis(CANNED, "canned2.jpg")
    assert dialog2.table.rowCount() == 2
    dialog2.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    count_before2 = repo.count_devices(session.id)
    dialog2._create_selected()
    assert repo.count_devices(session.id) == count_before2 + 1
    assert dialog2.created == 1
    ups_device = dialog2.created_devices[0]
    assert ups_device.device_type == "ups"
    assert ups_device.manufacturer == "APC", ups_device.manufacturer
    assert ups_device.model is None, ups_device.model  # модели не читалась
    assert "В стойке: U9" in ups_device.note
    print("4) Юнит без модели: бренд APC сохранён, модель не придумана — OK")
    dialog2.close()

    # 4b) Сортировка таблицы по юниту: U2 < U10, ручное/пустое — в конце.
    dialog3 = RackDialog(repo, session.id, paths, location=location)
    dialog3._on_analysis(
        RackAnalysis(
            ok=True,
            model_used="qwen2.5vl:3b",
            devices=[
                RackDevice(type_code="switch", brand="A", model="S1",
                           position="U10", detail="X"),
                RackDevice(type_code="ups", brand="B", position="U2", detail="Y"),
                RackDevice(type_code="other", detail="полка"),
            ],
        ),
        "canned3.jpg",
    )
    assert dialog3.table.rowCount() == 3
    positions = [dialog3.table.item(r, 3).text() for r in range(3)]
    assert positions == ["U2", "U10", ""], positions
    names = [dialog3.table.item(r, 2).text() for r in range(3)]
    assert names[0] == "B" and names[1] == "A S1" and names[2] == "полка"
    print("4b) Сортировка по юнитам: U2, U10, без позиции в конце — OK")
    dialog3.close()

    # 4c) Одинаковые юниты на разных позициях НЕ считаются дублями,
    #     а повторный кадр той же позиции не науськает строку.
    dialog4 = RackDialog(repo, session.id, paths, location=location)
    dialog4._on_analysis(
        RackAnalysis(
            ok=True,
            devices=[
                RackDevice(type_code="switch", brand="Hikvision", model="DS-3T",
                           position="U5", detail="панель портов"),
                RackDevice(type_code="switch", brand="Hikvision", model="DS-3T",
                           position="U6", detail="панель портов"),
            ],
        ),
        "c4.jpg",
    )
    assert dialog4.table.rowCount() == 2  # U5 и U6 — оба выжили
    dialog4._on_analysis(
        RackAnalysis(
            ok=True,
            devices=[
                RackDevice(type_code="switch", brand="Hikvision", model="DS-3T",
                           position="U5", detail="панель портов"),
            ],
        ),
        "c5.jpg",
    )
    assert dialog4.table.rowCount() == 2  # повторный U5 — дубль, один раз
    print("4c) Дубликаты юнитов: U5+U6 сохранены, повторный U5 погашен — OK")
    dialog4.close()

    # 5) Кнопка «🖥 Стойка» на экране обхода: ensure_location + активное устройство.
    from inventory.services.voice import VoiceService

    class FakeRackOK(QDialog):
        instances: list["FakeRackOK"] = []

        def __init__(self, repo_, session_id_, paths_, voice_=None, **kwargs):
            super().__init__()
            self.repo = repo_
            self.session_id = session_id_
            self.location = kwargs.get("location")
            self.created = 0
            self.created_devices: list = []
            FakeRackOK.instances.append(self)

        def exec(self) -> int:
            d1 = self.repo.create_device(
                self.session_id, "switch", self.location.id, model="SW-A",
            )
            d2 = self.repo.create_device(
                self.session_id, "nvr", self.location.id, model="NVR-A",
            )
            self.created_devices = [d1, d2]
            self.created = 2
            return QDialog.DialogCode.Accepted.value

    survey_module.beep_ok = lambda: None
    survey_module.beep_error = lambda: None
    survey_module.beep_scan = lambda: None
    survey_module.RackDialog = FakeRackOK

    config.last_location_id = location.id
    config.voice.enabled = False
    voice = VoiceService(config.voice)
    screen = survey_module.SurveyScreen(repo, config, paths, session, voice)
    screen.resize(1366, 768)
    screen.show()
    app.processEvents()
    count_before5 = repo.count_devices(session.id)
    screen.open_rack_dialog()
    app.processEvents()
    assert repo.count_devices(session.id) == count_before5 + 2
    last_created = FakeRackOK.instances[-1].created_devices[-1]
    assert screen.active_device is not None
    assert screen.active_device.id == last_created.id
    assert screen.rack_btn.isEnabled()
    screen.close()
    FakeRackOK.instances.clear()
    print("5) Кнопка «🖥 Стойка» на экране обхода: пачка создана, активное назначено — OK")

    # Дать фоновым воркерам (в т.ч. разогрев) спокойно доработать.
    import inventory.ui.rack_workers as rack_workers_module

    deadline = 20
    while rack_workers_module._ORPHANS and deadline > 0:
        for _worker in list(rack_workers_module._ORPHANS):
            _worker.wait(20000)
        app.processEvents()
        deadline -= 1

    voice.close()
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

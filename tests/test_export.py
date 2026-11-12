# tests/test_export.py
from openpyxl import load_workbook

from inventory.core.db import Database
from inventory.core.export_excel import export_session
from inventory.core.repository import InventoryRepository


def test_export_creates_sheets_and_rows(tmp_path):
    db = Database(tmp_path / "test.db")
    repo = InventoryRepository(db)
    session = repo.create_session("Объект №1", "Иванов И.И.")
    location = repo.create_location(session.id, "2", "204", "Корпус А", "2")
    camera = repo.create_device(
        session.id,
        "camera",
        location.id,
        serial="ABC123",
        mac="AA:BB:CC:DD:EE:FF",
        ip="192.168.10.51",
        model="DS-2CD2143G0",
    )
    switch = repo.create_device(
        session.id, "switch", location.id, serial="FDO2134X0ZZ", model="Catalyst 2960X"
    )
    repo.set_connection(camera.id, switch.id, "Gi0/12", "UTP")

    output = export_session(repo, session.id, tmp_path / "report.xlsx")
    assert output.exists()

    wb = load_workbook(output)
    # location в этом тесте создана с rack="2" → лист «Стойки» заполняется.
    assert wb.sheetnames == ["Титул", "Устройства", "Связи", "Стойки", "Сводка", "Проблемы"]

    titul = wb["Титул"]
    assert titul["A2"].value == "Объект №1"
    titul_texts = [
        titul.cell(row=row, column=1).value for row in range(1, titul.max_row + 1)
    ]
    assert "ИТОГИ" in titul_texts
    assert "Инспектор" in titul_texts

    ws = wb["Устройства"]
    assert ws["A1"].value == "ID устройства"
    assert ws.max_row == 3
    values = {ws.cell(row=row, column=1).value: row for row in range(2, ws.max_row + 1)}
    assert "CAM-001" in values
    assert "SW-001" in values

    ws_connections = wb["Связи"]
    assert ws_connections.max_row == 2
    assert ws_connections["A2"].value == "CAM-001"
    assert ws_connections["B2"].value == "SW-001"

    ws_problems = wb["Проблемы"]
    assert ws_problems.max_row >= 2

    db.close()


def test_export_racks_sheet_order_and_columns(tmp_path):
    """Лист «Стойки»: сортировка по юнитам, колонки, устройства без шкафа.

    Юнит — поле локации: позиция шкафа = локация с rack+unit.
    """
    db = Database(tmp_path / "racks.db")
    repo = InventoryRepository(db)
    session = repo.create_session("Объект", "Инспектор")
    loose_loc = repo.create_location(session.id, "1", "101")  # без шкафа

    def _unit_loc(unit: str) -> str:
        return repo.create_location(
            session.id, "2", "204", "Корпус А", "Ш2", unit
        ).id

    ups = repo.create_device(session.id, "ups", _unit_loc("U10"),
                             manufacturer="APC", model="SMT1500", serial="UPS001")
    switch = repo.create_device(session.id, "switch", _unit_loc("U2"),
                                manufacturer="D-Link", model="DES-1210",
                                serial="SW001", mac="AA:BB:CC:DD:EE:01")
    patch = repo.create_device(session.id, "patch", _unit_loc("U5"),
                               model="24 порта")
    loose = repo.create_device(session.id, "camera", loose_loc.id, serial="CAM001")
    free = repo.create_device(session.id, "other", _unit_loc("нижняя часть"))

    output = export_session(repo, session.id, tmp_path / "racks.xlsx")
    wb = load_workbook(output)
    assert wb.sheetnames == [
        "Титул", "Устройства", "Связи", "Стойки", "Сводка", "Проблемы",
    ]

    ws = wb["Стойки"]
    headers = [ws.cell(row=1, column=c).value for c in range(1, 10)]
    assert headers == [
        "Шкаф", "Юнит", "Тип", "Бренд-Модель", "S/N", "MAC", "Статус", "ID", "Локация",
    ]
    ids = [ws.cell(row=r, column=8).value for r in range(2, ws.max_row + 1)]
    # Числовая сортировка юнитов (U2 < U5 < U10), текст — после, без шкафа — нет.
    assert ids == [switch.display_id, patch.display_id, ups.display_id, free.display_id]
    assert loose.display_id not in ids  # устройство без шкафа не попадает на лист
    # Бренд-Модель склеена
    first_row = [ws.cell(row=2, column=c).value for c in range(1, 9)]
    assert first_row[3] == "D-Link DES-1210"
    assert first_row[0] == "Ш2"
    db.close()


def test_export_with_baseline_and_photos(tmp_path):
    from inventory.core.baseline import parse_baseline
    from openpyxl import Workbook

    db = Database(tmp_path / "test2.db")
    repo = InventoryRepository(db)
    session = repo.create_session("Объект №2", "Иванов И.И.")
    location = repo.create_location(session.id, "1", "101", "Корпус Б", "")

    # Прошлая таблица: одна позиция совпадёт, одна «пропадёт».
    past = tmp_path / "past.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["S/N", "MAC", "Модель", "IP"])
    ws.append(["ABC123", "AA:BB:CC:DD:EE:FF", "DS-2CD2143G0", "192.168.10.51"])
    ws.append(["OLD999", "", "Старая камера", ""])
    wb.save(past)
    rows = parse_baseline(past)
    result = repo.import_baseline(session.id, past, rows)

    camera = repo.create_device(
        session.id,
        "camera",
        location.id,
        serial="ABC123",
        mac="AA:BB:CC:DD:EE:FF",
        ip="192.168.10.51",
        model="DS-2CD2143G0",
    )
    photo_path = tmp_path / "photos" / "CAM-001_1.jpg"
    photo_path.parent.mkdir(parents=True, exist_ok=True)
    photo_path.write_bytes(b"\xff\xd8\xffstub")
    repo.add_photo(camera.id, str(photo_path))
    repo.update_device(camera.id, photo_path=str(photo_path))

    output = export_session(repo, session.id, tmp_path / "report2.xlsx")
    wb2 = load_workbook(output)
    assert wb2.sheetnames == ["Титул", "Устройства", "Связи", "Сводка", "Сверка", "Проблемы"]

    ws_compare = wb2["Сверка"]
    verdicts = {ws_compare.cell(row=r, column=1).value for r in range(2, ws_compare.max_row + 1)}
    assert "Совпадает" in verdicts
    assert "Пропало" in verdicts

    ws_devices = wb2["Устройства"]
    photo_cell = ws_devices.cell(row=2, column=22)
    assert photo_cell.value == "CAM-001_1.jpg"
    assert photo_cell.hyperlink is not None

    assert result["total"] == 2
    db.close()


def test_export_nvr_channel_map(tmp_path):
    db = Database(tmp_path / "nvr.db")
    repo = InventoryRepository(db)
    session = repo.create_session("Объект №3", "Петров П.П.")
    location = repo.create_location(session.id, "1", "102", "Корпус В", "")

    nvr = repo.create_device(
        session.id, "nvr", location.id, serial="NVR0001", model="DS-8608NI"
    )
    repo.create_device(
        session.id, "camera", location.id, serial="CAMSER1", nvr_channel="D2"
    )
    cam2 = repo.create_device(
        session.id, "camera", location.id, serial="CAMSER2"
    )
    # cam2 привязана связью к регистратору, порт = канал.
    repo.set_connection(cam2.id, nvr.id, "3", "UTP")

    output = export_session(repo, session.id, tmp_path / "nvr.xlsx")
    wb = load_workbook(output)
    # Лист появился, потому что есть каналы.
    assert "Регистраторы" in wb.sheetnames
    ws = wb["Регистраторы"]
    rows = {
        ws.cell(row=r, column=3).value: (
            ws.cell(row=r, column=1).value,
            ws.cell(row=r, column=2).value,
        )
        for r in range(2, ws.max_row + 1)
    }
    assert rows["CAM-001"] == ("NVR-001", "D2")  # единственный NVR + канал из поля
    assert rows["CAM-002"] == ("NVR-001", "3")   # канал из связи с регистратором

    # Канал тоже в листе «Устройства».
    ws_devices = wb["Устройства"]
    channel_col = next(
        c for c in range(1, ws_devices.max_column + 1)
        if ws_devices.cell(row=1, column=c).value == "Канал NVR"
    )
    assert ws_devices.cell(row=2, column=channel_col).value == "D2"
    db.close()


def test_nvr_channel_create_and_update(tmp_path):
    db = Database(tmp_path / "ch.db")
    repo = InventoryRepository(db)
    session = repo.create_session("Объект №4", "Сидоров С.С.")
    location = repo.create_location(session.id, "1", "10", "", "")

    device = repo.create_device(session.id, "camera", location.id, nvr_channel="D5")
    assert device.nvr_channel == "D5"
    device = repo.update_device(device.id, nvr_channel="D7")
    assert device.nvr_channel == "D7"
    device = repo.update_device(device.id, nvr_channel="   ")
    assert device.nvr_channel is None
    db.close()

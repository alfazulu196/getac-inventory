# -*- coding: utf-8 -*-
"""Генератор демо-сессии для репетиции обхода (без реального железа).

Запуск из корня проекта:
    python tools/make_demo.py           # демо в data\\inventory.db
    python tools/make_demo.py --db путь # другая база

Что делает:
  1. Удаляет прошлую демо-сессию («Демо-репетиция») — данные каскадно.
  2. Создаёт новую: 2 корпуса, 6 локаций, ~30 устройств (камеры, коммутаторы,
     регистраторы, ИБП, патч-панель), связи «камера → коммутатор → ядро»,
     каналы NVR, проблемные статусы, устройства без S/N.
  3. Рядом с базой кладёт «прошлую инвентаризацию»
     (data\\rehearsal\\прошлая_инвентаризация.xlsx) для репетиции сверки:
     в ней есть совпадения, изменения, пропавшие и «не сверяемые» записи.

Повторный запуск пересоздаёт демо-сессию с тем же содержимым.
Если в базе есть другая активная сессия — скрипт откажется работать
(завершите её или используйте --db со свежим файлом).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from inventory.core.db import Database  # noqa: E402
from inventory.core.models import Device  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402

DEMO_NAME = "Демо-репетиция"
DEMO_OBJECT = "Демо-объект (репетиция)"
DEMO_INSPECTOR = "Инспектор-репетиция"
BASELINE_SUBDIR = "rehearsal"
BASELINE_FILE = "прошлая_инвентаризация.xlsx"
TYPE_NAMES = {
    "camera": "IP-камера",
    "switch": "Коммутатор",
    "nvr": "Регистратор",
    "dvr": "Регистратор",
    "router": "Маршрутизатор",
    "ap": "Точка доступа",
    "ups": "ИБП",
    "server": "Сервер",
    "patch": "Патч-панель",
}


def _mac(offset: int) -> str:
    return (
        f"A0:B1:C4:{(offset >> 8) & 0xFF:02X}:"
        f"{(offset >> 4) & 0xFF:02X}:{offset & 0xFF:02X}"
    )


def build_demo(db_path: Path) -> dict[str, Path | int]:
    """Создаёт демо-сессию. Возвращает счётчики и путь к прошлой таблице."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(db_path)
    repo = InventoryRepository(db)

    # Убираем прошлые демо-сессии (все с этим именем) — данные удалятся каскадно.
    old = [row["id"] for row in db.fetchall("SELECT id FROM sessions WHERE name = ?", (DEMO_NAME,))]
    if old:
        with db.conn:
            db.executemany("DELETE FROM sessions WHERE id = ?", [(i,) for i in old])

    active = repo.get_active_session()
    if active is not None:
        raise SystemExit(
            f"В базе есть активная сессия «{active.name}» — демо нельзя создать "
            "поверх неё. Завершите активную сессию в приложении или запустите "
            "демо в другой базе (--db путь к новому файлу)."
        )

    session = repo.create_session(
        DEMO_OBJECT,
        DEMO_INSPECTOR,
        name=DEMO_NAME,
        comment="Сгенерировано tools/make_demo.py для репетиции, без реального объекта.",
        contractor="Демо-подрядчик ООО",
    )
    repo.remember_contractor("Демо-подрядчик ООО")

    # ------------------------------------------------------------------
    # Локации
    # ------------------------------------------------------------------
    r1 = repo.create_location(session.id, "1", "101", "Корпус А", "Ш-1")
    r2 = repo.create_location(session.id, "1", "102", "Корпус А", "Ш-2")
    street = repo.create_location(session.id, "1", "Улица", "Корпус А", "Мачта М-1")
    f2a = repo.create_location(session.id, "2", "204", "Корпус А", "Ш-3")
    server_loc = repo.create_location(session.id, "2", "210", "Корпус А", "Ш-4", unit="12")
    b1 = repo.create_location(session.id, "1", "15", "Корпус Б", "Щ-1")

    # ------------------------------------------------------------------
    # Серверная: ядро сети + регистраторы
    # ------------------------------------------------------------------
    router = repo.create_device(
        session.id, "router", server_loc.id,
        serial="KN1111GIGA", mac=_mac(0x010101), ip="192.168.10.1",
        model="Keenetic Giga",
    )
    core_switch = repo.create_device(
        session.id, "switch", server_loc.id,
        serial="FDO2134X0ZZ", mac=_mac(0x01F101), ip="192.168.10.2",
        model="Catalyst 2960X-48", note="Ядро, 48 портов PoE+",
    )
    nvr_big = repo.create_device(
        session.id, "nvr", server_loc.id,
        serial="DS8664NIK8", mac=_mac(0x01F201), ip="192.168.20.10",
        model="DS-8664NI-K8 (64 канала)",
    )
    nvr_small = repo.create_device(
        session.id, "nvr", server_loc.id,
        serial="DS7608NIK2", mac=_mac(0x01F202), ip="192.168.20.11",
        model="DS-7608NI-K2 (8 каналов)",
    )
    repo.create_device(
        session.id, "ups", server_loc.id, serial="APC1500VA",
        model="Back-UPS 1500", note="Батарея менялась в 2024",
    )
    repo.create_device(
        session.id, "server", server_loc.id, serial="SRVDEMO0001",
        ip="192.168.10.3", model="Dell T430",
    )
    repo.create_device(
        session.id, "patch", server_loc.id,
        model="Патч-панель 24", status="installed",
    )  # без S/N и MAC — попадает в «Проблемы» и «Не сверено» сверки
    repo.set_connection(core_switch.id, router.id, "Te1/0/1", "UTP")

    # ------------------------------------------------------------------
    # Коммутаторы этажей
    # ------------------------------------------------------------------
    sw_r1 = repo.create_device(
        session.id, "switch", r1.id, serial="DS3E0326P01",
        mac=_mac(0x01F301), ip="192.168.10.4",
        model="DS-3E0326P-E (PoE)",
    )
    sw_r2 = repo.create_device(
        session.id, "switch", r2.id, serial="DS3E0326P02",
        mac=_mac(0x01F302), ip="192.168.10.5",
        model="DS-3E0326P-E (PoE)",
    )
    sw_b1 = repo.create_device(
        session.id, "switch", b1.id, serial="FDO4455X0ZZ",
        mac=_mac(0x01F303), ip="192.168.30.2",
        model="Catalyst 2960-24",
    )
    repo.set_connection(sw_r1.id, core_switch.id, "Gi1/0/5", "UTP")
    repo.set_connection(sw_r2.id, core_switch.id, "Gi1/0/6", "UTP")
    repo.set_connection(sw_b1.id, core_switch.id, "Gi1/0/7", "Оптика")

    # ------------------------------------------------------------------
    # Камеры этажа 1 (каналы NVR_R-001: D1..D6)
    # ------------------------------------------------------------------
    floor1_models = [
        "DS-2CD2143G0-I", "DS-2CD2T47G2-LU", "DS-2CD2143G0-I",
        "DS-2CD2T47G2-LU", "DS-2CD2143G0-I", "DS-2CD2143G0-I",
    ]
    floor1_cameras: list[Device] = []
    for i, model in enumerate(floor1_models, start=1):
        cam = repo.create_device(
            session.id, "camera", (r1 if i % 2 else r2).id,
            serial=f"4520{i:09d}", mac=_mac(0x2000 + i),
            ip=f"192.168.10.{20 + i}", model=model, nvr_channel=f"D{i}",
            note="Съёмка ВДТ-стойла, смотреть с лестницы" if i % 3 == 0 else None,
        )
        floor1_cameras.append(cam)
        repo.set_connection(cam.id, sw_r1.id if i % 2 else sw_r2.id, f"Gi0/{i}", "UTP")

    # Проблемные камеры этажа 1
    broken = repo.create_device(
        session.id, "camera", r1.id,
        serial="452000000009", mac=_mac(0x2051), ip="192.168.10.29",
        model="DS-2CD2143G0-I", status="faulty",
        note="Индикации нет, не отвечает. Проверить PoE на порту.",
        nvr_channel="D7",
    )
    broken2 = repo.create_device(
        session.id, "camera", r1.id,
        serial="452000000010", mac=_mac(0x2052), ip="192.168.10.30",
        model="DS-2CD2T47G2-LU", status="replace",
        note="Разбит купол, корпус вскрыт.",
        nvr_channel="D8",
    )
    repo.set_connection(broken.id, sw_r1.id, "Gi0/9", "UTP")
    repo.set_connection(broken2.id, sw_r1.id, "Gi0/10", "UTP")

    repo.create_device(  # CAM-009: без MAC и без связи → явно в «Проблемах»
        session.id, "camera", street.id, serial="452000000011",
        status="missing",
        note="По плану камера на мачте М-1 — на корпусе не найдена.",
    )

    # ------------------------------------------------------------------
    # Камеры этажа 2 (каналы D9..D12)
    # ------------------------------------------------------------------
    floor2_models = ["DS-2CD2143G0-I", "DH-IPC-HDBW4433R", "DS-2CD2T47G2-LU", "DS-2CD2143G0-I"]
    floor2_cameras: list[Device] = []
    for i, model in enumerate(floor2_models, start=1):
        cam = repo.create_device(
            session.id, "camera", f2a.id,
            serial=f"4520{(i + 10):09d}", mac=_mac(0x2100 + i),
            ip=f"192.168.20.{20 + i}", model=model, nvr_channel=f"D{8 + i}",
            note="Купол затёрт" if i == 3 else None,
        )
        floor2_cameras.append(cam)
        repo.set_connection(cam.id, sw_r2.id, f"Gi0/{i}", "UTP")

    on_nvr2 = repo.create_device(
        session.id, "camera", f2a.id,
        serial="DH4L000000020", mac=_mac(0x2141), ip="192.168.20.30",
        model="DH-IPC-HDBW4433R", nvr_channel="D1",
    )
    repo.set_connection(on_nvr2.id, nvr_small.id, "D1")
    repo.set_connection(nvr_small.id, core_switch.id, "Gi1/0/8", "UTP")
    repo.set_connection(nvr_big.id, core_switch.id, "Gi1/0/9", "UTP")

    repo.create_device(  # CAM-015: «Без S/N и MAC»
        session.id, "camera", f2a.id,
        model="Без маркировки (ноунейм)",
        note="Этикетки нет, IP читается по экрану",
    )

    # ------------------------------------------------------------------
    # Корпус Б: камеры + EAN-кейс + демонтированная
    # ------------------------------------------------------------------
    b_models = ["DS-2CD2T47G2-LU", "DH-IPC-HDBW4433R", "DS-2CD2143G0-I"]
    b_cameras: list[Device] = []
    for i, model in enumerate(b_models, start=1):
        cam = repo.create_device(
            session.id, "camera", b1.id,
            serial=f"4530{(i + 20):09d}", mac=_mac(0x3000 + i),
            ip=f"192.168.30.{20 + i}", model=model, nvr_channel=f"D{10 + i}",
            note="Вход Б, съёмка с лестницы" if i == 2 else None,
        )
        b_cameras.append(cam)
        repo.set_connection(cam.id, sw_b1.id, f"Fa0/{i}", "UTP")

    repo.create_device(  # CAM-019: EAN с коробки
        session.id, "camera", b1.id,
        serial="453000000024", mac=_mac(0x3024), ip="192.168.30.24",
        model="DS-2CD2143G0-I", nvr_channel="D13",
        scanned_code="3800012345678", code_type="ean",
        note="EAN-код считан с коробки — этикетка S/N плохо читается.",
    )
    repo.create_device(  # CAM-020: демонтирована
        session.id, "camera", server_loc.id,
        serial="4520199999991", mac=_mac(0x2110),
        model="DS-2CD2143G0-I", status="dismantled",
        note="Снята, лежит в серверной. Планируется перенос.",
    )

    # ------------------------------------------------------------------
    # Точки доступа
    # ------------------------------------------------------------------
    for offset, location, switch, ip in (
        (0x4001, r1, sw_r1, "192.168.10.40"),
        (0x4002, f2a, sw_r2, "192.168.20.40"),
    ):
        ap = repo.create_device(
            session.id, "ap", location.id,
            serial=f"APDEMO{offset & 0xFFFF:04X}", mac=_mac(offset), ip=ip,
            model="UniFi AP AC Lite",
        )
        repo.set_connection(ap.id, switch.id, "Gi0/24", "UTP")

    # ------------------------------------------------------------------
    # «Прошлая инвентаризация» для репетиции сверки
    # ------------------------------------------------------------------
    baseline_dir = db_path.parent / BASELINE_SUBDIR
    baseline_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = baseline_dir / BASELINE_FILE
    _write_baseline(baseline_path, repo, session.id)
    return {
        "devices": len(repo.list_devices(session.id)),
        "locations": len(repo.list_locations(session.id)),
        "baseline": baseline_path,
    }


def _write_baseline(path: Path, repo: InventoryRepository, session_id: str) -> None:
    """«Прошлая таблица» будущего выезда: совпадения + изменения + пропавшие."""
    from openpyxl import Workbook

    devices = {d.display_id: d for d in repo.list_devices(session_id)}

    def row_of(display_id: str, *, ip: str | None = None, loc: str | None = None) -> list:
        d = devices[display_id]
        return [
            display_id,
            d.serial or "",
            d.mac or "",
            ip if ip is not None else (d.ip or ""),
            TYPE_NAMES.get(d.device_type, ""),
            d.model or "",
            loc if loc is not None else (d.location.label() if d.location else ""),
        ]

    # Совпадает как есть.
    same_ids = [
        "CAM-003", "CAM-004", "CAM-005",          # камера этажа 1
        "CAM-007", "CAM-008",                     # «Не работает» и «Замена» — уже были
        "CAM-010", "CAM-011", "CAM-014",          # этаж 2 + камера на NVR-002
        "CAM-016", "CAM-017", "CAM-019",          # корпус Б + EAN-кейс
        "SW-001", "SW-002", "SW-003", "SW-004",   # коммутаторы
        "NVR-002",                                # регистратор (NVR-001 — в «изменённых»)
        "UPS-001", "RTR-001", "SRV-001",          # ибп, роутер, сервер
    ]
    rows = [row_of(display_id) for display_id in same_ids]

    # Изменился: старые IP и места — сверка покажет разницу.
    rows.extend([
        row_of("CAM-001", ip="192.168.10.99"),
        row_of("CAM-002", loc="Старая мачта у входа"),
        row_of("CAM-012", ip="192.168.20.99"),
        row_of("NVR-001", ip="192.168.20.100"),
    ])

    # Пропало: прошлый выезд фиксировал оборудование, которого теперь нет.
    rows.extend([
        ["OLD-1", "4520099900001", "A0:B1:C4:90:00:01", "192.168.10.90",
         "IP-камера", "Снята в 2023", "Мачта 2"],
        ["OLD-2", "4520099900002", "A0:B1:C4:90:00:02", "192.168.10.91",
         "IP-камера", "Снята в 2023", "Мачта 3"],
        ["OLD-3", "FDO999900001", "A0:B1:C4:90:00:03", "",
         "Коммутатор", "Снятый хаб", "Пом. 100"],
        ["OLD-4", "DH0000000001", "A0:B1:C4:90:00:04", "192.168.20.90",
         "IP-камера", "Снята", "Пом. 205"],
        ["OLD-5", "4520099900005", "A0:B1:C4:90:00:05", "192.168.30.90",
         "IP-камера", "Снята", "Корпус Б, вход"],
    ])

    wb = Workbook()
    ws = wb.active
    ws.title = "Отчёт"
    ws.append(["ID камеры", "S/N", "MAC", "IP", "Тип", "Модель", "Место"])
    for data in rows:
        ws.append(data)
    wb.save(str(path))


def main(argv: list[str] | None = None) -> int:
    # Консоль Windows может не уметь в стрелки/эмодзи — пусть не падает печать.
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    parser = argparse.ArgumentParser(description="Демо-сессия для репетиции обхода")
    parser.add_argument(
        "--db", default=str(ROOT / "data" / "inventory.db"),
        help="путь к базе SQLite (по умолчанию data\\inventory.db)",
    )
    args = parser.parse_args(argv)
    try:
        result = build_demo(Path(args.db))
    except SystemExit as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1
    print()
    print(f"Демо-сессия создана: «{DEMO_NAME}»")
    print(f"  устройств: {result['devices']}, локаций: {result['locations']}")
    print()
    print("Как репетировать:")
    print("  1. Запустите приложение (python run_inventory.py или exe).")
    print("  2. На стартовом экране — «Продолжить обход» (сессия «Демо-репетиция»).")
    print("  3. Пройдите цикл: локация → ручной ввод кода → карточка → сохранить.")
    print("     Дубли: повторите код CAM-001 — проверьте окно «Возможный дубль».")
    print("  4. Завершение: «Завершить обход» → «🗂 Сверить с прошлой таблицей…» →")
    print(f"     выбрать {result['baseline']}")
    print("  5. «Экспорт Excel» → откройте листы «Сверка» и «Регистраторы».")
    print()
    print("Повторный запуск скрипта пересоздаст демо-сессию заново.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

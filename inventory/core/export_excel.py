# inventory/core/export_excel.py
"""Формирование Excel-отчёта по сессии."""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from inventory.config import APP_VERSION, STATUS_BY_CODE, device_type_name, status_name
from inventory.core.baseline import compare_baseline, parse_baseline
from inventory.core.repository import InventoryRepository

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
SECTION_FILL = PatternFill("solid", fgColor="D9E2F3")
THIN_BORDER = Border(
    left=Side(style="thin", color="BFBFBF"),
    right=Side(style="thin", color="BFBFBF"),
    top=Side(style="thin", color="BFBFBF"),
    bottom=Side(style="thin", color="BFBFBF"),
)
SWERKA_FILLS = {
    "Совпадает": PatternFill("solid", fgColor="2ECC71"),
    "Изменился": PatternFill("solid", fgColor="E67E22"),
    "Новое": PatternFill("solid", fgColor="F1C40F"),
    "Пропало": PatternFill("solid", fgColor="E74C3C"),
    "Не сверено": PatternFill("solid", fgColor="95A5A6"),
}

DEVICE_HEADERS = [
    "ID устройства",
    "Инвентарный номер",
    "Тип устройства",
    "Подтип",
    "Производитель",
    "Модель",
    "Серийный номер (S/N)",
    "MAC-адрес",
    "IP-адрес",
    "Здание",
    "Этаж",
    "Помещение",
    "Шкаф",
    "Юнит",
    "Место установки",
    "Подключен к",
    "Порт",
    "Канал NVR",
    "Способ питания",
    "Статус",
    "Примечание",
    "Фото",
    "Код сканирования",
    "Тип кода",
    "Проверено",
    "Дата инвентаризации",
    "Инспектор",
]

COMPARE_HEADERS = [
    "Результат",
    "ID (текущий)",
    "ID (прошлый)",
    "Тип",
    "Модель",
    "S/N",
    "MAC",
    "IP",
    "Локация",
    "Что изменилось",
]


def _style_header(ws) -> None:
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = THIN_BORDER
    ws.row_dimensions[1].height = 32


def _autosize(ws, max_width: int = 50) -> None:
    for column in ws.columns:
        letter = get_column_letter(column[0].column)
        max_len = 0
        for cell in column:
            value = "" if cell.value is None else str(cell.value)
            max_len = max(max_len, max(len(line) for line in value.split("\n")) if value else 0)
        ws.column_dimensions[letter].width = min(max(max_len + 3, 10), max_width)


def _safe_sheet_name(name: str) -> str:
    cleaned = re.sub(r"[\\/*?:\[\]]", "_", name).strip()
    return cleaned[:31] or "Лист"


def _tint(hex_color: str, factor: float = 0.82) -> str:
    """Смягчить цвет для фона строки (ближе к белому)."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return "FFFFFF"
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (
        round(int(ch + (255 - int(ch)) * factor)) for ch in (r, g, b)
    )
    return f"{r:02X}{g:02X}{b:02X}"


def _write_table(ws, headers: list[str], rows: list[list[Any]]) -> None:
    ws.append(headers)
    for row in rows:
        ws.append(row)
    _style_header(ws)
    ws.freeze_panes = "A2"
    if ws.max_row >= 1:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(ws.max_row, 1)}"
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=False)
            cell.border = THIN_BORDER


def _fill_titul(ws, session, summary: dict, comparison, repo=None) -> None:
    """Титульный лист «для заказчика»: шапка, итоги, подписи."""
    ws.merge_cells("A1:E1")
    ws["A1"] = "ОТЧЁТ ПО ИНВЕНТАРИЗАЦИИ ОБОРУДОВАНИЯ"
    ws["A1"].font = Font(bold=True, size=18)
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 32
    ws["A2"] = session.object_name or ""
    ws["A2"].font = Font(bold=True, size=14)
    ws["A2"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.merge_cells("A2:E2")

    row_index = 4
    header_row = [
        ("Инспектор", session.inspector),
        ("Сессия", session.name),
        ("Начата", session.started_at),
        ("Завершена", session.finished_at or "сессия ещё активна"),
        ("Отчёт сформирован", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Приложение", f"Инвентаризация объекта v{APP_VERSION}"),
    ]
    contractor = (getattr(session, "contractor", None) or "").strip()
    if repo is not None:
        org = str(repo.get_setting("report_org") or "").strip()
        contract = str(repo.get_setting("report_contract") or "").strip()
        if contractor or org:
            header_row.insert(1, ("Организация (подрядчик)", contractor or org))
        if contract:
            header_row.append(("Договор", contract))
    for label, value in header_row:
        label_cell = ws.cell(row=row_index, column=1, value=label)
        label_cell.font = Font(bold=True)
        label_cell.border = THIN_BORDER
        value_cell = ws.cell(row=row_index, column=2, value=value)
        value_cell.border = THIN_BORDER
        if value and len(str(value)) > 60:
            value_cell.alignment = Alignment(vertical="top", wrap_text=True)
        row_index += 1

    row_index += 1
    section_cell = ws.cell(row=row_index, column=1, value="ИТОГИ")
    section_cell.font = Font(bold=True, size=13)
    section_cell.fill = SECTION_FILL
    row_index += 1

    counts: list[tuple[str, Any]] = [
        ("Всего устройств", summary.get("total", 0)),
        ("Без S/N и MAC", summary.get("no_serial_mac", 0)),
        ("Камеры/точки без связи", summary.get("no_connection", 0)),
        ("Без фото", summary.get("no_photo", 0)),
        ("Не проверено", summary.get("unverified", 0)),
        ("Групп дублей", summary.get("duplicate_groups", 0)),
    ]
    if comparison is not None:
        c = comparison.counts
        counts += [
            ("Сверка: совпадает", c.get("matched", 0)),
            ("Сверка: изменился", c.get("changed", 0)),
            ("Сверка: новое", c.get("new", 0)),
            ("Сверка: пропало", c.get("missing", 0)),
            ("Сверка: не сверено", c.get("no_device_id", 0)),
        ]
    for label, value in counts:
        ws.cell(row=row_index, column=1, value=label)
        value_cell = ws.cell(row=row_index, column=2, value=value)
        value_cell.font = Font(bold=True)
        row_index += 1

    row_index += 1
    ws.cell(
        row=row_index + 1, column=1,
        value="Инспектор: ____________________    Дата: ____________",
    )
    ws.cell(
        row=row_index + 2, column=1,
        value="Заказчик: ______________________    Дата: ____________",
    )
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 56


def _export_path_with_fallback(path: Path) -> Path:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return path


def export_session(
    repo: InventoryRepository,
    session_id: str,
    output_path: str | Path,
) -> Path:
    """Экспортирует сессию в Excel. При занятом файле создаёт копию."""
    session = repo.get_session(session_id)
    if not session:
        raise ValueError("Сессия не найдена")

    output_path = Path(output_path)
    if output_path.suffix.lower() != ".xlsx":
        output_path = output_path.with_suffix(".xlsx")

    devices = repo.list_devices(session_id)
    devices.sort(
        key=lambda d: (
            d.location.label() if d.location else "",
            d.display_id,
        )
    )
    connections = repo.list_connections_view(session_id)
    connection_by_device = {row["device_id"]: row for row in connections}
    summary = repo.get_summary(session_id)

    photos_by_device: dict[str, list[str]] = {}
    for device in devices:
        names = [Path(photo.path).name for photo in repo.list_photos(device.id)]
        if not names and device.photo_path:
            names = [Path(device.photo_path).name]
        photos_by_device[device.id] = names
    photo_paths = {
        photo.path: Path(photo.path).name
        for device in devices
        for photo in repo.list_photos(device.id)
    }
    if not photo_paths and any(d.photo_path for d in devices):
        photo_paths = {
            d.photo_path: Path(d.photo_path).name
            for d in devices
            if d.photo_path
        }

    wb = Workbook()

    # Первый лист — титул; заполним в самом конце, когда известны все цифры.
    ws_titul = wb.active
    ws_titul.title = "Титул"

    # --- Устройства ---
    ws = wb.create_sheet("Устройства")
    rows: list[list[Any]] = []
    for device in devices:
        connection = connection_by_device.get(device.id, {})
        location = device.location
        rows.append(
            [
                device.display_id,
                device.asset_tag or "",
                device_type_name(device.device_type),
                device.subtype or "",
                device.manufacturer or "",
                device.model or "",
                device.serial or "",
                device.mac or "",
                device.ip or "",
                location.building if location else "",
                location.floor if location else "",
                location.room if location else "",
                location.rack if location else "",
                location.unit if location else "",
                location.label() if location else "",
                connection.get("upstream_display_id") or device.upstream_display_id or "",
                connection.get("upstream_port") or device.upstream_port or "",
                device.nvr_channel or "",
                device.power_type or "",
                status_name(device.status),
                device.note or "",
                "; ".join(photos_by_device.get(device.id, [])),
                device.scanned_code or "",
                device.code_type or "",
                "Да" if device.is_verified else "Нет",
                device.created_at,
                session.inspector,
            ]
        )
    _write_table(ws, DEVICE_HEADERS, rows)
    _autosize(ws)

    # Раскраска по статусу, перенос примечаний, ссылки на фото.
    status_col, note_col, photo_col = 19, 20, 21
    for row_cells, device in zip(ws.iter_rows(min_row=2), devices):
        row_cells[note_col].alignment = Alignment(vertical="top", wrap_text=True)
        color = STATUS_BY_CODE.get(device.status, {}).get("color", "#95a5a6")
        row_cells[status_col].fill = PatternFill("solid", fgColor=_tint(color))
        row_cells[note_col].fill = PatternFill("solid", fgColor=_tint(color, 0.94))
        if device.photo_path and row_cells[photo_col].value:
            row_cells[photo_col].hyperlink = device.photo_path
            row_cells[photo_col].font = Font(color="0563C1", underline="single")
    ws.column_dimensions[get_column_letter(note_col + 1)].width = 44
    ws.column_dimensions[get_column_letter(photo_col + 1)].width = 30

    # --- Связи ---
    ws_connections = wb.create_sheet("Связи")
    connection_rows = [
        [
            row.get("device_display_id", ""),
            row.get("upstream_display_id", ""),
            row.get("upstream_port", ""),
            row.get("cable_type", ""),
            row.get("note", ""),
        ]
        for row in connections
    ]
    _write_table(
        ws_connections,
        ["ID устройства", "Вышестоящее устройство", "Порт", "Тип кабеля", "Примечание"],
        connection_rows,
    )
    _autosize(ws_connections)

    # --- Регистраторы: карта каналов ---
    recorder_types = {"nvr", "dvr"}
    recorders = [d for d in devices if d.device_type in recorder_types]
    recorder_by_id = {d.id: d for d in recorders}

    def _channel_key(text: Any) -> int:
        digits = re.sub(r"[^0-9]", "", str(text or ""))
        return int(digits) if digits else 9999

    map_rows: list[list[Any]] = []
    for camera in devices:
        channel = str(camera.nvr_channel or "").strip()
        recorder = None
        if not channel:
            upstream_id = camera.upstream_device_id or (connection_by_device.get(camera.id, {}).get("upstream_device_id"))
            if upstream_id in recorder_by_id:
                recorder = recorder_by_id[upstream_id]
                channel = camera.upstream_port or connection_by_device.get(camera.id, {}).get("upstream_port") or ""
        if not channel:
            continue
        if recorder is None:
            upstream_id = camera.upstream_device_id or (
                connection_by_device.get(camera.id, {}).get("upstream_device_id")
            )
            if upstream_id in recorder_by_id:
                recorder = recorder_by_id[upstream_id]
        if recorder is None and len(recorders) == 1:
            recorder = recorders[0]
        map_rows.append(
            [
                recorder.display_id if recorder else "?",
                channel,
                camera.display_id,
                camera.model or "",
                camera.serial or "",
                camera.ip or "",
                camera.location.label() if camera.location else "",
                status_name(camera.status),
            ]
        )
    if map_rows:
        map_rows.sort(key=lambda row: (str(row[0]), _channel_key(row[1])))
        ws_recorders = wb.create_sheet("Регистраторы")
        _write_table(
            ws_recorders,
            ["Регистратор", "Канал", "Камера", "Модель", "S/N", "IP", "Локация", "Статус"],
            map_rows,
        )
        _autosize(ws_recorders)

    # --- Сводка ---
    ws_summary = wb.create_sheet("Сводка")
    ws_summary.append(["Показатель", "Значение"])
    _style_header(ws_summary)
    summary_rows = [
        ("Объект", session.object_name),
        ("Инспектор", session.inspector),
        ("Сессия", session.name),
        ("Начата", session.started_at),
        ("Завершена", session.finished_at or ""),
        ("Всего устройств", summary["total"]),
        ("Без S/N и MAC", summary["no_serial_mac"]),
        ("Камеры/точки без связи", summary["no_connection"]),
        ("Без фото", summary["no_photo"]),
        ("Не проверено", summary["unverified"]),
        ("Групп дублей", summary["duplicate_groups"]),
    ]
    for label, value in summary_rows:
        ws_summary.append([label, value])

    def section(title: str, items: list[tuple[str, int]]) -> None:
        ws_summary.append([])
        row_index = ws_summary.max_row + 1
        ws_summary.cell(row=row_index, column=1, value=title).font = Font(bold=True)
        ws_summary.cell(row=row_index, column=1).fill = SECTION_FILL
        ws_summary.cell(row=row_index, column=2).fill = SECTION_FILL
        ws_summary.append(["Наименование", "Количество"])
        for name, count in items:
            ws_summary.append([name, count])

    section("По типам", [(item["name"], item["count"]) for item in summary["by_type"]])
    section("По статусам", [(item["name"], item["count"]) for item in summary["by_status"]])
    section("По локациям", [(item["label"], item["count"]) for item in summary["by_location"]])
    for row in ws_summary.iter_rows(min_row=2):
        for cell in row:
            cell.border = THIN_BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=False)
    ws_summary.column_dimensions["A"].width = 48
    ws_summary.column_dimensions["B"].width = 22

    # --- Сверка с прошлой инвентаризацией ---
    comparison = None
    if session.baseline_path:
        baseline_rows = [
            {
                "display_id": row.display_id,
                "device_type": row.device_type,
                "manufacturer": row.manufacturer,
                "model": row.model,
                "serial": row.serial,
                "serial_norm": row.serial_norm or "",
                "mac": row.mac,
                "mac_norm": row.mac_norm or "",
                "ip": row.ip,
                "location_label": row.location_label,
                "status": row.status,
            }
            for row in repo.list_baseline(session_id)
        ]
        if baseline_rows:
            comparison = compare_baseline(baseline_rows, devices)
            ws_compare = wb.create_sheet("Сверка")
            verdict_by_device: dict[str, str] = {}
            compare_rows: list[list[Any]] = []

            def baseline_text(row: dict[str, Any], key: str) -> Any:
                return row.get(key) or ""

            for device, baseline_row, changes in comparison.matched:
                loc = device.location.label() if device.location else ""
                compare_rows.append(
                    ["Совпадает", device.display_id, baseline_text(baseline_row, "display_id"),
                     baseline_text(baseline_row, "device_type"), device.model or "",
                     device.serial or "", device.mac or "", device.ip or "", loc, ""]
                )
                verdict_by_device[device.id] = "Совпадает"
            for device, baseline_row, changes in comparison.changed:
                loc = device.location.label() if device.location else ""
                compare_rows.append(
                    ["Изменился", device.display_id,
                     baseline_text(baseline_row, "display_id"),
                     baseline_text(baseline_row, "device_type"), device.model or "",
                     device.serial or "", device.mac or "", device.ip or "", loc,
                     "; ".join(changes)]
                )
                verdict_by_device[device.id] = "Изменился"
            for device in comparison.new:
                loc = device.location.label() if device.location else ""
                compare_rows.append(
                    ["Новое", device.display_id, "", device_type_name(device.device_type),
                     device.model or "", device.serial or "", device.mac or "",
                     device.ip or "", loc, "Есть сейчас — в прошлой таблице нет"]
                )
                verdict_by_device[device.id] = "Новое"
            for device in devices:
                if device.id in verdict_by_device:
                    continue
                compare_rows.append(
                    ["Не сверено", device.display_id, "", device_type_name(device.device_type),
                     device.model or "", device.serial or "", device.mac or "",
                     device.ip or "", device.location.label() if device.location else "",
                     "Нет S/N и MAC — сравнение невозможно"]
                )
            for row in comparison.missing:
                compare_rows.append(
                    ["Пропало", "", baseline_text(row, "display_id"),
                     baseline_text(row, "device_type"), baseline_text(row, "model"),
                     baseline_text(row, "serial"), baseline_text(row, "mac"),
                     baseline_text(row, "ip"), baseline_text(row, "location_label"),
                     "По прошлой таблице был — в текущем обходе не найден"]
                )
            _write_table(ws_compare, COMPARE_HEADERS, compare_rows)
            _autosize(ws_compare)
            for row_cells in ws_compare.iter_rows(min_row=2):
                fill = SWERKA_FILLS.get(str(row_cells[0].value or ""))
                if fill is not None:
                    row_cells[0].fill = fill
            section(
                "Сверка с прошлой таблицей",
                [
                    ("Совпадает", comparison.counts["matched"]),
                    ("Изменился", comparison.counts["changed"]),
                    ("Новое", comparison.counts["new"]),
                    ("Пропало", comparison.counts["missing"]),
                    ("Не сверено (нет S/N и MAC)", comparison.counts["no_device_id"]),
                ],
            )
            for row in ws_summary.iter_rows(min_row=2):
                for cell in row:
                    cell.border = THIN_BORDER
                    cell.alignment = Alignment(vertical="top", wrap_text=False)

    # --- Проблемы ---
    ws_problems = wb.create_sheet("Проблемы")
    serial_counts = Counter(d.serial_norm for d in devices if d.serial_norm)
    mac_counts = Counter(d.mac_norm for d in devices if d.mac_norm)
    problem_rows: list[list[Any]] = []
    for device in devices:
        reasons: list[str] = []
        if not device.serial_norm and not device.mac_norm:
            reasons.append("Нет S/N и MAC")
        if not device.is_verified:
            reasons.append("Не проверено")
        if device.status in ("faulty", "replace", "missing"):
            reasons.append(f"Статус: {status_name(device.status)}")
        if device.code_type == "unknown":
            reasons.append("Неизвестный код")
        if device.device_type in ("camera", "ap") and not device.upstream_device_id:
            reasons.append("Нет связи с коммутатором")
        if device.serial_norm and serial_counts[device.serial_norm] > 1:
            reasons.append("Дубль S/N")
        if device.mac_norm and mac_counts[device.mac_norm] > 1:
            reasons.append("Дубль MAC")
        if reasons:
            problem_rows.append(
                [
                    device.display_id,
                    "; ".join(reasons),
                    device.location.label() if device.location else "",
                    device.note or "",
                ]
            )
    _write_table(
        ws_problems,
        ["ID устройства", "Проблема", "Место установки", "Примечание"],
        problem_rows,
    )
    _autosize(ws_problems)

    _fill_titul(wb["Титул"], session, summary, comparison, repo=repo)

    try:
        wb.save(output_path)
        return output_path
    except PermissionError:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fallback = output_path.with_name(f"{output_path.stem}_{stamp}{output_path.suffix}")
        wb.save(fallback)
        return fallback


def suggested_export_path(exports_dir: Path, session_name: str, object_name: str) -> Path:
    safe = re.sub(r"[^\w\-]+", "_", f"{object_name}_{session_name}", flags=re.UNICODE).strip("_")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return exports_dir / f"inventory_{safe or 'session'}_{stamp}.xlsx"

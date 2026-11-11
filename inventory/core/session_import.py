# inventory/core/session_import.py
"""Перенос сессии из другой SQLite-базы (обход двумя ноутбуками).

Как пользоваться:
  1. Второй ноутбук кладёт свою базу (data\\inventory.db) и рядом папку
     data\\photos на флешку или в общую папку.
  2. В приложении — «⤵ Импорт из базы…»: выбрать файл и сессию источника.
  3. Совпадения по S/N или MAC разбираются по выбранному режиму:
     пропустить / добавить как новые (пара уйдёт в «Дубликаты») /
     заполнить пустые поля существующих записей.

Устройства сохраняют исходные uuid — повторный импорт той же базы не плодит
копий («уже есть» пропускается). Импорт идёт по записи; если прервался,
повторный запуск довозит недостающее.
"""
from __future__ import annotations

import json
import sqlite3
import uuid as uuid_module
from dataclasses import dataclass, field
from pathlib import Path

from inventory.core.models import Device
from inventory.core.parser import is_valid_ipv4, normalize_mac_compact, normalize_serial
from inventory.core.repository import InventoryRepository

CONFLICT_SKIP = "skip"
CONFLICT_APPEND = "append"
CONFLICT_FILL = "fill"
CONFLICT_MODES = (CONFLICT_SKIP, CONFLICT_APPEND, CONFLICT_FILL)


@dataclass
class SourceSessionInfo:
    """Сессия в базе-источнике."""

    id: str
    name: str
    object_name: str
    inspector: str
    started_at: str
    finished_at: str | None
    status: str
    device_count: int


@dataclass
class ImportReport:
    """Итоги импорта: сколько что перенесено и какие решения приняты."""

    imported: int = 0
    skipped_duplicate: int = 0        # S/N или MAC совпали, режим «пропустить»
    skipped_existing: int = 0         # устройство уже в базе (повторный импорт)
    filled: int = 0                   # существующая запись дополнена
    fill_differences: list[str] = field(default_factory=list)
    connections_added: int = 0
    connections_skipped: int = 0
    photos_copied: int = 0
    photo_failures: list[str] = field(default_factory=list)
    locations_created: int = 0
    locations_reused: int = 0
    scan_events_added: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Перенесено устройств: {self.imported}"]
        if self.skipped_duplicate:
            lines.append(f"Совпадений пропущено: {self.skipped_duplicate}")
        if self.skipped_existing:
            lines.append(f"Уже были в базе: {self.skipped_existing}")
        if self.filled:
            lines.append(f"Записей дополнено: {self.filled}")
        if self.fill_differences:
            lines.append(
                f"Расхождений (оставлено существующее): {len(self.fill_differences)}"
            )
        if self.connections_added:
            lines.append(f"Связей добавлено: {self.connections_added}")
        if self.connections_skipped:
            lines.append(
                "Связей не перенесено (вышестоящее устройство не импортировано): "
                f"{self.connections_skipped}"
            )
        if self.photos_copied:
            lines.append(f"Файлов фото скопировано: {self.photos_copied}")
        for warning in self.warnings:
            lines.append(f"⚠ {warning}")
        return "\n".join(lines)


def _open_source(path: Path) -> sqlite3.Connection:
    """Открывает базу-источник только для чтения, проверяя, что это база приложения."""
    path = Path(path)
    if not path.exists():
        raise ValueError(f"Файл не найден: {path}")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type in ('table','view')"
        )
    }
    required = {"sessions", "devices", "locations", "connections", "scan_events"}
    if not required <= tables:
        connection.close()
        raise ValueError(
            "Выбранный файл не похож на базу приложения "
            "(нет таблиц sessions/devices/locations)"
        )
    return connection


def list_source_sessions(path: Path) -> list[SourceSessionInfo]:
    """Все сессии из базы-источника с числом устройств."""
    connection = _open_source(path)
    try:
        rows = connection.execute(
            """
            SELECT s.id, s.name, s.object_name, s.inspector, s.started_at,
                   s.finished_at, s.status,
                   (SELECT COUNT(*) FROM devices d
                     WHERE d.session_id = s.id AND d.deleted_at IS NULL) AS device_count
            FROM sessions s
            ORDER BY s.started_at DESC
            """
        ).fetchall()
    finally:
        connection.close()
    return [
        SourceSessionInfo(
            id=row["id"],
            name=row["name"],
            object_name=row["object_name"],
            inspector=row["inspector"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            status=row["status"],
            device_count=int(row["device_count"]),
        )
        for row in rows
    ]


def _norm_key(row) -> tuple[str, str, str, str, str]:
    return (
        (row["building"] or "").strip(),
        (row["floor"] or "").strip(),
        (row["room"] or "").strip(),
        (row["rack"] or "").strip(),
        (row["unit"] or "").strip(),
    )


def _copy_photo(source_path: str, photos_dir: Path, display_hint: str) -> str | None:
    """Копирует файл фото в целевую папку; возвращает новый путь или None."""
    source_file = Path(source_path)
    if not str(source_file).strip() or not source_file.exists():
        return None
    photos_dir.mkdir(parents=True, exist_ok=True)
    target = photos_dir / source_file.name
    if target.exists():
        target = photos_dir / (
            f"{display_hint.replace('-', '_')}_{uuid_module.uuid4().hex[:6]}"
            f"_{source_file.name}"
        )
    try:
        target.write_bytes(source_file.read_bytes())
    except OSError:
        return None
    return str(target)


def _copy_source_photos(
    repo: InventoryRepository,
    source: sqlite3.Connection,
    source_device_id: str,
    target_device: Device,
    photos_dir: Path | None,
    report: ImportReport,
    overwrite_cover: bool = True,
) -> None:
    """Переносит фото устройства: файлы копируются из папки рядом с базой-источником."""
    if photos_dir is None:
        return
    rows = source.execute(
        "SELECT * FROM photos WHERE device_id = ? ORDER BY id", (source_device_id,)
    ).fetchall()
    if not rows:
        return
    copied_paths: list[str] = []
    for row in rows:
        new_path = _copy_photo(row["path"], photos_dir, target_device.display_id)
        if new_path is None:
            report.photo_failures.append(
                f"{target_device.display_id}: {Path(row['path']).name}"
            )
            continue
        repo.add_photo(target_device.id, new_path, role=row["role"] or "")
        report.photos_copied += 1
        copied_paths.append(new_path)
    if copied_paths and overwrite_cover:
        repo.update_device(target_device.id, photo_path=copied_paths[-1])


def _resolve_locations(
    repo: InventoryRepository,
    source: sqlite3.Connection,
    source_session_id: str,
    session_id: str,
    report: ImportReport,
) -> dict[str, str]:
    """Локации источника → id локаций целевой сессии (по корпус/этаж/пом./шкаф/юнит)."""
    mapping: dict[str, str] = {}
    known: dict[tuple, str] = {}
    for loc in repo.list_locations(session_id):
        known[
            (
                loc.building.strip(),
                loc.floor.strip(),
                loc.room.strip(),
                loc.rack.strip(),
                loc.unit.strip(),
            )
        ] = loc.id
    rows = source.execute(
        "SELECT * FROM locations WHERE session_id = ? ORDER BY created_at",
        (source_session_id,),
    ).fetchall()
    for row in rows:
        key = _norm_key(row)
        existing = known.get(key)
        if existing:
            mapping[row["id"]] = existing
            report.locations_reused += 1
            continue
        created = repo.create_location(
            session_id,
            key[1],
            key[2],
            building=key[0],
            rack=key[3],
            unit=key[4],
            description=row["description"],
        )
        mapping[row["id"]] = created.id
        known[key] = created.id
        report.locations_created += 1
    return mapping


def _fill_target(
    repo: InventoryRepository,
    target: Device,
    source_row,
    report: ImportReport,
) -> None:
    """«Заполнить пустое»: существующая запись важнее, пустые поля дополняются."""
    pairs = (
        ("asset_tag", "Инвентарный", target.asset_tag, source_row["asset_tag"]),
        ("subtype", "Подтип", target.subtype, source_row["subtype"]),
        ("manufacturer", "Производитель", target.manufacturer, source_row["manufacturer"]),
        ("model", "Модель", target.model, source_row["model"]),
        ("power_type", "Питание", target.power_type, source_row["power_type"]),
        ("ip", "IP", target.ip, source_row["ip"]),
        ("nvr_channel", "Канал NVR", target.nvr_channel, source_row["nvr_channel"]),
        ("note", "Примечание", target.note, source_row["note"]),
    )
    updates = {key: incoming for key, _label, current, incoming in pairs
               if not current and incoming}
    if updates:
        try:
            repo.update_device(target.id, **updates)
            report.filled += 1
        except ValueError as exc:
            report.warnings.append(f"{target.display_id}: {exc}")
    else:
        report.skipped_duplicate += 1

    for key, label, current, incoming in pairs:
        if not current or not incoming or str(current) == str(incoming):
            continue
        report.fill_differences.append(
            f"{target.display_id} · {label}: «{current}» (здесь) vs «{incoming}» (импорт)"
        )


def import_session(
    repo: InventoryRepository,
    session_id: str,
    source_path: Path,
    source_session_id: str,
    photos_dir: Path | None = None,
    on_conflict: str = CONFLICT_SKIP,
) -> ImportReport:
    """Переносит устройства, связи, фото и журнал сканов из базы-источника."""
    if on_conflict not in CONFLICT_MODES:
        raise ValueError(f"Неизвестный режим конфликтов: {on_conflict}")
    session = repo.get_session(session_id)
    if not session:
        raise ValueError("Сессия не найдена")
    if session.status != "active":
        raise ValueError("Импорт доступен только в активную сессию")

    source = _open_source(source_path)
    report = ImportReport()
    try:
        source_sessions = {
            row["id"] for row in source.execute("SELECT id FROM sessions").fetchall()
        }
        if source_session_id not in source_sessions:
            raise ValueError("Выбранная сессия отсутствует в этой базе")

        locations = _resolve_locations(
            repo, source, source_session_id, session_id, report
        )
        devices = source.execute(
            "SELECT * FROM devices WHERE session_id = ? AND deleted_at IS NULL "
            "ORDER BY created_at, display_id",
            (source_session_id,),
        ).fetchall()

        # Целевые индексы: uuid всех устройств (вкл. удалённые) и нормы активных.
        target_ids: set[str] = {
            row["id"]
            for row in repo.db.fetchall(
                "SELECT id FROM devices WHERE session_id = ?", (session_id,)
            )
        }
        target_by_serial: dict[str, Device] = {}
        target_by_mac: dict[str, Device] = {}
        for device in repo.list_devices(session_id):
            if device.serial_norm:
                target_by_serial.setdefault(device.serial_norm, device)
            if device.mac_norm:
                target_by_mac.setdefault(device.mac_norm, device)

        for row in devices:
            if row["id"] in target_ids:
                report.skipped_existing += 1
                continue

            serial_norm = normalize_serial(row["serial"])
            mac_norm = normalize_mac_compact(row["mac"])
            matches: dict[str, Device] = {}
            if serial_norm and serial_norm in target_by_serial:
                matches[target_by_serial[serial_norm].id] = target_by_serial[serial_norm]
            if mac_norm and mac_norm in target_by_mac:
                matches[target_by_mac[mac_norm].id] = target_by_mac[mac_norm]
            match_list = list(matches.values())

            if match_list and on_conflict == CONFLICT_SKIP:
                report.skipped_duplicate += 1
                continue
            if match_list and on_conflict == CONFLICT_FILL:
                for match in match_list:
                    _fill_target(repo, match, row, report)
                continue
            # Нет совпадений, либо режим «добавить как новые».

            location_id = locations.get(row["location_id"])
            if location_id is None:
                fallback = repo.list_locations(session_id)
                location_id = (
                    fallback[0].id
                    if fallback
                    else repo.create_location(session_id, "-", "Импорт").id
                )
                report.warnings.append(
                    f"{row['display_id']}: локация источника не совпала — "
                    "устройство помещено в первую локацию сессии"
                )

            attributes = None
            if row["attributes_json"]:
                try:
                    attributes = json.loads(row["attributes_json"])
                except (TypeError, ValueError):
                    attributes = None

            ip = row["ip"]
            if ip and not is_valid_ipv4(ip):
                report.warnings.append(
                    f"{row['display_id']}: некорректный IP «{ip}» пропущен"
                )
                ip = None

            device = repo.create_device_with_id(
                session_id,
                row["id"],
                None,  # display_id присваивается заново в целевой сессии
                row["device_type"],
                location_id,
                asset_tag=row["asset_tag"],
                subtype=row["subtype"],
                manufacturer=row["manufacturer"],
                model=row["model"],
                serial=row["serial"],
                mac=row["mac"],
                ip=ip,
                power_type=row["power_type"],
                status=row["status"] or "installed",
                note=row["note"],
                scanned_code=row["scanned_code"],
                code_type=row["code_type"],
                attributes=attributes,
                is_verified=bool(row["is_verified"]),
                nvr_channel=row["nvr_channel"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            _copy_source_photos(
                repo, source, row["id"], device, photos_dir, report
            )
            target_ids.add(row["id"])
            if serial_norm:
                target_by_serial.setdefault(serial_norm, device)
            if mac_norm:
                target_by_mac.setdefault(mac_norm, device)
            report.imported += 1

        # Связи: переносятся, когда оба конца импортированы (uuid сохранены).
        for row in source.execute("SELECT * FROM connections").fetchall():
            device_guid = row["device_id"]
            upstream_guid = row["upstream_device_id"]
            if device_guid not in target_ids:
                continue
            if upstream_guid and upstream_guid not in target_ids:
                report.connections_skipped += 1
                continue
            if upstream_guid is None or repo.get_connection(device_guid) is not None:
                continue
            repo.set_connection(
                device_guid,
                upstream_guid,
                row["upstream_port"],
                row["cable_type"],
                row["note"],
            )
            report.connections_added += 1

        # Журнал сканов: переносим, привязку к устройству сохраняем, где возможна.
        for row in source.execute(
            "SELECT * FROM scan_events WHERE session_id = ? ORDER BY id",
            (source_session_id,),
        ).fetchall():
            event_device = row["device_id"]
            if event_device is not None and event_device not in target_ids:
                event_device = None
            with repo.db.conn:
                repo.db.execute(
                    """
                    INSERT INTO scan_events(
                        session_id, raw_code, parsed_type, parsed_value, action,
                        device_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        row["raw_code"],
                        row["parsed_type"],
                        row["parsed_value"],
                        row["action"],
                        event_device,
                        row["created_at"],
                    ),
                )
            report.scan_events_added += 1

        stamp = report.summary().replace("\n", "; ")
        with repo.db.conn:
            repo.db.execute(
                "UPDATE sessions SET comment = "
                "CASE WHEN comment IS NULL OR comment = '' THEN ? "
                "ELSE comment || ' | ' || ? END "
                "WHERE id = ?",
                (f"Импорт: {stamp}", f"Импорт: {stamp}", session_id),
            )
    finally:
        source.close()
    return report

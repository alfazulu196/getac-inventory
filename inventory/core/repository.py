# inventory/core/repository.py
"""Репозиторий: сессии, локации, устройства, связи, журнал, сводка."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from inventory.config import (
    DEVICE_TYPE_BY_CODE,
    device_type_name,
    device_type_prefix,
    status_name,
)
from inventory.core.db import Database, now_iso
from inventory.core.models import (
    BaselineDevice,
    Connection,
    Device,
    Location,
    Photo,
    ScanEvent,
    Session,
)
from inventory.core.parser import (
    clean_serial,
    is_valid_ipv4,
    normalize_mac,
    normalize_mac_compact,
    normalize_serial,
)

DEVICE_COLUMNS = (
    "id, session_id, display_id, asset_tag, device_type, subtype, manufacturer, model, "
    "serial, serial_norm, mac, mac_norm, ip, location_id, power_type, status, note, "
    "photo_path, scanned_code, code_type, attributes_json, is_verified, created_at, "
    "updated_at, deleted_at"
)

DEVICE_SELECT = """
SELECT
    d.*,
    l.id AS loc_id,
    l.session_id AS loc_session_id,
    l.building AS loc_building,
    l.floor AS loc_floor,
    l.room AS loc_room,
    l.rack AS loc_rack,
    l.unit AS loc_unit,
    l.description AS loc_description,
    l.created_at AS loc_created_at,
    c.upstream_device_id AS upstream_device_id,
    up.display_id AS upstream_display_id,
    c.upstream_port AS upstream_port
FROM devices d
JOIN locations l ON l.id = d.location_id
LEFT JOIN connections c ON c.device_id = d.id
LEFT JOIN devices up ON up.id = c.upstream_device_id
"""


class InventoryRepository:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------
    # Сессии
    # ------------------------------------------------------------------
    def create_session(
        self,
        object_name: str,
        inspector: str,
        name: str | None = None,
        comment: str | None = None,
        contractor: str | None = None,
    ) -> Session:
        object_name = object_name.strip()
        inspector = inspector.strip()
        contractor = (contractor or "").strip() or None
        if not object_name:
            raise ValueError("Укажите объект")
        if not inspector:
            raise ValueError("Укажите инспектора")
        if self.get_active_session():
            raise ValueError("Уже есть активная сессия. Завершите её перед созданием новой")

        session_id = uuid4().hex
        now = now_iso()
        session_name = (name or f"Инвентаризация: {object_name}").strip()
        with self.db.conn:
            self.db.execute(
                """
                INSERT INTO sessions(id, name, object_name, inspector, contractor, started_at, status, comment)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
                """,
                (session_id, session_name, object_name, inspector, contractor, now, comment),
            )
        return self.get_session(session_id)  # type: ignore[return-value]

    def get_session(self, session_id: str) -> Session | None:
        row = self.db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
        return Session.from_row(row) if row else None

    def get_active_session(self) -> Session | None:
        row = self.db.fetchone(
            "SELECT * FROM sessions WHERE status = 'active' ORDER BY started_at DESC LIMIT 1"
        )
        return Session.from_row(row) if row else None

    def list_sessions(self, limit: int = 100) -> list[Session]:
        rows = self.db.fetchall(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
        )
        return [Session.from_row(row) for row in rows]

    def finish_session(self, session_id: str) -> None:
        with self.db.conn:
            self.db.execute(
                "UPDATE sessions SET status = 'completed', finished_at = ? WHERE id = ?",
                (now_iso(), session_id),
            )

    def archive_session(self, session_id: str) -> None:
        with self.db.conn:
            self.db.execute(
                "UPDATE sessions SET status = 'archived', finished_at = COALESCE(finished_at, ?) WHERE id = ?",
                (now_iso(), session_id),
            )

    def reopen_session(self, session_id: str) -> Session:
        """Вернуть завершённую сессию в активное состояние (добавлять устройства).

        Требуется, чтобы активной была сама сессия; иначе — ValueError
        (UI сначала предлагает завершить текущую активную сессию).
        """
        active = self.get_active_session()
        if active and active.id != session_id:
            raise ValueError(
                f"Активна другая сессия «{active.object_name}». Завершите её перед открытием"
            )
        with self.db.conn:
            self.db.execute(
                "UPDATE sessions SET status = 'active', finished_at = NULL WHERE id = ?",
                (session_id,),
            )
        reopened = self.get_session(session_id)
        if reopened is None:
            raise ValueError("Сессия не найдена")
        return reopened

    # ------------------------------------------------------------------
    # Подрядчики (список для старта сессии, хранится в настройках)
    # ------------------------------------------------------------------
    CONTRACTORS_KEY = "contractors"
    CONTRACTORS_LIMIT = 20

    def list_contractors(self) -> list[str]:
        raw = self.get_setting(self.CONTRACTORS_KEY) or "[]"
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [str(item).strip() for item in data if str(item).strip()]

    def remember_contractor(self, name: str | None) -> list[str]:
        """Добавить/поднять подрядчика вверх списка; вернуть список."""
        name = (name or "").strip()
        items = self.list_contractors()
        if not name:
            return items
        if name in items:
            items.remove(name)
        items.insert(0, name)
        items = items[: self.CONTRACTORS_LIMIT]
        self.set_setting(self.CONTRACTORS_KEY, json.dumps(items, ensure_ascii=False))
        return items

    # ------------------------------------------------------------------
    # Локации
    # ------------------------------------------------------------------
    def create_location(
        self,
        session_id: str,
        floor: str,
        room: str,
        building: str = "",
        rack: str = "",
        unit: str = "",
        description: str | None = None,
    ) -> Location:
        if not floor.strip() or not room.strip():
            raise ValueError("Укажите этаж и помещение")
        location_id = uuid4().hex
        now = now_iso()
        with self.db.conn:
            self.db.execute(
                """
                INSERT INTO locations(id, session_id, building, floor, room, rack, unit, description, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    location_id,
                    session_id,
                    building.strip(),
                    floor.strip(),
                    room.strip(),
                    rack.strip(),
                    unit.strip(),
                    description,
                    now,
                ),
            )
        return self.get_location(location_id)  # type: ignore[return-value]

    def get_location(self, location_id: str) -> Location | None:
        row = self.db.fetchone("SELECT * FROM locations WHERE id = ?", (location_id,))
        return Location.from_row(row) if row else None

    def find_location(
        self,
        session_id: str,
        building: str,
        floor: str,
        room: str,
        rack: str = "",
        unit: str = "",
    ) -> Location | None:
        row = self.db.fetchone(
            """
            SELECT * FROM locations
            WHERE session_id = ? AND building = ? AND floor = ? AND room = ?
              AND rack = ? AND unit = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (session_id, building.strip(), floor.strip(), room.strip(), rack.strip(), unit.strip()),
        )
        return Location.from_row(row) if row else None

    def get_or_create_location(
        self,
        session_id: str,
        floor: str,
        room: str,
        building: str = "",
        rack: str = "",
        unit: str = "",
        description: str | None = None,
    ) -> Location:
        existing = self.find_location(session_id, building, floor, room, rack, unit)
        if existing:
            return existing
        return self.create_location(
            session_id, floor, room, building, rack, unit, description
        )

    def list_locations(self, session_id: str, limit: int | None = None) -> list[Location]:
        sql = "SELECT * FROM locations WHERE session_id = ? ORDER BY created_at DESC"
        params: list[Any] = [session_id]
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return [Location.from_row(row) for row in self.db.fetchall(sql, params)]

    def recent_locations(self, session_id: str, limit: int = 10) -> list[Location]:
        rows = self.db.fetchall(
            """
            SELECT l.*, MAX(d.created_at) AS last_used
            FROM locations l
            JOIN devices d ON d.location_id = l.id AND d.deleted_at IS NULL
            WHERE l.session_id = ?
            GROUP BY l.id
            ORDER BY last_used DESC
            LIMIT ?
            """,
            (session_id, limit),
        )
        return [Location.from_row(row) for row in rows]

    def update_location(self, location_id: str, **fields: Any) -> Location:
        allowed = {"building", "floor", "room", "rack", "unit", "description"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_location(location_id)  # type: ignore[return-value]
        if not str(updates.get("floor", "x")).strip() or not str(updates.get("room", "x")).strip():
            raise ValueError("Этаж и помещение не могут быть пустыми")
        set_sql = ", ".join(f"{key} = ?" for key in updates)
        with self.db.conn:
            self.db.execute(
                f"UPDATE locations SET {set_sql} WHERE id = ?",
                (*updates.values(), location_id),
            )
        return self.get_location(location_id)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Устройства
    # ------------------------------------------------------------------
    def _next_display_id(self, session_id: str, device_type: str) -> str:
        prefix = device_type_prefix(device_type)
        rows = self.db.fetchall(
            "SELECT display_id FROM devices WHERE session_id = ? AND display_id LIKE ?",
            (session_id, f"{prefix}-%"),
        )
        max_number = 0
        for row in rows:
            suffix = row["display_id"].split("-", 1)[-1]
            if suffix.isdigit():
                max_number = max(max_number, int(suffix))
        return f"{prefix}-{max_number + 1:03d}"

    def next_display_id(self, session_id: str, device_type: str) -> str:
        return self._next_display_id(session_id, device_type)

    def create_device(
        self,
        session_id: str,
        device_type: str,
        location_id: str,
        *,
        asset_tag: str | None = None,
        subtype: str | None = None,
        manufacturer: str | None = None,
        model: str | None = None,
        serial: str | None = None,
        mac: str | None = None,
        ip: str | None = None,
        power_type: str | None = None,
        status: str = "installed",
        note: str | None = None,
        photo_path: str | None = None,
        scanned_code: str | None = None,
        code_type: str | None = None,
        attributes: dict[str, Any] | None = None,
        is_verified: bool = False,
        nvr_channel: str | None = None,
    ) -> Device:
        return self.create_device_with_id(
            session_id,
            uuid4().hex,
            None,
            device_type,
            location_id,
            asset_tag=asset_tag,
            subtype=subtype,
            manufacturer=manufacturer,
            model=model,
            serial=serial,
            mac=mac,
            ip=ip,
            power_type=power_type,
            status=status,
            note=note,
            photo_path=photo_path,
            scanned_code=scanned_code,
            code_type=code_type,
            attributes=attributes,
            is_verified=is_verified,
            nvr_channel=nvr_channel,
        )

    def create_device_with_id(
        self,
        session_id: str,
        device_id: str,
        display_id: str | None,
        device_type: str,
        location_id: str,
        *,
        asset_tag: str | None = None,
        subtype: str | None = None,
        manufacturer: str | None = None,
        model: str | None = None,
        serial: str | None = None,
        mac: str | None = None,
        ip: str | None = None,
        power_type: str | None = None,
        status: str = "installed",
        note: str | None = None,
        photo_path: str | None = None,
        scanned_code: str | None = None,
        code_type: str | None = None,
        attributes: dict[str, Any] | None = None,
        is_verified: bool = False,
        nvr_channel: str | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> Device:
        """Создание с заданными id и display_id — для импорта из другой базы."""
        session = self.get_session(session_id)
        if not session:
            raise ValueError("Сессия не найдена")
        if session.status != "active":
            raise ValueError("Сессия завершена: добавление устройств недоступно")
        if device_type not in DEVICE_TYPE_BY_CODE:
            raise ValueError(f"Неизвестный тип устройства: {device_type}")
        location = self.get_location(location_id)
        if not location or location.session_id != session_id:
            raise ValueError("Локация не принадлежит текущей сессии")
        if ip and not is_valid_ipv4(ip):
            raise ValueError(f"Некорректный IP-адрес: {ip}")

        serial_clean = clean_serial(serial)
        serial_norm = normalize_serial(serial)
        mac_clean = normalize_mac(mac) or (mac.strip().upper() if mac else None)
        mac_norm = normalize_mac_compact(mac)

        if display_id is None:
            display_id = self._next_display_id(session_id, device_type)
        created_ts = created_at or now_iso()
        updated_ts = updated_at or created_ts
        with self.db.transaction():
            self.db.execute(
                """
                INSERT INTO devices(
                    id, session_id, display_id, asset_tag, device_type, subtype,
                    manufacturer, model, serial, serial_norm, mac, mac_norm, ip,
                    location_id, power_type, status, note, photo_path, scanned_code,
                    code_type, attributes_json, is_verified, nvr_channel,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    device_id,
                    session_id,
                    display_id,
                    asset_tag,
                    device_type,
                    subtype,
                    manufacturer,
                    model,
                    serial_clean,
                    serial_norm,
                    mac_clean,
                    mac_norm,
                    ip,
                    location_id,
                    power_type,
                    status,
                    note,
                    photo_path,
                    scanned_code,
                    code_type,
                    json.dumps(attributes, ensure_ascii=False) if attributes else None,
                    1 if is_verified else 0,
                    (nvr_channel.strip() or None) if nvr_channel else None,
                    created_ts,
                    updated_ts,
                ),
            )
        return self.get_device(device_id)  # type: ignore[return-value]

    def get_device(self, device_id: str, include_deleted: bool = False) -> Device | None:
        sql = DEVICE_SELECT + " WHERE d.id = ?"
        if not include_deleted:
            sql += " AND d.deleted_at IS NULL"
        row = self.db.fetchone(sql, (device_id,))
        return self._device_from_row(row) if row else None

    def _device_from_row(self, row: Any) -> Device:
        device = Device.from_row(row)
        device.location = Location(
            id=row["loc_id"],
            session_id=row["loc_session_id"],
            building=row["loc_building"] or "",
            floor=row["loc_floor"] or "",
            room=row["loc_room"] or "",
            rack=row["loc_rack"] or "",
            unit=row["loc_unit"] or "",
            description=row["loc_description"],
            created_at=row["loc_created_at"],
        )
        return device

    def update_device(self, device_id: str, **fields: Any) -> Device:
        allowed = {
            "asset_tag",
            "device_type",
            "subtype",
            "manufacturer",
            "model",
            "serial",
            "mac",
            "ip",
            "location_id",
            "power_type",
            "status",
            "note",
            "photo_path",
            "scanned_code",
            "code_type",
            "attributes_json",
            "is_verified",
            "nvr_channel",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_device(device_id)  # type: ignore[return-value]

        device = self.get_device(device_id)
        if not device:
            raise ValueError("Устройство не найдено")

        if "device_type" in updates and updates["device_type"] not in DEVICE_TYPE_BY_CODE:
            raise ValueError(f"Неизвестный тип устройства: {updates['device_type']}")
        if "location_id" in updates:
            location = self.get_location(updates["location_id"])
            if not location or location.session_id != device.session_id:
                raise ValueError("Локация не принадлежит текущей сессии")
        if "ip" in updates and updates["ip"] and not is_valid_ipv4(updates["ip"]):
            raise ValueError(f"Некорректный IP-адрес: {updates['ip']}")
        if "serial" in updates:
            updates["serial"] = clean_serial(updates["serial"])
            updates["serial_norm"] = normalize_serial(updates["serial"])
        if "mac" in updates:
            updates["mac"] = normalize_mac(updates["mac"]) or (
                updates["mac"].strip().upper() if updates["mac"] else None
            )
            updates["mac_norm"] = normalize_mac_compact(updates["mac"])
        if "nvr_channel" in updates:
            updates["nvr_channel"] = (
                updates["nvr_channel"].strip() or None
            ) if updates["nvr_channel"] else None
        if "is_verified" in updates:
            updates["is_verified"] = 1 if updates["is_verified"] else 0
        if "attributes_json" in updates and isinstance(updates["attributes_json"], dict):
            updates["attributes_json"] = json.dumps(
                updates["attributes_json"], ensure_ascii=False
            )

        updates["updated_at"] = now_iso()
        set_sql = ", ".join(f"{key} = ?" for key in updates)
        with self.db.conn:
            self.db.execute(
                f"UPDATE devices SET {set_sql} WHERE id = ?",
                (*updates.values(), device_id),
            )
        return self.get_device(device_id)  # type: ignore[return-value]

    def soft_delete_device(self, device_id: str) -> None:
        now = now_iso()
        with self.db.conn:
            self.db.execute(
                "UPDATE devices SET deleted_at = ?, updated_at = ? WHERE id = ?",
                (now, now, device_id),
            )

    def restore_device(self, device_id: str) -> None:
        with self.db.conn:
            self.db.execute(
                "UPDATE devices SET deleted_at = NULL, updated_at = ? WHERE id = ?",
                (now_iso(), device_id),
            )

    def _build_device_filters(
        self,
        session_id: str,
        *,
        query: str | None = None,
        device_type: str | None = None,
        status: str | None = None,
        location_id: str | None = None,
        has_photo: bool | None = None,
        has_connection: bool | None = None,
        verified: bool | None = None,
        include_deleted: bool = False,
    ) -> tuple[str, list[Any]]:
        where = ["d.session_id = ?"]
        params: list[Any] = [session_id]
        if not include_deleted:
            where.append("d.deleted_at IS NULL")
        if query:
            like = f"%{query.strip()}%"
            where.append(
                "(d.display_id LIKE ? OR d.asset_tag LIKE ? OR d.serial LIKE ? "
                "OR d.mac LIKE ? OR d.ip LIKE ? OR d.model LIKE ? OR d.manufacturer LIKE ? "
                "OR d.note LIKE ?)"
            )
            params.extend([like] * 8)
        if device_type:
            where.append("d.device_type = ?")
            params.append(device_type)
        if status:
            where.append("d.status = ?")
            params.append(status)
        if location_id:
            where.append("d.location_id = ?")
            params.append(location_id)
        if has_photo is not None:
            if has_photo:
                where.append("d.photo_path IS NOT NULL AND d.photo_path <> ''")
            else:
                where.append("(d.photo_path IS NULL OR d.photo_path = '')")
        if has_connection is not None:
            where.append("c.id IS NOT NULL" if has_connection else "c.id IS NULL")
        if verified is not None:
            where.append("d.is_verified = ?")
            params.append(1 if verified else 0)
        return " AND ".join(where), params

    def list_devices(
        self,
        session_id: str,
        *,
        query: str | None = None,
        device_type: str | None = None,
        status: str | None = None,
        location_id: str | None = None,
        has_photo: bool | None = None,
        has_connection: bool | None = None,
        verified: bool | None = None,
        include_deleted: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Device]:
        where_sql, params = self._build_device_filters(
            session_id,
            query=query,
            device_type=device_type,
            status=status,
            location_id=location_id,
            has_photo=has_photo,
            has_connection=has_connection,
            verified=verified,
            include_deleted=include_deleted,
        )
        sql = DEVICE_SELECT + f" WHERE {where_sql} ORDER BY d.created_at DESC, d.display_id DESC"
        if limit:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        rows = self.db.fetchall(sql, params)
        return [self._device_from_row(row) for row in rows]

    def count_devices(
        self,
        session_id: str,
        *,
        query: str | None = None,
        device_type: str | None = None,
        status: str | None = None,
        location_id: str | None = None,
        has_photo: bool | None = None,
        has_connection: bool | None = None,
        verified: bool | None = None,
        include_deleted: bool = False,
    ) -> int:
        where_sql, params = self._build_device_filters(
            session_id,
            query=query,
            device_type=device_type,
            status=status,
            location_id=location_id,
            has_photo=has_photo,
            has_connection=has_connection,
            verified=verified,
            include_deleted=include_deleted,
        )
        row = self.db.fetchone(
            f"""
            SELECT COUNT(*) AS c
            FROM devices d
            LEFT JOIN connections c ON c.device_id = d.id
            WHERE {where_sql}
            """,
            params,
        )
        return int(row["c"]) if row else 0

    def find_duplicates(
        self,
        session_id: str,
        serial_norm: str | None = None,
        mac_norm: str | None = None,
        exclude_id: str | None = None,
    ) -> list[Device]:
        conditions: list[str] = []
        params: list[Any] = [session_id]
        if serial_norm:
            conditions.append("serial_norm = ?")
            params.append(serial_norm)
        if mac_norm:
            conditions.append("mac_norm = ?")
            params.append(mac_norm)
        if not conditions:
            return []
        sql = (
            "SELECT id FROM devices WHERE session_id = ? AND deleted_at IS NULL "
            f"AND ({' OR '.join(conditions)})"
        )
        if exclude_id:
            sql += " AND id <> ?"
            params.append(exclude_id)
        rows = self.db.fetchall(sql, params)
        result: list[Device] = []
        for row in rows:
            device = self.get_device(row["id"])
            if device:
                result.append(device)
        return result

    def get_summary(self, session_id: str) -> dict[str, Any]:
        total_row = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM devices WHERE session_id = ? AND deleted_at IS NULL",
            (session_id,),
        )
        total = int(total_row["c"]) if total_row else 0

        by_type_rows = self.db.fetchall(
            """
            SELECT device_type, COUNT(*) AS c
            FROM devices
            WHERE session_id = ? AND deleted_at IS NULL
            GROUP BY device_type
            ORDER BY c DESC
            """,
            (session_id,),
        )
        by_type = [
            {
                "code": row["device_type"],
                "name": device_type_name(row["device_type"]),
                "count": int(row["c"]),
            }
            for row in by_type_rows
        ]

        by_status_rows = self.db.fetchall(
            """
            SELECT status, COUNT(*) AS c
            FROM devices
            WHERE session_id = ? AND deleted_at IS NULL
            GROUP BY status
            ORDER BY c DESC
            """,
            (session_id,),
        )
        by_status = [
            {"code": row["status"], "name": status_name(row["status"]), "count": int(row["c"])}
            for row in by_status_rows
        ]

        by_location_rows = self.db.fetchall(
            """
            SELECT l.id, l.building, l.floor, l.room, l.rack, l.unit, COUNT(*) AS c
            FROM devices d
            JOIN locations l ON l.id = d.location_id
            WHERE d.session_id = ? AND d.deleted_at IS NULL
            GROUP BY l.id
            ORDER BY c DESC
            """,
            (session_id,),
        )
        by_location = [
            {
                "id": row["id"],
                "label": Location(
                    id=row["id"],
                    session_id=session_id,
                    building=row["building"] or "",
                    floor=row["floor"] or "",
                    room=row["room"] or "",
                    rack=row["rack"] or "",
                    unit=row["unit"] or "",
                ).label(),
                "count": int(row["c"]),
            }
            for row in by_location_rows
        ]

        no_serial_mac_row = self.db.fetchone(
            """
            SELECT COUNT(*) AS c FROM devices
            WHERE session_id = ? AND deleted_at IS NULL
              AND (serial_norm IS NULL OR serial_norm = '')
              AND (mac_norm IS NULL OR mac_norm = '')
            """,
            (session_id,),
        )
        no_connection_row = self.db.fetchone(
            """
            SELECT COUNT(*) AS c
            FROM devices d
            LEFT JOIN connections c ON c.device_id = d.id
            WHERE d.session_id = ? AND d.deleted_at IS NULL
              AND d.device_type IN ('camera', 'ap') AND c.id IS NULL
            """,
            (session_id,),
        )
        no_photo_row = self.db.fetchone(
            """
            SELECT COUNT(*) AS c FROM devices
            WHERE session_id = ? AND deleted_at IS NULL
              AND (photo_path IS NULL OR photo_path = '')
            """,
            (session_id,),
        )
        unverified_row = self.db.fetchone(
            """
            SELECT COUNT(*) AS c FROM devices
            WHERE session_id = ? AND deleted_at IS NULL AND is_verified = 0
            """,
            (session_id,),
        )
        duplicate_row = self.db.fetchone(
            """
            SELECT COUNT(*) AS c FROM (
                SELECT serial_norm AS value FROM devices
                WHERE session_id = ? AND deleted_at IS NULL
                  AND serial_norm IS NOT NULL AND serial_norm <> ''
                GROUP BY serial_norm HAVING COUNT(*) > 1
                UNION ALL
                SELECT mac_norm AS value FROM devices
                WHERE session_id = ? AND deleted_at IS NULL
                  AND mac_norm IS NOT NULL AND mac_norm <> ''
                GROUP BY mac_norm HAVING COUNT(*) > 1
            )
            """,
            (session_id, session_id),
        )

        return {
            "total": total,
            "by_type": by_type,
            "by_status": by_status,
            "by_location": by_location,
            "no_serial_mac": int(no_serial_mac_row["c"]) if no_serial_mac_row else 0,
            "no_connection": int(no_connection_row["c"]) if no_connection_row else 0,
            "no_photo": int(no_photo_row["c"]) if no_photo_row else 0,
            "unverified": int(unverified_row["c"]) if unverified_row else 0,
            "duplicate_groups": int(duplicate_row["c"]) if duplicate_row else 0,
        }

    # ------------------------------------------------------------------
    # Связи
    # ------------------------------------------------------------------
    def set_connection(
        self,
        device_id: str,
        upstream_device_id: str | None = None,
        upstream_port: str | None = None,
        cable_type: str | None = None,
        note: str | None = None,
    ) -> Connection:
        device = self.get_device(device_id)
        if not device:
            raise ValueError("Устройство не найдено")
        if upstream_device_id:
            if upstream_device_id == device_id:
                raise ValueError("Устройство не может быть подключено само к себе")
            upstream = self.get_device(upstream_device_id)
            if not upstream or upstream.session_id != device.session_id:
                raise ValueError("Вышестоящее устройство не найдено в текущей сессии")

        existing = self.get_connection(device_id)
        now = now_iso()
        connection_id = existing.id if existing else uuid4().hex
        with self.db.conn:
            self.db.execute(
                """
                INSERT INTO connections(
                    id, device_id, upstream_device_id, upstream_port, cable_type, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    upstream_device_id = excluded.upstream_device_id,
                    upstream_port = excluded.upstream_port,
                    cable_type = excluded.cable_type,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (
                    connection_id,
                    device_id,
                    upstream_device_id,
                    upstream_port,
                    cable_type,
                    note,
                    existing.created_at if existing else now,
                    now,
                ),
            )
        return self.get_connection(device_id)  # type: ignore[return-value]

    def get_connection(self, device_id: str) -> Connection | None:
        row = self.db.fetchone("SELECT * FROM connections WHERE device_id = ?", (device_id,))
        return Connection.from_row(row) if row else None

    def delete_connection(self, device_id: str) -> None:
        with self.db.conn:
            self.db.execute("DELETE FROM connections WHERE device_id = ?", (device_id,))

    def list_connections_view(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT c.*, d.display_id AS device_display_id, up.display_id AS upstream_display_id
            FROM connections c
            JOIN devices d ON d.id = c.device_id AND d.deleted_at IS NULL
            LEFT JOIN devices up ON up.id = c.upstream_device_id
            WHERE d.session_id = ?
            ORDER BY d.display_id
            """,
            (session_id,),
        )
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Журнал сканирований
    # ------------------------------------------------------------------
    def log_scan(
        self,
        session_id: str,
        raw_code: str,
        parsed_type: str | None = None,
        parsed_value: str | None = None,
        action: str = "created",
        device_id: str | None = None,
    ) -> int:
        with self.db.conn:
            cursor = self.db.execute(
                """
                INSERT INTO scan_events(session_id, raw_code, parsed_type, parsed_value, action, device_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, raw_code, parsed_type, parsed_value, action, device_id, now_iso()),
            )
        return int(cursor.lastrowid)

    def list_scan_events(self, session_id: str, limit: int = 200) -> list[ScanEvent]:
        rows = self.db.fetchall(
            "SELECT * FROM scan_events WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        )
        return [ScanEvent.from_row(row) for row in rows]

    # ------------------------------------------------------------------
    # Настройки
    # ------------------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.db.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            return row["value"]

    def set_setting(self, key: str, value: Any) -> None:
        with self.db.conn:
            self.db.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    # ------------------------------------------------------------------
    # Фотографии устройства (мультифото, таблица photos)
    # ------------------------------------------------------------------
    def add_photo(self, device_id: str, path: str, role: str = "") -> Photo:
        created = now_iso()
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO photos(device_id, path, role, created_at) VALUES (?, ?, ?, ?)",
                (device_id, path, role, created),
            )
            row = conn.execute(
                "SELECT * FROM photos WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            return Photo.from_row(row)

    def list_photos(self, device_id: str) -> list[Photo]:
        rows = self.db.fetchall(
            "SELECT * FROM photos WHERE device_id = ? ORDER BY id", (device_id,)
        )
        return [Photo.from_row(row) for row in rows]

    def delete_photo(self, photo_id: int) -> None:
        """Удаляет фото. Если удалили обложку (device.photo_path) — подставляет последнее оставшееся."""
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT device_id, path FROM photos WHERE id = ?", (photo_id,)
            ).fetchone()
            if row is None:
                return
            device_id, path = row["device_id"], row["path"]
            conn.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
            device = conn.execute(
                "SELECT photo_path FROM devices WHERE id = ?", (device_id,)
            ).fetchone()
            if not device or not device["photo_path"]:
                return
            if Path(device["photo_path"]) != Path(path):
                return
            remaining = conn.execute(
                "SELECT path FROM photos WHERE device_id = ? ORDER BY id DESC LIMIT 1",
                (device_id,),
            ).fetchone()
            conn.execute(
                "UPDATE devices SET photo_path = ? WHERE id = ?",
                (remaining["path"] if remaining else None, device_id),
            )

    # ------------------------------------------------------------------
    # Сверка с прошлой инвентаризацией (baseline_devices)
    # ------------------------------------------------------------------
    def import_baseline(
        self,
        session_id: str,
        path: str | Path,
        rows: list[dict[str, Any]],
    ) -> dict[str, int]:
        """Заменяет baseline сессии. rows — результат baseline.parse_baseline()."""
        from inventory.services.logging_setup import log_info

        usable = sum(1 for row in rows if row.get("serial_norm") or row.get("mac_norm"))
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM baseline_devices WHERE session_id = ?", (session_id,))
            stamp = now_iso()
            for row in rows:
                conn.execute(
                    "INSERT INTO baseline_devices("
                    "session_id, display_id, device_type, manufacturer, model, "
                    "serial, serial_norm, mac, mac_norm, ip, location_label, status, "
                    "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        session_id,
                        row.get("display_id"),
                        row.get("device_type"),
                        row.get("manufacturer"),
                        row.get("model"),
                        row.get("serial"),
                        row.get("serial_norm"),
                        row.get("mac"),
                        row.get("mac_norm"),
                        row.get("ip"),
                        row.get("location_label"),
                        row.get("status"),
                        stamp,
                    ),
                )
            conn.execute(
                "UPDATE sessions SET baseline_path = ? WHERE id = ?",
                (str(path), session_id),
            )
        log_info(
            "Baseline импортирован: %s (строк %d, опознаваемых %d)", str(path), len(rows), usable
        )
        return {"total": len(rows), "usable": usable}

    def list_baseline(self, session_id: str) -> list[BaselineDevice]:
        rows = self.db.fetchall(
            "SELECT * FROM baseline_devices WHERE session_id = ? ORDER BY id", (session_id,)
        )
        return [BaselineDevice.from_row(row) for row in rows]

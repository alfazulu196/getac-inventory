# inventory/core/models.py
"""Модели данных приложения."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Optional


@dataclass(frozen=True)
class DeviceType:
    code: str
    name: str
    prefix: str
    default_power: str
    icon: str

    @property
    def display_name(self) -> str:
        return f"{self.icon} {self.name}".strip()


@dataclass
class Session:
    id: str
    name: str
    object_name: str
    inspector: str
    started_at: str
    finished_at: Optional[str] = None
    status: str = "active"
    comment: Optional[str] = None
    baseline_path: Optional[str] = None
    contractor: Optional[str] = None

    @classmethod
    def from_row(cls, row: Any) -> "Session":
        keys = row.keys()
        baseline = row["baseline_path"] if "baseline_path" in keys else None
        contractor = row["contractor"] if "contractor" in keys else None
        return cls(
            id=row["id"],
            name=row["name"],
            object_name=row["object_name"],
            inspector=row["inspector"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            status=row["status"],
            comment=row["comment"],
            baseline_path=baseline,
            contractor=contractor,
        )


@dataclass
class Photo:
    """Фотографический снимок устройства (много на одно устройство)."""

    id: int
    device_id: str
    path: str
    role: str = ""
    created_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> "Photo":
        return cls(
            id=row["id"],
            device_id=row["device_id"],
            path=row["path"],
            role=row["role"] or "",
            created_at=row["created_at"] or "",
        )


@dataclass
class BaselineDevice:
    """Запись прошлой инвентаризации (для сверки)."""

    id: int
    session_id: str
    display_id: Optional[str] = None
    device_type: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial: Optional[str] = None
    serial_norm: Optional[str] = None
    mac: Optional[str] = None
    mac_norm: Optional[str] = None
    ip: Optional[str] = None
    location_label: Optional[str] = None
    status: Optional[str] = None
    created_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> "BaselineDevice":
        return cls(
            id=row["id"],
            session_id=row["session_id"],
            display_id=row["display_id"],
            device_type=row["device_type"],
            manufacturer=row["manufacturer"],
            model=row["model"],
            serial=row["serial"],
            serial_norm=row["serial_norm"],
            mac=row["mac"],
            mac_norm=row["mac_norm"],
            ip=row["ip"],
            location_label=row["location_label"],
            status=row["status"],
            created_at=row["created_at"] or "",
        )


@dataclass
class Location:
    id: str
    session_id: str
    building: str
    floor: str
    room: str
    rack: str
    unit: str
    description: Optional[str] = None
    created_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> "Location":
        return cls(
            id=row["id"],
            session_id=row["session_id"],
            building=row["building"] or "",
            floor=row["floor"] or "",
            room=row["room"] or "",
            rack=row["rack"] or "",
            unit=row["unit"] or "",
            description=row["description"],
            created_at=row["created_at"],
        )

    def label(self) -> str:
        parts = [
            self.building,
            f"{self.floor} этаж" if self.floor else "",
            self.room,
            f"шкаф {self.rack}" if self.rack else "",
            f"U{self.unit}" if self.unit else "",
        ]
        return ", ".join(p for p in parts if p)

    def short_label(self) -> str:
        parts = [p for p in (self.floor, self.room, self.rack) if p]
        return " · ".join(parts) if parts else "Локация не выбрана"


@dataclass
class Device:
    id: str
    session_id: str
    display_id: str
    device_type: str
    location_id: str
    status: str
    created_at: str
    updated_at: str
    asset_tag: Optional[str] = None
    subtype: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial: Optional[str] = None
    serial_norm: Optional[str] = None
    mac: Optional[str] = None
    mac_norm: Optional[str] = None
    ip: Optional[str] = None
    power_type: Optional[str] = None
    note: Optional[str] = None
    photo_path: Optional[str] = None
    scanned_code: Optional[str] = None
    code_type: Optional[str] = None
    attributes_json: Optional[str] = None
    is_verified: int = 0
    nvr_channel: Optional[str] = None
    deleted_at: Optional[str] = None
    location: Optional[Location] = None
    upstream_device_id: Optional[str] = None
    upstream_display_id: Optional[str] = None
    upstream_port: Optional[str] = None

    @classmethod
    def from_row(cls, row: Any) -> "Device":
        keys = row.keys()
        return cls(
            id=row["id"],
            session_id=row["session_id"],
            display_id=row["display_id"],
            device_type=row["device_type"],
            location_id=row["location_id"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            asset_tag=row["asset_tag"],
            subtype=row["subtype"],
            manufacturer=row["manufacturer"],
            model=row["model"],
            serial=row["serial"],
            serial_norm=row["serial_norm"],
            mac=row["mac"],
            mac_norm=row["mac_norm"],
            ip=row["ip"],
            power_type=row["power_type"],
            note=row["note"],
            photo_path=row["photo_path"],
            scanned_code=row["scanned_code"],
            code_type=row["code_type"],
            attributes_json=row["attributes_json"],
            is_verified=row["is_verified"] or 0,
            nvr_channel=row["nvr_channel"] if "nvr_channel" in keys else None,
            deleted_at=row["deleted_at"],
            upstream_device_id=row["upstream_device_id"] if "upstream_device_id" in keys else None,
            upstream_display_id=row["upstream_display_id"] if "upstream_display_id" in keys else None,
            upstream_port=row["upstream_port"] if "upstream_port" in keys else None,
        )

    def title(self) -> str:
        parts = [self.display_id]
        if self.manufacturer:
            parts.append(self.manufacturer)
        if self.model:
            parts.append(self.model)
        return " · ".join(parts)


@dataclass
class Connection:
    id: str
    device_id: str
    created_at: str
    updated_at: str
    upstream_device_id: Optional[str] = None
    upstream_port: Optional[str] = None
    cable_type: Optional[str] = None
    note: Optional[str] = None

    @classmethod
    def from_row(cls, row: Any) -> "Connection":
        return cls(
            id=row["id"],
            device_id=row["device_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            upstream_device_id=row["upstream_device_id"],
            upstream_port=row["upstream_port"],
            cable_type=row["cable_type"],
            note=row["note"],
        )


@dataclass
class ScanEvent:
    id: int
    session_id: str
    raw_code: str
    action: str
    created_at: str
    parsed_type: Optional[str] = None
    parsed_value: Optional[str] = None
    device_id: Optional[str] = None

    @classmethod
    def from_row(cls, row: Any) -> "ScanEvent":
        return cls(
            id=row["id"],
            session_id=row["session_id"],
            raw_code=row["raw_code"],
            action=row["action"],
            created_at=row["created_at"],
            parsed_type=row["parsed_type"],
            parsed_value=row["parsed_value"],
            device_id=row["device_id"],
        )


def dataclass_to_dict(obj: Any) -> dict[str, Any]:
    """Преобразует dataclass в словарь, пропуская None."""
    result: dict[str, Any] = {}
    for f in fields(obj):
        value = getattr(obj, f.name)
        if value is not None:
            result[f.name] = value
    return result

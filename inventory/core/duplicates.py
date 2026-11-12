# inventory/core/duplicates.py
"""Проверка дублей устройств по S/N, MAC и инвентарному номеру."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from inventory.core.models import Device
from inventory.core.parser import ParsedCode, normalize_mac_compact, normalize_serial
from inventory.core.repository import InventoryRepository


@dataclass
class DuplicateMatch:
    device: Device
    matched_by: list[str] = field(default_factory=list)

    def message(self) -> str:
        fields = {
            "serial": "S/N",
            "mac": "MAC",
            "asset_tag": "Инв. номер",
        }
        labels = ", ".join(fields.get(item, item) for item in self.matched_by)
        location = self.device.location.label() if self.device.location else ""
        suffix = f" ({location})" if location else ""
        return f"{self.device.display_id} — совпадение по {labels}{suffix}"


def check_duplicates(
    repo: InventoryRepository,
    session_id: str,
    *,
    parsed: ParsedCode | None = None,
    serial: str | None = None,
    mac: str | None = None,
    asset_tag: str | None = None,
    exclude_id: str | None = None,
) -> list[DuplicateMatch]:
    """Совпадения по S/N, MAC или инвентарному номеру (бирка «INV=…»).

    EAN дублем устройства не считается.
    """
    if parsed is not None:
        # Товарный код не идентифицирует конкретное устройство.
        if parsed.code_type.value == "ean":
            return []
        serial = serial or parsed.fields.get("serial")
        mac = mac or parsed.fields.get("mac")
        asset_tag = asset_tag or parsed.fields.get("asset_tag")

    serial_norm = normalize_serial(serial)
    mac_norm = normalize_mac_compact(mac)
    asset_norm = (asset_tag or "").strip().upper() or None
    if not serial_norm and not mac_norm and not asset_norm:
        return []

    devices = repo.find_duplicates(
        session_id,
        serial_norm=serial_norm,
        mac_norm=mac_norm,
        asset_tag=asset_norm,
        exclude_id=exclude_id,
    )
    result: list[DuplicateMatch] = []
    for device in devices:
        matched_by: list[str] = []
        if serial_norm and device.serial_norm == serial_norm:
            matched_by.append("serial")
        if mac_norm and device.mac_norm == mac_norm:
            matched_by.append("mac")
        if asset_norm and (device.asset_tag or "").strip().upper() == asset_norm:
            matched_by.append("asset_tag")
        result.append(DuplicateMatch(device=device, matched_by=matched_by))
    return result


@dataclass
class DuplicateGroup:
    """Группа устройств сессии, у которых совпадает S/N или MAC."""

    devices: list[Device] = field(default_factory=list)
    matched_by: list[str] = field(default_factory=list)  # «S/N» и/или «MAC»

    def label(self) -> str:
        return " + ".join(self.matched_by)


def find_duplicate_groups(repo: InventoryRepository, session_id: str) -> list[DuplicateGroup]:
    """Все подозрительные группы в сессии одним проходом.

    Устройства, совпадающие по S/N или MAC, объединяются в группы; пересекающиеся
    совпадения (один S/N на тройку, у одного из них ещё и MAC-сосед) дают одну группу.
    Удалённые записи не участвуют.
    """
    devices = repo.list_devices(session_id)
    serial_index: dict[str, list[Device]] = defaultdict(list)
    mac_index: dict[str, list[Device]] = defaultdict(list)
    for device in devices:
        if device.serial_norm:
            serial_index[device.serial_norm].append(device)
        if device.mac_norm:
            mac_index[device.mac_norm].append(device)
    if not serial_index and not mac_index:
        return []

    parent: dict[str, str] = {}

    def find(item_id: str) -> str:
        root = item_id
        parent.setdefault(root, root)
        while parent[root] != root:
            root = parent[root]
        while parent[item_id] != root:
            parent[item_id], item_id = root, parent[item_id]
        return root

    def union(left_id: str, right_id: str) -> None:
        left_root, right_root = find(left_id), find(right_id)
        if left_root != right_root:
            parent[right_root] = left_root

    for index in (serial_index, mac_index):
        for members in index.values():
            for other in members[1:]:
                union(members[0].id, other.id)

    clusters: dict[str, list[Device]] = defaultdict(list)
    for device in devices:
        if device.serial_norm or device.mac_norm:
            clusters[find(device.id)].append(device)

    groups: list[DuplicateGroup] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        matched_by: list[str] = []
        serial_counts = Counter(d.serial_norm for d in members if d.serial_norm)
        mac_counts = Counter(d.mac_norm for d in members if d.mac_norm)
        if any(count > 1 for count in serial_counts.values()):
            matched_by.append("S/N")
        if any(count > 1 for count in mac_counts.values()):
            matched_by.append("MAC")
        groups.append(DuplicateGroup(devices=members, matched_by=matched_by))
    return groups

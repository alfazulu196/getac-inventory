# tools/export_netbox.py
r"""Экспорт инвентаризации в bulk-import NetBox (CSV + полный JSON).

Модель данных и заголовки CSV сверены с исходниками NetBox
(netbox/dcim/forms/bulk_import.py: DeviceImportForm, DeviceTypeImportForm,
RackImportForm, ManufacturerImportForm, DeviceRoleImportForm,
SiteImportForm, LocationImportForm):
  - позиция устройства = НИЖНИЙ занимаемый юнит (конвенция NetBox:
    «3U-устройство занимает U8..U10 -> position 8»);
  - стойка без rack_type обязана иметь width и u_height (заполняются);
  - devices.csv ссылается на device_types.csv по manufacturer + model.

Порядок импорта (в NetBox: Import нужного типа, по одному файлу):
  1) sites.csv  2) locations.csv  3) racks.csv  4) roles.csv
  5) manufacturers.csv  6) device_types.csv  7) devices.csv

Позиция юнита берётся из пометки приложения «В стойке: U5-U8» (note)
или из attributes_json["position"]. Производитель без значения
попадает в «Unknown», модель без значения — в «Unknown».

Запуск:
  python -X utf8 tools\export_netbox.py
  python -X utf8 tools\export_netbox.py --db data\inventory.db --out data\exports\netbox
  python -X utf8 tools\export_netbox.py --session <id> --rack-height 42
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # noqa: E402

from inventory.config import DEVICE_TYPES  # noqa: E402

# Наши статусы -> DeviceStatusChoices NetBox (по смыслу).
STATUS_MAP = {
    "installed": "active",
    "faulty": "failed",
    "replace": "offline",
    "spare": "inventory",
    "missing": "offline",
    "removed": "decommissioning",
}
DEFAULT_MANUFACTURER = "Unknown"
NOTE_POS_RE = re.compile(r"В стойке:\s*([^\n;]+)")
UNIT_RE = re.compile(r"U(\d+)\s*(?:[-–—]\s*U?(\d+))?", re.IGNORECASE)

# Кириллица → ASCII (транслит). Публичный: переиспользуется в tools/tia606.py
# для сегментов меток TIA-606.
TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

CSV_HEADERS: dict[str, list[str]] = {
    "sites.csv": ["name", "slug", "status"],
    "locations.csv": ["site", "name", "slug", "status"],
    "racks.csv": ["site", "location", "name", "status", "width", "u_height"],
    "roles.csv": ["name", "slug", "description"],
    "manufacturers.csv": ["name", "slug"],
    "device_types.csv": ["manufacturer", "model", "slug", "u_height"],
    "devices.csv": [
        "site", "location", "rack", "position", "face", "name", "role",
        "manufacturer", "device_type", "serial", "asset_tag", "status",
        "description", "comments",
    ],
}


def slugify(text: str) -> str:
    """ASCII-slug: кириллица транслитом, прочее -> '-', максимум 50 символов."""
    buf = []
    for ch in (text or "").lower():
        if ch in TRANSLIT:
            buf.append(TRANSLIT[ch])
        elif ch.isascii() and ch.isalnum():
            buf.append(ch)
        else:
            buf.append("-")
    slug = re.sub(r"-{2,}", "-", "".join(buf)).strip("-")
    return (slug[:50].rstrip("-")) or "x"


def unique_slug(base: str, taken: set[str]) -> str:
    """Slug без коллизий внутри набора (NetBox требует глобальную уникальность)."""
    slug = slugify(base)
    i = 2
    while slug in taken:
        tail = f"-{i}"
        slug = f"{slug[:50 - len(tail)].rstrip('-')}{tail}"
        i += 1
    taken.add(slug)
    return slug


def parse_position(text: str | None) -> tuple[int, int] | None:
    """«U5-U8» -> (5, 4); «U5» -> (5, 1); без юнитов -> None."""
    m = UNIT_RE.search(text or "")
    if not m:
        return None
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else start
    if end < start:
        start, end = end, start
    return start, max(end - start + 1, 1)


def position_from_device(row: dict, attributes: dict) -> tuple[int, int] | None:
    """Позиция устройства: attributes_json["position"], затем «В стойке: …»."""
    raw = (attributes or {}).get("position")
    if raw:
        parsed = parse_position(str(raw))
        if parsed:
            return parsed
    m = NOTE_POS_RE.search(row.get("note") or "")
    if m:
        return parse_position(m.group(1))
    return None


def _room_name(loc: dict) -> str:
    parts = [loc.get("building") or "", loc.get("floor") or "",
             loc.get("room") or ""]
    name = " - ".join(p.strip() for p in parts if p.strip())
    return name or "Без помещения"


def _safe_attrs(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def collect(db_path: Path, session_id: str | None = None) -> dict:
    """Читает БД read-only: сессии, локации, живые устройства."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if session_id:
            sessions = [dict(r) for r in conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,))]
        else:
            sessions = [dict(r) for r in conn.execute(
                "SELECT * FROM sessions ORDER BY started_at")]
        ids = [s["id"] for s in sessions]
        if not ids:
            return {"sessions": [], "locations": [], "devices": []}
        marks = ",".join("?" for _ in ids)
        locations = [dict(r) for r in conn.execute(
            f"SELECT * FROM locations WHERE session_id IN ({marks}) "
            "ORDER BY created_at", ids)]
        devices = [dict(r) for r in conn.execute(
            f"SELECT * FROM devices WHERE session_id IN ({marks}) "
            "AND deleted_at IS NULL ORDER BY display_id", ids)]
        return {"sessions": sessions, "locations": locations, "devices": devices}
    finally:
        conn.close()


def build_export(data: dict, rack_height: int = 42,
                 rack_width: str = "19") -> dict:
    """Собирает строки всех CSV + полный JSON из записей БД.

    Локация NetBox = помещение (building/floor/room); стойки — внутри
    локации. Позиция и высота типа вычисляются из «В стойке: U5-U8».
    """
    role_desc = {dt.code: dt.display_name for dt in DEVICE_TYPES}
    taken: set[str] = set()  # slug-и уникальны глобально, как в NetBox

    sites: list[dict] = []
    site_by_session: dict[str, dict] = {}
    for s in data["sessions"]:
        name = (s.get("object_name") or s.get("name") or "Объект").strip()
        site = {"name": name, "slug": unique_slug(name, taken),
                "status": "active"}
        sites.append(site)
        site_by_session[s["id"]] = site

    # Локация = помещение; внутри неё — стойки.
    loc_groups: dict[tuple, dict] = {}
    loc_key: dict[str, tuple[dict, str]] = {}
    for loc in data["locations"]:
        site = site_by_session.get(loc["session_id"])
        if site is None:
            continue
        key = (loc["session_id"], loc["building"], loc["floor"], loc["room"])
        group = loc_groups.setdefault(key, {
            "site": site, "name": _room_name(loc), "slug": "",
            "rack_names": [],
        })
        rack_name = (loc["rack"] or "").strip()
        if rack_name and rack_name not in group["rack_names"]:
            group["rack_names"].append(rack_name)
        loc_key[loc["id"]] = (group, rack_name)
    for group in loc_groups.values():
        group["slug"] = unique_slug(group["name"], taken)

    locations = [
        {"site": g["site"]["name"], "name": g["name"], "slug": g["slug"],
         "status": "active"}
        for g in loc_groups.values()
    ]

    racks: list[dict] = []
    for group in loc_groups.values():
        for rack_name in group["rack_names"]:
            racks.append({
                "site": group["site"]["name"],
                "location": group["name"],
                "name": rack_name,
                "status": "active",
                "width": rack_width,
                "u_height": rack_height,
            })

    devices = data["devices"]
    used_codes = sorted({(d["device_type"] or "other") for d in devices})
    codes = list(dict.fromkeys(
        [dt.code for dt in DEVICE_TYPES]
        + [c for c in used_codes if c not in role_desc]))
    roles = [
        {"name": code, "slug": unique_slug(code, taken),
         "description": role_desc.get(code, code)}
        for code in codes
    ]

    mfr_names: list[str] = []
    mfr_of: dict[str, str] = {}
    for d in devices:
        mfr = (d["manufacturer"] or "").strip() or DEFAULT_MANUFACTURER
        mfr_of[d["id"]] = mfr
        if mfr not in mfr_names:
            mfr_names.append(mfr)
    manufacturers = [
        {"name": name, "slug": unique_slug(name, taken)}
        for name in mfr_names
    ]

    model_of: dict[str, str] = {}
    dt_heights: dict[tuple[str, str], int] = {}
    for d in devices:
        model = (d["model"] or "").strip() or "Unknown"
        model_of[d["id"]] = model
        attrs = _safe_attrs(d["attributes_json"])
        pos = position_from_device(d, attrs)
        height = pos[1] if pos else 1
        key = (mfr_of[d["id"]], model)
        dt_heights[key] = max(dt_heights.get(key, 1), height)
    device_types = [
        {"manufacturer": mfr, "model": model,
         "slug": unique_slug(model, taken), "u_height": height}
        for (mfr, model), height in sorted(dt_heights.items())
    ]

    device_rows: list[dict] = []
    for d in devices:
        group, rack_name = loc_key.get(d["location_id"], (None, ""))
        site_name = group["site"]["name"] if group else ""
        loc_name = group["name"] if group else ""
        attrs = _safe_attrs(d["attributes_json"])
        pos = position_from_device(d, attrs)
        comments = "; ".join(
            f"{label}: {value}" for label, value in (
                ("MAC", d["mac_norm"] or d["mac"] or ""),
                ("IP", d["ip"] or ""),
            ) if value)
        device_rows.append({
            "site": site_name,
            "location": loc_name,
            "rack": rack_name,
            "position": str(pos[0]) if pos and rack_name else "",
            "face": "front" if pos and rack_name else "",
            "name": d["display_id"],
            "role": d["device_type"] or "other",
            "manufacturer": mfr_of[d["id"]],
            "device_type": model_of[d["id"]],
            "serial": d["serial"] or "",
            "asset_tag": d["asset_tag"] or "",
            "status": STATUS_MAP.get(d["status"], "active"),
            "description": (d["note"] or "").strip(),
            "comments": comments,
        })

    full = {
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "InventorySurvey (schema v4)",
        "netbox_reference": "netbox/dcim/forms/bulk_import.py",
        "sites": sites,
        "locations": locations,
        "racks": racks,
        "roles": roles,
        "manufacturers": manufacturers,
        "device_types": device_types,
        "devices": [
            {**row, "subtype": d.get("subtype"),
             "mac": d["mac"], "ip": d["ip"],
             "power_type": d["power_type"],
             "created_at": d["created_at"]}
            for row, d in zip(device_rows, devices)
        ],
    }
    tables = {
        "sites.csv": sites,
        "locations.csv": locations,
        "racks.csv": racks,
        "roles.csv": roles,
        "manufacturers.csv": manufacturers,
        "device_types.csv": device_types,
        "devices.csv": device_rows,
    }
    return {"tables": tables, "full": full}


README_TEXT = """Экспорт InventorySurvey -> NetBox (bulk-import CSV)

Порядок импорта в NetBox (меню Import соответствующего типа):
  1) sites.csv
  2) locations.csv
  3) racks.csv
  4) roles.csv
  5) manufacturers.csv
  6) device_types.csv
  7) devices.csv

Позиция устройства = НИЖНИЙ занимаемый юнит (конвенция NetBox).
Высота типа устройства (u_height) вычислена из диапазона «В стойке: U5-U8».
Стойки создаются с width=19 и u_height по ключу --rack-height (42 по умолчанию):
без rack_type это обязательные поля импорта.
Заголовки CSV сверены с netbox/dcim/forms/bulk_import.py (DeviceImportForm и др.).
"""


def write_csv_files(out_dir: Path, tables: dict[str, list[dict]]) -> list[Path]:
    """Пишет CSV по заголовкам CSV_HEADERS + README.txt; возвращает пути."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, header in CSV_HEADERS.items():
        rows = tables.get(name, [])
        path = out_dir / name
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            for row in rows:
                writer.writerow([row.get(col, "") for col in header])
        written.append(path)
    readme = out_dir / "README.txt"
    readme.write_text(README_TEXT, encoding="utf-8")
    written.append(readme)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Экспорт инвентаризации в bulk-import NetBox")
    ap.add_argument("--db", default="data/inventory.db",
                    help="путь к базе SQLite (по умолчанию data/inventory.db)")
    ap.add_argument("--out", default="data/exports/netbox",
                    help="папка вывода (по умолчанию data/exports/netbox)")
    ap.add_argument("--session", default=None,
                    help="экспортировать одну сессию по id")
    ap.add_argument("--rack-height", type=int, default=42,
                    help="высота стоек в U (по умолчанию 42)")
    ap.add_argument("--rack-width", default="19", choices=("19", "23"),
                    help="ширина стоек в дюймах (по умолчанию 19)")
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"[X] База не найдена: {db_path}")
        return 1
    data = collect(db_path, args.session)
    if not data["sessions"]:
        print("[X] Сессий не найдено — экспортировать нечего")
        return 1
    result = build_export(data, rack_height=args.rack_height,
                          rack_width=args.rack_width)
    out_dir = Path(args.out)
    write_csv_files(out_dir, result["tables"])
    (out_dir / "netbox_full.json").write_text(
        json.dumps(result["full"], ensure_ascii=False, indent=1),
        encoding="utf-8")
    counts = ", ".join(
        f"{name}: {len(result['tables'][name])}" for name in CSV_HEADERS)
    print(f"[OK] Экспорт NetBox: {out_dir}")
    print(f"     {counts}")
    print("     Порядок импорта — в README.txt рядом с CSV")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# inventory/core/db.py
"""SQLite-хранилище: схема, подключение, транзакции."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 4

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    object_name TEXT NOT NULL,
    inspector TEXT NOT NULL,
    contractor TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    comment TEXT,
    baseline_path TEXT
);

CREATE TABLE IF NOT EXISTS locations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    building TEXT NOT NULL DEFAULT '',
    floor TEXT NOT NULL DEFAULT '',
    room TEXT NOT NULL DEFAULT '',
    rack TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT '',
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    display_id TEXT NOT NULL,
    asset_tag TEXT,
    device_type TEXT NOT NULL,
    subtype TEXT,
    manufacturer TEXT,
    model TEXT,
    serial TEXT,
    serial_norm TEXT,
    mac TEXT,
    mac_norm TEXT,
    ip TEXT,
    location_id TEXT NOT NULL REFERENCES locations(id),
    power_type TEXT,
    status TEXT NOT NULL DEFAULT 'installed',
    note TEXT,
    photo_path TEXT,
    scanned_code TEXT,
    code_type TEXT,
    attributes_json TEXT,
    is_verified INTEGER NOT NULL DEFAULT 0,
    nvr_channel TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    UNIQUE(session_id, display_id)
);

CREATE TABLE IF NOT EXISTS connections (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL UNIQUE REFERENCES devices(id) ON DELETE CASCADE,
    upstream_device_id TEXT REFERENCES devices(id) ON DELETE SET NULL,
    upstream_port TEXT,
    cable_type TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    raw_code TEXT NOT NULL,
    parsed_type TEXT,
    parsed_value TEXT,
    action TEXT NOT NULL,
    device_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_locations_session ON locations(session_id);
CREATE INDEX IF NOT EXISTS idx_devices_session ON devices(session_id);
CREATE INDEX IF NOT EXISTS idx_devices_serial ON devices(serial_norm);
CREATE INDEX IF NOT EXISTS idx_devices_mac ON devices(mac_norm);
CREATE INDEX IF NOT EXISTS idx_devices_type ON devices(device_type);
CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);
CREATE INDEX IF NOT EXISTS idx_devices_location ON devices(location_id);
CREATE INDEX IF NOT EXISTS idx_scan_events_session ON scan_events(session_id);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_photos_device ON photos(device_id);

CREATE TABLE IF NOT EXISTS baseline_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    display_id TEXT,
    device_type TEXT,
    manufacturer TEXT,
    model TEXT,
    serial TEXT,
    serial_norm TEXT,
    mac TEXT,
    mac_norm TEXT,
    ip TEXT,
    location_label TEXT,
    status TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_baseline_session ON baseline_devices(session_id);
"""

# Пошаговая миграция: заявление ALTER выполняются только один раз.
MIGRATIONS: dict[int, list[str]] = {
    2: ["ALTER TABLE sessions ADD COLUMN baseline_path TEXT"],
    3: ["ALTER TABLE devices ADD COLUMN nvr_channel TEXT"],
    4: ["ALTER TABLE sessions ADD COLUMN contractor TEXT"],
}


def now_iso() -> str:
    """Локальное время в ISO 8601 с часовым поясом."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Database:
    """Обёртка над sqlite3 с WAL, внешними ключами и транзакциями."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.initialize()

    def initialize(self) -> None:
        with self.conn:
            self.conn.executescript(SCHEMA_SQL)
            row = self.conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            current = row["version"] if row else 0
            if current > SCHEMA_VERSION:
                raise RuntimeError(
                    f"База создана более новой версией приложения: "
                    f"{current} > {SCHEMA_VERSION}"
                )
            for from_version in range(current, SCHEMA_VERSION):
                statements = MIGRATIONS.get(from_version + 1, [])
                for statement in statements:
                    try:
                        self.conn.execute(statement)
                    except sqlite3.OperationalError:
                        # Колонка/объект уже существуют (например, после сбоя миграции) — не страшно.
                        pass
            if row is None:
                self.conn.execute(
                    "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                    (SCHEMA_VERSION, now_iso()),
                )
            elif current < SCHEMA_VERSION:
                self.conn.execute(
                    "UPDATE schema_version SET version = ?, applied_at = ?",
                    (SCHEMA_VERSION, now_iso()),
                )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Транзакция: commit при успехе, rollback при ошибке."""
        try:
            self.conn.execute("BEGIN")
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, params: list[tuple]) -> sqlite3.Cursor:
        return self.conn.executemany(sql, params)

    def fetchone(self, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

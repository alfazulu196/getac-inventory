# inventory/services/backup.py
"""Резервное копирование и восстановление SQLite."""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


def backup_database(
    db_path: str | Path,
    backups_dir: str | Path,
    prefix: str = "inventory",
) -> Path | None:
    """Создаёт согласованную копию базы через sqlite backup API."""
    db_path = Path(db_path)
    backups_dir = Path(backups_dir)
    if not db_path.exists():
        return None
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = backups_dir / f"{prefix}_{stamp}.db"
    source = sqlite3.connect(str(db_path))
    target = sqlite3.connect(str(backup_path))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return backup_path


def prune_backups(backups_dir: str | Path, keep: int = 30) -> list[Path]:
    backups_dir = Path(backups_dir)
    if not backups_dir.exists():
        return []
    files = sorted(
        (path for path in backups_dir.glob("inventory_*.db") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed: list[Path] = []
    for path in files[keep:]:
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            pass
    return removed


def restore_database(
    backup_path: str | Path,
    db_path: str | Path,
    safety_dir: str | Path | None = None,
) -> Path | None:
    """Восстанавливает базу из копии, сохраняя текущую как safety-копию."""
    backup_path = Path(backup_path)
    db_path = Path(db_path)
    if not backup_path.exists():
        raise FileNotFoundError(backup_path)

    safety_path: Path | None = None
    if db_path.exists() and safety_dir:
        safety_dir = Path(safety_dir)
        safety_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safety_path = safety_dir / f"before_restore_{stamp}.db"
        shutil.copy2(db_path, safety_path)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_path, db_path)
    return safety_path

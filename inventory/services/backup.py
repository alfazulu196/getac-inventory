# inventory/services/backup.py
"""Резервное копирование и восстановление SQLite + SHA-256-манифест.

Манифест (manifest.json рядом с копиями) хранит для каждой копии
sha256/размер/время: по нему можно проверить, что архив не побит и не
подменён — копия базы единственный шанс откатиться в поле.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

MANIFEST_NAME = "manifest.json"


def _hash_file(path: Path) -> str:
    with path.open("rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def _read_manifest(backups_dir: Path) -> list[dict]:
    try:
        data = json.loads((backups_dir / MANIFEST_NAME).read_text("utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("backups") if isinstance(data, dict) else None
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _write_manifest(backups_dir: Path, entries: list[dict]) -> None:
    entries.sort(key=lambda e: str(e.get("file")), reverse=True)
    payload = {"version": 1, "backups": entries}
    path = backups_dir / MANIFEST_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)  # атомарная замена: прерванный save не побит


def _register_backup(backups_dir: Path, backup_path: Path) -> None:
    entries = [
        e for e in _read_manifest(backups_dir)
        if e.get("file") != backup_path.name
    ]
    entries.append(
        {
            "file": backup_path.name,
            "sha256": _hash_file(backup_path),
            "size": backup_path.stat().st_size,
            "created": datetime.now().isoformat(timespec="seconds"),
        }
    )
    _write_manifest(backups_dir, entries)


def backup_database(
    db_path: str | Path,
    backups_dir: str | Path,
    prefix: str = "inventory",
) -> Path | None:
    """Создаёт согласованную копию базы через sqlite backup API.

    Копия сразу регистрируется в SHA-256-манифесте.
    """
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
    try:
        _register_backup(backups_dir, backup_path)
    except OSError:
        pass  # копия важнее манифеста; verify пометит её как без записи
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
    # Манифест синхронизируем с выжившими: записи пропавших копий — вон.
    surviving = {path.name for path in files[:keep]}
    entries = [
        e for e in _read_manifest(backups_dir)
        if e.get("file") in surviving
    ]
    try:
        _write_manifest(backups_dir, entries)
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


def verify_manifest(backups_dir: str | Path) -> list[str]:
    """Проверка целостности по манифесту → список проблем (пусто = всё в порядке).

    Ищет: не читающийся/побитый файл (SHA-256 не совпал), пропавший файл
    с записью в манифесте, копию без записи (сделана до манифеста или
    подложенную), лишнюю запись.
    """
    backups_dir = Path(backups_dir)
    if not backups_dir.exists():
        return []
    entries: dict[str, dict] = {}
    for entry in _read_manifest(backups_dir):
        name = str(entry.get("file") or "")
        if name:
            entries[name] = entry
    issues: list[str] = []
    for path in sorted(backups_dir.glob("*.db")):
        entry = entries.pop(path.name, None)
        if entry is None:
            issues.append(f"{path.name}: нет записи в манифесте")
            continue
        expected = str(entry.get("sha256") or "")
        if not expected:
            issues.append(f"{path.name}: в записи нет SHA-256")
            continue
        try:
            actual = _hash_file(path)
        except OSError as exc:
            issues.append(f"{path.name}: не читается ({exc.strerror or exc})")
            continue
        if actual != expected:
            issues.append(
                f"{path.name}: SHA-256 не совпал — файл изменён или повреждён"
            )
    for name in sorted(entries):
        issues.append(f"{name}: файла нет на диске")
    return issues

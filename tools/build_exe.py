# -*- coding: utf-8 -*-
"""Сборка exe-приложения инвентаризации для Getac V110.

Запуск из корня проекта:
    python tools/build_exe.py                # onefile: один exe + самотест + zip
    python tools/build_exe.py --onedir       # папка: старт мгновенный, без распаковки
    python tools/build_exe.py --skip-build   # только упаковка release (без пересборки)

Результат (onefile): папка release/ с файлами
  InventorySurvey.exe  — приложение (один файл, всё внутри)
  model/               — модель голосового распознавания Vosk (если найдена)
  ПРОЧТИ_МЕНЯ.txt      — краткая инструкция для ноутбука
  getac_inventory.zip  — архив для переноса на ноутбук (без runtime-файлов)

Результат (--onedir): то же, но exe публикуется как папка InventorySurvey/ и
архив называется getac_inventory_onedir.zip.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from inventory.config import APP_VERSION  # noqa: E402

EXE_NAME = "InventorySurvey"
DIST_DIR = ROOT / "dist"
RELEASE_DIR = ROOT / "release"
README_NAME = "ПРОЧТИ_МЕНЯ.txt"
ZIP_NAME = "getac_inventory.zip"
ZIP_ONEDIR_NAME = "getac_inventory_onedir.zip"

README_TEXT = """Инвентаризация объекта — сборка для полевого ноутбука (Getac V110)
=====================================================================
Версия сборки: {version}

1. Распакуйте ВСЮ папку на ноутбук (не запускайте из архива!).
2. Запустите InventorySurvey.exe{start_note}
3. Рядом с exe появятся:
     config.json   — настройки (сканер, голос)
     data\\         — база inventory.db, photos\\, exports\\, backups\\, logs\\

Голосовые заметки:
    Папка model\\ — офлайн-модель распознавания речи (Vosk).
    Если model\\ убрать — голос будет недоступен, остальное работает.

Перенос результатов на основной компьютер:
    data\\exports\\*.xlsx  — Excel-отчёты
    data\\inventory.db    — вся база (можно целиком копировать)
    data\\backups\\        — автоматические копии базы
{speed_note}
"""


def run_build(onedir: bool) -> Path:
    """Собирает exe через PyInstaller, возвращает путь к exe."""
    log_path = ROOT / "build" / "pyinstaller.log"
    log_path.parent.mkdir(exist_ok=True)
    print("Сборка exe (PyInstaller, %s)... лог: %s" % ("onedir" if onedir else "onefile", log_path))
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--windowed", "--noupx",
        "--onefile" if not onedir else "--onedir",
        "--name", EXE_NAME,
        "--collect-all", "vosk",          # внутри vosk — libvosk.dll
        "--collect-all", "pyzbar",        # внутри pyzbar — libzbar-64.dll (штрихкоды)
        "--collect-all", "winrt",         # WinRT-пакеты (встроенный OCR этикеток)
        "--add-data",                     # полная база производителей IEEE (OUI)
        str(ROOT / "inventory" / "data" / "oui.csv") + ";" + "inventory/data",
        "--exclude-module", "pandas",
        "--exclude-module", "tabulate",
        "--exclude-module", "requests",
        "--exclude-module", "pytest",
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        str(ROOT / "run_inventory.py"),
    ]
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False)
    if proc.returncode != 0:
        tail = log_path.read_text(encoding="utf-16-le" if False else "utf-8", errors="replace")[-3000:]
        sys.stderr.write(tail)
        raise SystemExit(f"Сборка не удалась, код {proc.returncode}")
    exe = exe_path(onedir)
    size_mb = (
        (exe.stat().st_size / (1024 * 1024))
        if exe.is_file()
        else sum(p.stat().st_size for p in exe.rglob("*") if p.is_file()) / (1024 * 1024)
    )
    print(f"Собрано: {exe} ({size_mb:.0f} МБ)")
    return exe


def exe_path(onedir: bool) -> Path:
    """Путь к exe в dist: у onedir — внутри одноимённой папки."""
    if onedir:
        return DIST_DIR / EXE_NAME / f"{EXE_NAME}.exe"
    return DIST_DIR / f"{EXE_NAME}.exe"


def loaded_dir(onedir: bool) -> Path:
    """Папка, где exe создаёт config.json и data (база рядом с exe)."""
    return (RELEASE_DIR / EXE_NAME) if onedir else RELEASE_DIR


def prepare_release(exe: Path | None, with_model: bool, onedir: bool) -> None:
    """Готовит release/: exe, модель Vosk, краткая инструкция.

    Уже собранные zip другой сборки сохраняются, чтобы можно было собрать
    обе поставки подряд (onefile и onedir) без перезапуска сборок.
    """
    kept_zips: dict[str, Path] = {}
    if RELEASE_DIR.exists():
        for name in (ZIP_NAME, ZIP_ONEDIR_NAME):
            old = RELEASE_DIR / name
            if old.exists():
                kept_zips[name] = Path(str(Path(tempfile.gettempdir()) / name))
                shutil.copy2(old, kept_zips[name])
        shutil.rmtree(RELEASE_DIR)
    RELEASE_DIR.mkdir(parents=True)
    if exe is None:
        exe = exe_path(onedir)
        if not exe.exists():
            raise SystemExit("exe не найден; сначала выполните сборку без --skip-build")
    if onedir:
        shutil.copytree(
            exe.parent,
            RELEASE_DIR / EXE_NAME,
            ignore=shutil.ignore_patterns(".pytest_cache"),
        )
    else:
        shutil.copy2(exe, RELEASE_DIR / exe.name)

    for name, saved in kept_zips.items():
        shutil.copy2(saved, RELEASE_DIR / name)

    model_src = ROOT / "model"
    if with_model and model_src.is_dir():
        shutil.copytree(model_src, RELEASE_DIR / "model")
        print("Модель Vosk скопирована в release/model")
    write_readme(onedir)
    # На случай прошлых запусков: runtime-артефакты в архив не пакуем.
    _remove_runtime_junk()


def write_readme(onedir: bool) -> None:
    if onedir:
        start_note = "\n   В этой сборке exe лежит в папке InventorySurvey\\ — запуск мгновенный."
        speed_note = "\nСборка «папка»: старт приложения мгновенный; файлы уже распакованы."
    else:
        start_note = ""
        speed_note = (
            "\nПервый запуск exe может занять несколько секунд — это нормально"
            "\n(файл самораспаковывается во временную папку)."
        )
    text = README_TEXT.format(
        version=APP_VERSION, start_note=start_note, speed_note=speed_note
    )
    (RELEASE_DIR / README_NAME).write_text(text, encoding="utf-8")


def _remove_runtime_junk() -> None:
    # Runtime-артефакты создаёт exe рядом с собой: у onefile — в release/,
    # у onedir — внутри release/InventorySurvey/.
    for base in (RELEASE_DIR, RELEASE_DIR / EXE_NAME):
        if not base.is_dir():
            continue
        for junk in ("config.json", "data"):
            target = base / junk
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()


def smoke_test(app_dir: Path, wait_seconds: int = 30) -> None:
    """Офлайн-запуск exe: процесс жив, база и config созданы — значит пути верны."""
    import os

    exe = app_dir / f"{EXE_NAME}.exe"
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
    print(f"Самотест exe (offscreen, до {wait_seconds} с, рабочая папка {app_dir.name})...")
    proc = subprocess.Popen([str(exe)], cwd=app_dir, env=env)
    try:
        time.sleep(wait_seconds)
        alive = proc.poll() is None
        db_ok = (app_dir / "data" / "inventory.db").exists()
        cfg_ok = (app_dir / "config.json").exists()
        if not alive:
            raise SystemExit("exe завершился сразу после запуска")
        if not (db_ok and cfg_ok):
            missing = [name for name, ok in (("data/inventory.db", db_ok), ("config.json", cfg_ok)) if not ok]
            raise SystemExit("exe работает, но не создал: " + ", ".join(missing))
        print("Самотест ок: процесс жив, config.json и data/inventory.db созданы рядом с exe")
    finally:
        _kill_tree(proc)


def _kill_tree(proc: subprocess.Popen) -> None:
    """Убираем процесс целиком: у onefile-загрузчика есть дочерний процесс."""
    try:
        import psutil
    except ImportError:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        return
    try:
        parent = psutil.Process(proc.pid)
        procs = [parent, *parent.children(recursive=True)]
        for item in procs:
            try:
                item.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(procs, timeout=10)
        for item in alive:
            try:
                item.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(procs, timeout=5)
    except psutil.NoSuchProcess:
        pass


def make_zip(onedir: bool) -> None:
    """Пакует release/ в zip, не включая в него сами zip-файлы.

    Важный нюанс: shutil.make_archive открывает целевой zip ДО обхода каталога,
    поэтому архив, лежащий внутри release/, начинает включать сам себя и
    раздувается до гигабайтов. Пакуем через staging-папку вне release/.
    """
    _remove_runtime_junk()
    zip_name = ZIP_ONEDIR_NAME if onedir else ZIP_NAME
    staging = ROOT / "build" / "_zip_stage"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for item in RELEASE_DIR.iterdir():
        if item.suffix.lower() == ".zip":
            continue
        if item.is_dir():
            shutil.copytree(item, staging / item.name)
        else:
            shutil.copy2(item, staging / item.name)
    zip_base = staging / Path(zip_name).stem
    if Path(str(zip_base) + ".zip").exists():
        zip_path = Path(str(zip_base) + ".zip")
        zip_path.unlink()
    shutil.move(str(Path(shutil.make_archive(str(zip_base), "zip", root_dir=staging))), str(RELEASE_DIR / zip_name))
    shutil.rmtree(staging)


def main() -> int:
    parser = argparse.ArgumentParser(description="Сборка exe для Getac V110")
    parser.add_argument(
        "--onedir", action="store_true",
        help="сборка папкой (мгновенный старт) вместо одного exe"
    )
    parser.add_argument("--skip-build", action="store_true", help="не пересобирать exe, только упаковка")
    parser.add_argument("--no-model", action="store_true", help="не копировать модель Vosk")
    parser.add_argument("--skip-selftest", action="store_true", help="не запускать офлайн-самотест")
    parser.add_argument("--no-zip", action="store_true", help="не создавать zip")
    args = parser.parse_args()

    exe = None
    if not args.skip_build:
        exe = run_build(args.onedir)
    prepare_release(exe, with_model=not args.no_model, onedir=args.onedir)

    if not args.skip_selftest:
        smoke_test(loaded_dir(args.onedir))

    if not args.no_zip:
        make_zip(args.onedir)
        zip_name = ZIP_ONEDIR_NAME if args.onedir else ZIP_NAME
        zip_path = RELEASE_DIR / zip_name
        zip_mb = zip_path.stat().st_size / (1024 * 1024)
        print(f"Архив готов: {zip_path} ({zip_mb:.0f} МБ)")

    print(f"Готово. Версия сборки: {APP_VERSION}. Состав release/:")
    for item in sorted(RELEASE_DIR.iterdir()):
        print("  " + item.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())

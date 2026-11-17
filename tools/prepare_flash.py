# tools/prepare_flash.py
r"""Заливка флешки для выезда одной командой (ЧЕК_ЛИСТ_ВЫЕЗД.md, раздел 1).

Копирует на целевой диск ровно то, что нужно в поле, и сверит SHA-256:
  - модель qwen2.5vl:3b — манифест + ТОЛЬКО blobs, на которые он ссылается
    (~3 ГБ; в локальной библиотеке бывают чужие модели с экспериментов,
    скрипт их не трогает);
  - release\getac_inventory_onedir.zip (или --onefile: getac_inventory.zip);
  - полевые скрипты check_ollama.bat / backup_data.bat / backup_loop.bat;
  - ЧЕК_ЛИСТ_ВЫЕЗД.md -> CHEK_LIST_VYEZD.md (ASCII-имя: на него ссылается
    check_ollama.bat на флешке);
  - OllamaSetup.exe, если найден (рядом с репо или в Downloads).

Имя ollama-blob — это его SHA-256, поэтому каждый blob после копирования
пересчитывается: побитая флешка ловится на месте, а не в поле.

Запуск: python -X utf8 tools\prepare_flash.py E:
        (или tools\prepare_flash.bat E:)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

CHUNK = 4 * 1024 * 1024
FIELD_BATS = ("check_ollama.bat", "backup_data.bat", "backup_loop.bat")


def human(n: float) -> str:
    if n >= 1024**3:
        return f"{n / 1024**3:.2f} ГБ"
    if n >= 1024**2:
        return f"{n / 1024**2:.1f} МБ"
    return f"{n / 1024:.0f} КБ"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_dest(raw: str) -> Path:
    d = raw.strip().strip('"')
    if re.fullmatch(r"[A-Za-z]", d):
        d += ":\\"
    elif re.fullmatch(r"[A-Za-z]:", d):
        d += "\\"
    return Path(d)


def model_paths(ollama_dir: Path, model: str) -> tuple[Path, Path]:
    lib, _, tag = model.partition(":")
    if "/" in lib:  # пользовательская модель owner/name:tag
        owner, name = lib.split("/", 1)
        manifest = (
            ollama_dir / "manifests" / "registry.ollama.ai" / owner / name / tag
        )
    else:
        manifest = (
            ollama_dir / "manifests" / "registry.ollama.ai" / "library" / lib / tag
        )
    return manifest, ollama_dir / "blobs"


def manifest_digests(manifest: Path) -> list[str]:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    digests: list[str] = []
    cfg = (data.get("config") or {}).get("digest")
    if cfg:
        digests.append(cfg)
    for layer in data.get("layers") or []:
        if layer.get("digest"):
            digests.append(layer["digest"])
    return sorted(set(digests))


def find_installer(root: Path) -> Path | None:
    candidates = [
        root.parent / "OllamaSetup.exe",
        root / "OllamaSetup.exe",
        Path.home() / "Downloads" / "OllamaSetup.exe",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Заливка флешки выезда (см. ЧЕК_ЛИСТ_ВЫЕЗД.md, раздел 1)")
    ap.add_argument("dest", help="буква диска или папка, например E:")
    ap.add_argument("--model", default="qwen2.5vl:3b",
                    help="модель ollama (по умолчанию qwen2.5vl:3b)")
    ap.add_argument("--onefile", action="store_true",
                    help="брать getac_inventory.zip вместо onedir-версии")
    ap.add_argument(
        "--ollama-dir", default=str(Path.home() / ".ollama" / "models"),
        help="папка локальных моделей ollama")
    ap.add_argument("--release-dir", default=None,
                    help="папка со сборкой (по умолчанию <репо>\\release)")
    ap.add_argument("--installer", default=None,
                    help="путь к OllamaSetup.exe (по умолчанию — авто-поиск)")
    ap.add_argument("--no-installer", action="store_true",
                    help="не копировать установщик ollama")
    ap.add_argument("--no-verify", action="store_true",
                    help="не сверять SHA-256 после копирования (не советую)")
    ap.add_argument("--dry-run", action="store_true",
                    help="показать план и размеры, ничего не копируя")
    args = ap.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    ollama_dir = Path(args.ollama_dir)
    release_dir = Path(args.release_dir) if args.release_dir else root / "release"
    zip_name = (
        "getac_inventory.zip" if args.onefile else "getac_inventory_onedir.zip"
    )
    verify = not args.no_verify

    dest_path = resolve_dest(args.dest)
    if not dest_path.exists():
        try:
            dest_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"[X] Целевой путь недоступен: {dest_path} ({exc})")
            return 1

    # ---- план копирования: (метка, src, dst, ожидаемый SHA-256) ----
    plan: list[tuple[str, Path, Path, str | None]] = []

    manifest, blobs_dir = model_paths(ollama_dir, args.model)
    if not manifest.is_file():
        print(f"[X] Модель {args.model}: манифест не найден: {manifest}")
        return 1
    plan.append((
        f"модель {args.model} (манифест)", manifest,
        dest_path / "models" / manifest.relative_to(ollama_dir),
        sha256_file(manifest) if verify else None,
    ))
    for i, digest in enumerate(manifest_digests(manifest), 1):
        blob_src = blobs_dir / digest.replace(":", "-")
        hex_hash = digest.split(":", 1)[-1]
        if not blob_src.is_file():
            print(f"[X] Модель {args.model}: нет blob {blob_src.name} — "
                  "библиотека неполна, сначала ollama pull")
            return 1
        if verify and not args.dry_run:
            if sha256_file(blob_src) != hex_hash:
                print(f"[X] Локальный blob повреждён: {blob_src.name} — "
                      "ollama pull заново, на флешку не годится")
                return 1
        plan.append((
            f"модель {args.model} (blob {i})", blob_src,
            dest_path / "models" / "blobs" / blob_src.name, hex_hash,
        ))

    zip_src = release_dir / zip_name
    if not zip_src.is_file():
        print(f"[X] Сборка не найдена: {zip_src} — сначала tools/build_exe.py")
        return 1
    plan.append((
        f"сборка {zip_name}", zip_src, dest_path / zip_name,
        sha256_file(zip_src) if verify else None,
    ))

    for bat in FIELD_BATS:
        bat_src = root / "tools" / bat
        if not bat_src.is_file():
            print(f"[!] Полевой скрипт не найден (копируйте вручную): {bat_src}")
            continue
        plan.append((
            f"скрипт {bat}", bat_src, dest_path / bat,
            sha256_file(bat_src) if verify else None,
        ))

    check_src = root / "ЧЕК_ЛИСТ_ВЫЕЗД.md"
    if check_src.is_file():
        plan.append((
            "чек-лист -> CHEK_LIST_VYEZD.md", check_src,
            dest_path / "CHEK_LIST_VYEZD.md",
            sha256_file(check_src) if verify else None,
        ))

    installer: Path | None = None
    if not args.no_installer:
        installer = (
            Path(args.installer) if args.installer else find_installer(root)
        )
        if installer is None or not installer.is_file():
            print("[!] OllamaSetup.exe не найден — скачайте "
                  "https://ollama.com/download и положите на флешку")
            installer = None
        else:
            plan.append((
                "установщик ollama", installer, dest_path / installer.name,
                sha256_file(installer) if verify else None,
            ))

    total = sum(src.stat().st_size for _, src, _, _ in plan)
    print(f"План: {len(plan)} файлов, {human(total)}")
    for label, src, dst, _ in plan:
        print(f"  {label}: {human(src.stat().st_size)} -> {dst.relative_to(dest_path)}")
    if args.dry_run:
        print("[i] dry-run: ничего не копировано")
        return 0

    free = shutil.disk_usage(dest_path).free
    if free < total + 500 * 1024 * 1024:
        print(f"[X] Мало места: свободно {human(free)}, "
              f"нужно {human(total)} + запас. Освободите флешку.")
        return 1

    copied = errors = 0
    for label, src, dst, expect in plan:
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
        except OSError as exc:
            print(f"[X] {label}: не записан ({exc})")
            errors += 1
            continue
        if expect is not None and sha256_file(dst) != expect:
            print(f"[X] {label}: SHA-256 НЕ сошёлся после записи — "
                  "запись битая, попробуйте другой порт/флешку")
            errors += 1
            continue
        copied += 1
        print(f"[OK] {label} ({human(dst.stat().st_size)})")

    if errors:
        print(f"\n[X] Готово с ошибками: {copied}/{len(plan)}. "
              "С побитыми копиями в поле не выезжать!")
        return 1
    print(f"\n[OK] Флешка готова: {dest_path}")
    print("     Проверка на V110: check_ollama.bat (см. CHEK_LIST_VYEZD.md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# tools/probe_scanner.py
# -*- coding: utf-8 -*-
"""Диагностика COM-сканера на реальном железе (безопасный зонд).

Что делает:
  1. Перечисляет COM-порты и подписывает производителя по VID/PID.
  2. Открывает указанный порт и слушает — не пишет ли модуль что-то сам.
  3. Только с флагом --send: по очереди шлёт известные команды триггера
     лазера с паузами — смотрите на лазер, какая команда его зажгла.
  4. С флагом --record: во время каждой команды сохраняет кадры камеры в
     data\\probe_laser\\ — писать должна та камера, что СМОТРИТ НА ЛАЗЕРНОЕ
     ОКНО СЧИТЫВАТЕЛЯ: внешняя USB (--camera N) или IP-камера (--camera-url
     rtsp://...). Зонд сам ищет «красные» кадры-след луча и пишет вердикт
     в data\\probe_laser\\report.txt.

Запуск из корня проекта:
    python tools/probe_scanner.py                 # только список и прослушивание
    python tools/probe_scanner.py --list-cameras  # контрольный снимок USB-камер 0..3
    python tools/probe_scanner.py --list-cameras --camera-url URL  # тест IP-камеры
    python tools/probe_scanner.py --port COM3     # открыть конкретный порт
    python tools/probe_scanner.py --port COM3 --send   # + попытки зажечь лазер
    python tools/probe_scanner.py --port COM3 --send --record --camera-url URL

Перед --send/--record закройте приложение (оно занимает камеру/порт).
Отправка без --send не выполняется; команды, которые модуль не знает,
он просто игнорирует (или бипит) — это нормальный зонд, не «настройка».
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from inventory.services.scanner import list_serial_ports  # noqa: E402

try:
    import serial  # noqa: E402
except ImportError:  # pragma: no cover
    print("pyserial не установлен: pip install pyserial")
    raise SystemExit(1)

# VID производителей сканерных модулей (первая половина USB id).
KNOWN_VENDORS = {
    0x05E0: "Zebra / Symbol (SNP/SNAPI)",
    0x0C2E: "Honeywell",
    0x0453: "Datalogic",
    0x05F9: "CipherLab",
    0x0403: "FTDI (мост, вендор модуля нужно уточнить по реакции)",
    0x067B: "Prolific (мост)",
    0x1A86: "WCH CH340 (мост)",
    0x10C4: "Silicon Labs CP210x (мост)",
}

# Кандидаты команд «включи лазер» (название, байты).
TRIGGER_CANDIDATES = [
    ("Datalogic SYN+T", b"\x16T"),
    ("Metrologic/Honeywell DC3 (0x13)", b"\x13"),
    ("Honeywell Synapse SOH-T", b"\x01T\r"),
    ("Zebra SNP TRG1", b"TRG1\r"),
    ("Zebra SNAPI TRGON", b"TRGON\r"),
    ("Generic LON", b"LON\r\n"),
    ("CipherLab ~T", b"~T\r"),
]


def hexdump(data: bytes) -> str:
    if not data:
        return "(тишина)"
    return " ".join(f"{b:02X}" for b in data)


def _slugify(name: str) -> str:
    """ASCII-имя файла из названия команды (для путей на диске)."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return slug or "command"


def open_camera(index: int | None = None, url: str | None = None):
    """Открывает камеру: USB по индексу или IP-камеру по rtsp/http URL; None — не удалось."""
    try:
        import cv2  # локальный импорт: зонд должен работать и без OpenCV
    except ImportError:  # pragma: no cover
        print("OpenCV не установлен — запись кадров недоступна (pip install opencv-python).")
        return None
    if url:
        capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    else:
        capture = cv2.VideoCapture(index)
    if not capture.isOpened():
        source = url if url else f"индекс {index}"
        print(f"Камера ({source}) не открылась (занята, нет связи или неверный URL).")
        capture.release()
        return None
    if url:
        # Сетевой поток: сбрасываем устаревшие кадры парой пустых захватов.
        for _ in range(5):
            capture.read()
    return capture


RED_PX = 400  # порог «красных» пикселей в кадре — след лазерного луча


def red_score(frame) -> int:
    """Сколько в кадре ярко-красных пикселей (лазерная полоса/точка)."""
    try:  # кадр в BGR
        b = frame[:, :, 0].astype(int)
        g = frame[:, :, 1].astype(int)
        r = frame[:, :, 2].astype(int)
        return int(((r > 160) & (r > b + 60) & (r > g + 60)).sum())
    except Exception:  # noqa: BLE001 — кадр неожиданной формы
        return 0


def record_window(capture, out_dir: Path, prefix: str, seconds: float, step: float = 0.2):
    """Сохраняет кадры <prefix>_NNN.jpg в течение seconds.

    Возвращает (сохранено, кадров_с_красным, максимум_красных_пикселей) —
    по красному зонд потом сам решает, зажёгся ли луч.
    """
    import cv2

    out_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    red_frames = 0
    max_red = 0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ok, frame = capture.read()
        score = red_score(frame) if ok and frame is not None else 0
        if score > max_red:
            max_red = score
        if score >= RED_PX:
            red_frames += 1
        if ok and frame is not None:
            path = out_dir / f"{prefix}_{saved:03d}.jpg"
            ok_write, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if ok_write:
                try:  # imwrite не берёт кириллические пути на Windows; imencode пишет в память
                    path.write_bytes(encoded.tobytes())
                    saved += 1
                except OSError as exc:
                    print(f"    не записан {path.name}: {exc}")
            else:
                print(f"    OpenCV не смог закодировать {path.name}")
        time.sleep(step)
    if not saved:
        print(
            f"    ({prefix}) кадры не читаются — камера занята, нет связи по URL"
            " или индекс не тот; продолжаю без записи"
        )
    return saved, red_frames, max_red


def verdict_for(red_frames: int, max_red: int, baseline_max: int) -> str:
    """Эвристика «луч виден»: сравниваем с baseline, снятым до команд."""
    if red_frames >= 3 and max_red >= RED_PX and max_red > baseline_max + 200:
        return "🔴 ЛАЗЕР ВИДЕН"
    if red_frames >= 2:
        return "🟡 есть следы красного"
    return "—"


def append_report(out_dir: Path, prefix: str, saved: int, red_frames: int, max_red: int, verdict: str) -> None:
    """Строка в report.txt — итоги автодетекта для разбора и пересылки."""
    line = (
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {prefix:<28} | кадров {saved:>3} |"
        f" красных {red_frames:>3} | max_red {max_red:>6} | {verdict}\n"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "report.txt").open("a", encoding="utf-8") as handle:
        handle.write(line)


def list_cameras(out_dir: Path, indices: tuple[int, ...] = (0, 1, 2, 3), url: str | None = None) -> None:
    """Контрольный снимок камер — чтобы выбрать ту, что смотрит на считыватель."""
    test_dir = out_dir / "camera_test"
    if url:
        capture = open_camera(url=url)
        if capture is None:
            print("IP-камера не открылась: проверьте URL (rtsp://логин:пароль@IP:554/...).")
            return
        saved, _, _ = record_window(capture, test_dir, "ipcam", 1.5, step=0.25)
        capture.release()
        print(f"IP-камера: {saved} контрольных кадров в {test_dir}\\ipcam_*.jpg")
        return
    found = 0
    for index in indices:
        capture = open_camera(index)
        if capture is None:
            continue
        capture.read()  # прогрев захвата
        saved, _, _ = record_window(capture, test_dir, f"cam{index}", 0.6, step=0.2)
        capture.release()
        if saved:
            print(f"  индекс {index}: OK — контрольный кадр {test_dir / f'cam{index}_000.jpg'}")
            found += 1
    if not found:
        print("Ни одна USB-камера не открылась. Закройте приложение и повторите.")
    else:
        print(
            "Откройте сохранённые кадры и найдите тот, где виден считыватель:"
            " его индекс передавайте в --camera при --record."
        )


def describe_ports() -> None:
    ports = list_serial_ports()
    if not ports:
        print("COM-портов не видно — модуль не в системе или это HID-сканер.")
        return
    for idx, port in enumerate(ports):
        print(f"[{idx}] {port.device} — {port.description}")
    print()
    # Подробности с VID/PID — через собственный pyserial.
    from serial.tools import list_ports

    for port_info in list_ports.comports():
        vendor = KNOWN_VENDORS.get(port_info.vid, "неопознанный вендор") if port_info.vid else "нет USB-признака"
        vid = f"{port_info.vid:#06x}" if port_info.vid else "-------"
        pid = f"{port_info.pid:#06x}" if port_info.pid else "-------"
        print(
            f"{port_info.device}: {port_info.description}\n"
            f"    VID={vid} PID={pid} — {vendor}\n"
            f"    hwid: {port_info.hwid}"
        )


def probe_port(
    port_name: str,
    baudrate: int,
    send_triggers: bool,
    record: bool = False,
    camera_index: int = 0,
    camera_url: str | None = None,
    out_dir: Path | None = None,
    listen_seconds: float = 0.0,
) -> None:
    out_dir = out_dir or (ROOT / "data" / "probe_laser")
    print(f"\n== Открываю {port_name} @ {baudrate} ==")
    try:
        connection = serial.Serial(port_name, baudrate, timeout=0.25)
    except Exception as exc:  # noqa: BLE001
        print(f"Не удалось открыть порт: {exc}")
        print("Порт занят другой программой или не существует — закройте приложения и повторите.")
        return

    if listen_seconds > 0:
        # Простой слушатель: жмёте физический триггер/сканируете — если модуль
        # отдаёт данные в этот COM, байты появятся здесь с метками времени.
        print(f"== Слушаю {port_name} @ {baudrate} {listen_seconds:.0f} с — жмите триггер и сканируйте! ==\n")
        deadline = time.monotonic() + listen_seconds
        total = 0
        while time.monotonic() < deadline:
            chunk = connection.read(1024)
            if chunk:
                total += len(chunk)
                left = max(0.0, deadline - time.monotonic())
                print(f"[ещё {left:4.1f} с] {len(chunk)} байт: {hexdump(chunk)}")
        if total:
            print(f"\nИТОГО: {total} байт за окно — ЭТОТ ПОРТ ЖИВОЙ, канал найден!")
        else:
            print("\nЗа окно байт не пришло (тишина): порт не тот, или модуль в режиме клавиатуры (HID).")
        connection.close()
        return

    capture = None
    if record:
        capture = open_camera(url=camera_url) if camera_url else open_camera(index=camera_index)
        if capture is None:
            print(
                "Запись кадров недоступна — продолжаю без камеры"
                " (подсказка: python tools/probe_scanner.py --list-cameras)"
            )

    # Стартовое окно тишины 3 с: слушаем модуль; если запись включена —
    # одновременно пишем baseline (лазер ещё точно не зажжён).
    connection.reset_input_buffer()
    baseline_max = 0
    if capture is not None:
        saved, red_frames, max_red = record_window(capture, out_dir, "00_baseline", 3.0)
        baseline_max = max_red
        print(f"Baseline-кадры (команд ещё не было): {saved} шт, красных {red_frames}, max_red {max_red}")
        append_report(out_dir, "00_baseline", saved, red_frames, max_red, "референс")
    else:
        time.sleep(3.0)
    banner = connection.read(512)
    print("Модуль сам пишет:", hexdump(banner))

    if not send_triggers:
        print("(--send не указан — команды не отправляю)")
        connection.close()
        if capture is not None:
            capture.release()
        return

    print("\n== Пытаемся зажечь лазер. Смотрите на луч! ==\n")
    hits: list[str] = []
    for index, (name, payload) in enumerate(TRIGGER_CANDIDATES, start=1):
        print(f"--- отправляю: {name}  ({hexdump(payload)})")
        try:
            connection.write(payload)
        except Exception as exc:  # noqa: BLE001
            print(f"    не отправилось: {exc}")
            break
        prefix = f"{index:02d}_{_slugify(name)}"
        if capture is not None:
            saved, red_frames, max_red = record_window(capture, out_dir, prefix, 4.0)
            verdict = verdict_for(red_frames, max_red, baseline_max)
            print(f"    кадры: {saved} шт, красных {red_frames}, max_red {max_red} — {verdict}")
            append_report(out_dir, prefix, saved, red_frames, max_red, verdict)
            if verdict.startswith("🔴"):
                hits.append(name)
        else:
            time.sleep(4.0)  # окно, в котором должен зажечься луч
        answer = connection.read(512)
        print(f"    ответ модуля: {hexdump(answer)}\n")
        print("    (если луч зажёгся — прервите Ctrl+C, команда найдена)")

    connection.close()
    if capture is not None:
        capture.release()
    print("Готово.")
    if hits:
        print("== ИТОГ: лазер замечен автоматически на командах: " + ", ".join(hits) + " ==")
    else:
        print("== ИТОГ: автодетект луча ничего не поймал — проверьте кадры глазами ==")
    print(f"Кадры и отчёт: {out_dir} (report.txt)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Диагностика COM-сканера")
    parser.add_argument("--port", default=None, help="COM-порт, например COM3")
    parser.add_argument("--baud", type=int, default=9600, help="скорость (по умолчанию 9600)")
    parser.add_argument("--send", action="store_true", help="отправлять команды триггера")
    parser.add_argument(
        "--record", action="store_true",
        help="сохранять кадры камеры во время каждой команды (data\\probe_laser)",
    )
    parser.add_argument(
        "--camera", type=int, default=0,
        help="индекс USB-камеры для --record (0 — обычно встроенная, 1 — внешняя)",
    )
    parser.add_argument(
        "--camera-url", default=None,
        help="URL IP-камеры для --record (например: rtsp://логин:пароль@192.168.0.10:554/stream)",
    )
    parser.add_argument(
        "--list-cameras", action="store_true",
        help="контрольный снимок USB-камер 0..3 (или IP-камеры из --camera-url)",
    )
    parser.add_argument(
        "--listen", type=int, default=0, metavar="СЕК",
        help="только слушать порт N секунд (для теста физического триггера)",
    )
    args = parser.parse_args()

    out_dir = ROOT / "data" / "probe_laser"

    if args.list_cameras:
        print("== Контрольные снимки камер -> data\\probe_laser\\camera_test ==\n")
        list_cameras(out_dir, url=args.camera_url)
        return 0

    print("== 1. Порты и модули в системе ==")
    describe_ports()

    if not args.port:
        print(
            "\nУкажите порт шагом 2:  python tools/probe_scanner.py --port COM3\n"
            "(без --send команды не отправляются)"
        )
        return 0

    if args.listen:
        probe_port(args.port, args.baud, False, False, 0, None, out_dir, listen_seconds=args.listen)
        return 0

    record = args.record or bool(args.camera_url)
    probe_port(args.port, args.baud, args.send, record, args.camera, args.camera_url, out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# inventory/services/photo_intel.py
"""Разбор фото этикетки с телефона: штрихкоды (zbar) + встроенный OCR Windows.

Конвейер офлайн, без внешних сервисов:
1) изображение читается поддержкой кириллических путей (imdecode);
2) яркая (белая) этикетка автоматически локализуется по порогу яркости;
3) этикетка приводится к нескольким масштабам и развороту (Windows OCR
   ограничен 5000 px по стороне и предпочитает высоту букв ~30–60 px);
4) каждый вариант распознаётся двумя языками (en-US и ru): на этикетках
   смесь — модель латиницей, пометки бывают кириллицей;
5) штрихкоды декодирует pyzbar (CODE128/CODE39/EAN/QR…) по всей этикетке
   и по отдельным зонам, где OCR видит полосу «страшных» символов —
   типичная картина 1D-штрихкода;
6) из объединённых строк извлекаются КАНДИДАТЫ полей (MAC, S/N, модель,
   P/N, IP). Распознавание с фото всегда с огрехами — значения
   подтверждает пользователь в диалоге, список кандидатов виден целиком.
"""
from __future__ import annotations

import asyncio
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

WINOCR_MAX = 4900          # предел Windows OCR по стороне, px
_OCR_SCALES = (1400, 2400, 4200)
_OCR_LANGS = ("en-US", "ru")

# Строка-опечатка OCR на 1D-штрихкоде: столбцы из «I l 1 | • · !».
BARCODE_LINE_RE = re.compile(r"^[\WIl1|!i•·]{6,}$")

# Отображение «грязных» символов распознавания в гексадесимичные (цифры не перечислены:
# они и так валидны). То же всё, что OCR путает в Ф/Г и т.п.
_HEX_MAP: dict[str, str] = {
    "O": "0", "o": "0", "Q": "0", "О": "0", "о": "0", "@": "0",
    "I": "1", "i": "1", "l": "1", "L": "1", "|": "1", "!": "1", "t": "1",
    "Z": "2", "z": "2",
    "З": "3", "з": "3",
    "Ч": "4",
    "б": "B",
    "S": "5", "s": "5",
    "g": "9", "q": "9",
    "A": "A", "a": "A", "А": "A", "а": "A",
    "B": "B", "В": "B", "в": "B",
    "C": "C", "c": "C", "С": "C", "с": "C", "Р": "C", "р": "C",
    "D": "D", "d": "D",
    "E": "E", "e": "E", "Е": "E", "е": "E",
    "F": "F", "f": "F", "Ф": "F", "ф": "F", "Г": "F", "г": "F",
}


@dataclass
class PhotoIntel:
    """Результат разбора фото этикетки: кандидаты полей + служебные данные."""

    serials: list[str] = field(default_factory=list)
    macs: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    part_numbers: list[str] = field(default_factory=list)
    ip_addresses: list[str] = field(default_factory=list)
    barcodes: list[tuple[str, str]] = field(default_factory=list)  # (тип, значение)
    raw_lines: list[str] = field(default_factory=list)
    label_size: tuple[int, int] | None = None  # (w, h) вырезки этикетки
    ok: bool = False
    error: str | None = None

    @property
    def has_candidates(self) -> bool:
        return bool(self.serials or self.macs or self.models or self.part_numbers)

    def summary(self) -> str:
        parts = []
        if self.serials:
            parts.append("S/N: " + ", ".join(self.serials[:3]))
        if self.macs:
            parts.append("MAC: " + ", ".join(self.macs[:3]))
        if self.models:
            parts.append("Модель: " + ", ".join(self.models[:2]))
        if self.part_numbers:
            parts.append("P/N: " + ", ".join(self.part_numbers[:2]))
        return " · ".join(parts) if parts else "Ничего не распознано"


# ----------------------------------------------------------------------------
# Загрузка и локализация этикетки
# ----------------------------------------------------------------------------
def _load_bgr(path: str | Path):
    import cv2
    import numpy as np

    data = np.fromfile(str(path), dtype=np.uint8)  # Windows: пути с кириллицей
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _find_label(img):
    """Крупнейшая яркая связная область (этикетка) или весь кадр."""
    import cv2
    import numpy as np

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, bright = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)
    if bright.mean() < 5:
        return None
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < 0.02 * img.shape[0] * img.shape[1]:
        return None
    x, y, w, h = cv2.boundingRect(contour)
    pad = max(8, min(w, h) // 30)
    y0, y1 = max(0, y - pad), min(img.shape[0], y + h + pad)
    x0, x1 = max(0, x - pad), min(img.shape[1], x + w + pad)
    return img[y0:y1, x0:x1]


def _variant(gray_label, rot, cap: int):
    import cv2

    base = cv2.rotate(gray_label, rot)
    hh, ww = base.shape[:2]
    scale = min(cap / hh, cap / ww, 8.0)
    if scale <= 0:
        return None
    big = cv2.resize(base, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return cv2.createCLAHE(2.0, (8, 8)).apply(big)


def _save(arr, work: Path, name: str) -> Path:
    import cv2

    path = work / f"intel_{name}.png"
    ok, buf = cv2.imencode(".png", arr)
    if not ok:
        raise RuntimeError("не удалось записать временный PNG")
    buf.tofile(str(path))
    return path


# ----------------------------------------------------------------------------
# OCR и декодирование штрихкодов
# ----------------------------------------------------------------------------
async def _ocr_lines(path: Path, tag: str):
    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage import FileAccessMode, StorageFile
    from winrt.windows.graphics.imaging import (
        BitmapAlphaMode,
        BitmapDecoder,
        BitmapPixelFormat,
    )

    if not OcrEngine.is_language_supported(Language(tag)):
        return [], []
    engine = OcrEngine.try_create_from_language(Language(tag))
    if engine is None:
        return [], []
    file = await StorageFile.get_file_from_path_async(str(path))
    stream = await file.open_async(FileAccessMode.READ)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async(
        BitmapPixelFormat.BGRA8, BitmapAlphaMode.PREMULTIPLIED
    )
    result = await engine.recognize_async(bitmap)
    lines = [line.text for line in result.lines]
    words = []
    for line in result.lines:
        for word in line.words:
            rect = word.bounding_rect
            words.append(
                (word.text, (int(rect.x), int(rect.y), int(rect.width), int(rect.height)))
            )
    return lines, words


def _decode_barcodes(arr) -> list[tuple[str, str]]:
    from pyzbar.pyzbar import decode

    hits = []
    for item in decode(arr):
        value = item.data.decode("utf-8", "replace").strip()
        if value:
            hits.append((item.type, value))
    return hits


# ----------------------------------------------------------------------------
# Извлечение кандидатов из строк
# ----------------------------------------------------------------------------
_STRICT_MAC = re.compile(
    r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])"
    r"|(?<![0-9A-Fa-f])[0-9A-Fa-f]{12}(?![0-9A-Fa-f])"
)
_MAC_KEY = re.compile(r"MAC|МАС|МАC", re.IGNORECASE)
_SERIAL_KEY = re.compile(
    r"(?:\bSN\b|S[/\\]N|S\.N\.|Serial)\s*[:=]?\s*([A-Za-z0-9\-]{5,24})", re.IGNORECASE
)
_PART_KEY = re.compile(
    r"(?:P[/\\]N|P\.N\.)\s*[:=]?\s*([A-Za-z0-9\-/]{5,24})", re.IGNORECASE
)
_MODEL_HINT = re.compile(r"[A-Z0-9][\w]{1,}[-/][\w/()\-]{3,}")
_IP = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
_JUNK = re.compile(r"[^0-9A-Za-z\-/]")
_TOKEN = re.compile(r"[^\W_]{10,24}")


def _hex_normalize(token: str) -> str | None:
    """Грязный токен → канонический MAC (12 hex) или None."""
    mapped: list[str] = []
    for char in token:
        if char in ":-. ":
            continue
        replacement = _HEX_MAP.get(char)
        mapped.append(replacement if replacement is not None else char)
    text = "".join(mapped).upper()
    if len(text) != 12:  # 13–17 символов — это S/N, а не MAC: не отрезаем молча
        return None
    if any(c not in "0123456789ABCDEF" for c in text):
        return None
    return text


def _hex_quality(token: str) -> int:
    """Сколько символов токена отображаются в hex; -1 при «неhex»-мусоре."""
    score = 0
    for char in token:
        if char.isdigit():
            score += 1
        elif char in _HEX_MAP or char.upper() in "ABCDEF":
            score += 1
        else:
            return -1
    return score


def _mac_candidates(line: str) -> list[str]:
    found: list[str] = []
    for hit in _STRICT_MAC.finditer(line):
        value = _hex_normalize(hit.group(0))
        if value and value not in found:
            found.append(value)
    # Контекст «MAC … хвост»: ближайший алфанумерный комок.
    for match in _MAC_KEY.finditer(line):
        tail = line[match.end():match.end() + 26].strip()
        token = _JUNK.sub("", tail.split(" ")[0]) if tail else ""
        if len(token) >= 10 and _hex_quality(token) == len(token):
            value = _hex_normalize(token)
            if value and value not in found:
                found.append(value)
    # Одиночный «грязный» токен, целиком отображаемый в hex.
    for token in _TOKEN.findall(line):
        if len(token) in range(11, 18) and _hex_quality(token) == len(token):
            value = _hex_normalize(token)
            if value and value not in found:
                found.append(value)
    return found


def extract_candidates(lines: list[str], barcodes: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Грубые кандидаты полей из строк OCR и декодированных штрихкодов."""
    serials: list[str] = []
    macs: list[str] = []
    models: list[str] = []
    part_numbers: list[str] = []
    ips: list[str] = []

    for kind, value in barcodes:
        value = (value or "").strip()
        if not value:
            continue
        if kind.upper() in {"QRCODE", "PDF417", "AZTEC"}:
            if value not in serials:
                serials.append(value)
            continue
        token = _JUNK.sub("", value)
        if 6 <= len(token) <= 24:
            if _hex_quality(token) == len(token) and 12 <= len(token) <= 17:
                mapped = _hex_normalize(token)
                if mapped and mapped not in macs:
                    macs.append(mapped)
            if token not in serials and len(token) >= 8:
                serials.append(token)

    for line in lines:
        stripped = line.strip()
        if not stripped or BARCODE_LINE_RE.match(stripped):
            continue
        for value in _mac_candidates(stripped):
            if value not in macs:
                macs.append(value)
        for match in _IP.finditer(stripped):
            ip = match.group(0)
            if all(int(octet) <= 255 for octet in ip.split(".")) and ip not in ips:
                ips.append(ip)
        serial_match = _SERIAL_KEY.search(stripped)
        if serial_match:
            token = _JUNK.sub("", serial_match.group(1))
            if 5 <= len(token) <= 24 and token not in serials and token not in part_numbers:
                serials.append(token)
        part_match = _PART_KEY.search(stripped)
        if part_match:
            token = _JUNK.sub("", part_match.group(1))
            if 5 <= len(token) <= 24 and token not in part_numbers:
                part_numbers.append(token)
        if _MODEL_HINT.search(stripped) and "china" not in stripped.lower():
            candidate = re.sub(r"\s+", "_", stripped)
            if 6 <= len(candidate) <= 42 and re.search(r"\d", candidate) and candidate not in models:
                models.append(candidate)

    return {
        "serials": serials,
        "macs": macs,
        "models": models,
        "part_numbers": part_numbers,
        "ips": ips,
    }


# ----------------------------------------------------------------------------
# Полный конвейер
# ----------------------------------------------------------------------------
def analyse_photo(path: str | Path) -> PhotoIntel:
    """Полный разбор фото. Блокирующий; из Qt вызывать в рабочем потоке."""
    intel = PhotoIntel()
    try:
        import cv2

        img = _load_bgr(path)
        if img is None:
            intel.error = "Файл не читается как изображение"
            return intel
        label = _find_label(img)
        if label is None:
            label = img
        intel.label_size = (label.shape[1], label.shape[0])

        work = Path(tempfile.gettempdir()) / "GetacInventory" / "label_intel"
        work.mkdir(parents=True, exist_ok=True)

        gray_label = cv2.cvtColor(label, cv2.COLOR_BGR2GRAY)
        lines_union: list[str] = []
        barcodes_all: list[tuple[str, str]] = []
        seen: set[str] = set()

        for cap in _OCR_SCALES:
            big = _variant(gray_label, cv2.ROTATE_90_COUNTERCLOCKWISE, cap)
            if big is None:
                continue
            png = _save(big, work, f"cap{cap}")
            for tag in _OCR_LANGS:
                lines, words = asyncio.run(_ocr_lines(png, tag))
                for text in lines:
                    if text not in seen:
                        seen.add(text)
                        lines_union.append(text)
            for hit in _decode_barcodes(big):
                if hit not in barcodes_all:
                    barcodes_all.append(hit)
            if cap == _OCR_SCALES[-1]:
                for zone in _barcode_zones(big, words):
                    for hit in _decode_barcodes(zone):
                        if hit not in barcodes_all:
                            barcodes_all.append(hit)

        intel.raw_lines = lines_union
        intel.barcodes = barcodes_all
        fields = extract_candidates(lines_union, barcodes_all)
        intel.serials = fields["serials"]
        intel.macs = fields["macs"]
        intel.models = fields["models"]
        intel.part_numbers = fields["part_numbers"]
        intel.ip_addresses = fields["ips"]
        intel.ok = True
        return intel
    except Exception as exc:  # noqa: BLE001 — конвейер мягко деградирует
        intel.error = str(exc)
        return intel


def _barcode_zones(arr, words) -> list:
    zones = []
    height, width = arr.shape[:2]
    for text, (x, y, w, h) in words:
        if not BARCODE_LINE_RE.match(text) or w < width * 0.2:
            continue
        y0, y1 = max(0, y - h), min(height, y + h + h)
        x0, x1 = max(0, x - 8), min(width, x + w + 8)
        zone = arr[y0:y1, x0:x1]
        if zone.size:
            zones.append(zone)
    return zones


def ocr_present() -> bool:
    """Есть ли в системе языковые пакеты Windows OCR (на старых Windows их нет)."""
    try:
        from winrt.windows.media.ocr import OcrEngine

        return any(True for _ in OcrEngine.available_recognizer_languages)
    except Exception:  # noqa: BLE001
        return False


def photo_intel_available() -> tuple[bool, str]:
    """Полная проверка конвейера: pyzbar + WinRT-OCR.

    Возвращает (доступен, причина). Причину показываем пользователю,
    если кнопка разбора этикетки не работает.
    """
    try:
        from pyzbar.pyzbar import decode  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"не найден pyzbar ({exc})"
    if not ocr_present():
        return False, "в системе нет языковых пакетов Windows OCR"
    return True, ""

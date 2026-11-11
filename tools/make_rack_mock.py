# tools/make_rack_mock.py
"""Синтетическое фото серверной стойки для проверки распознавания.

Рисует фотореалистичный макет 10U-шкафа: 8 юнитов с брендами, моделями,
портами/индикаторами и бирками позиций U на левой шине. Результат —
tools/assets/rack_mock.jpg — затем прогоняется через живую модель
(inventory.services.rack_ai.analyse_rack_photo) как «полевой тест» стойки.

Запуск: python -X utf8 tools/make_rack_mock.py
"""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tools" / "assets" / "rack_mock.jpg"
FONTS = Path(r"C:\Windows\Fonts")

W, H = 960, 1280
FRAME = 20              # рама шкафа
RAIL_X0, RAIL_X1 = FRAME + 6, FRAME + 6 + 58
UNIT_X0 = RAIL_X1 + 8
UNIT_X1 = W - FRAME - 6
TOP_Y, BOTTOM_Y = FRAME + 24, H - FRAME - 24
U_H = (BOTTOM_Y - TOP_Y) / 10.0

GREEN = (60, 210, 100)
AMBER = (255, 176, 32)

# (юнит lo, hi), бренд, модель, цвет панели, цвет текста, цвет доп. текста, вид
UNITS = [
    ((9, 10), "Cisco", "WS-C2960+24TC-L", (38, 41, 46), (232, 234, 238),
     (150, 156, 164), "switch24"),
    ((8, 8), "", "", (16, 18, 20), (210, 214, 220), (140, 145, 152), "patch"),
    ((7, 7), "TP-LINK", "TL-SG1016", (52, 56, 62), (240, 242, 246),
     (160, 166, 174), "switch16"),
    ((6, 6), "HIKVISION", "DS-7608NI-K2", (12, 13, 15), (235, 238, 242),
     (150, 156, 164), "nvr"),
    ((5, 5), "APC", "AP7921", (35, 38, 42), (238, 240, 244),
     (160, 166, 174), "pdu"),
    ((4, 4), "MikroTik", "RB2011UiAS-IN", (29, 43, 51), (238, 240, 244),
     (150, 156, 164), "router"),
    ((2, 3), "HP", "ProLiant DL360 G9", (158, 163, 168), (10, 16, 28),
     (60, 66, 74), "server"),
    ((1, 1), "APC", "Smart-UPS SMT750R", (18, 20, 24), (240, 242, 246),
     (165, 170, 178), "ups"),
]


def fonts() -> dict[str, ImageFont.FreeTypeFont]:
    return {
        "brand": ImageFont.truetype(str(FONTS / "arialbd.ttf"), 27),
        "model": ImageFont.truetype(str(FONTS / "arial.ttf"), 19),
        "tag": ImageFont.truetype(str(FONTS / "consolab.ttf"), 21),
        "small": ImageFont.truetype(str(FONTS / "arialbd.ttf"), 15),
    }


def y_range(lo: int, hi: int) -> tuple[float, float]:
    """Y-границы юнита (верхняя и нижняя) на холсте. U10 сверху."""
    y_top = BOTTOM_Y - hi * U_H
    y_bot = BOTTOM_Y - (lo - 1) * U_H
    return y_top, y_bot


def faceplate(d: ImageDraw.ImageDraw, y0: float, y1: float,
              face: tuple, text: tuple, sub: tuple) -> None:
    """Панель юнита: заливка, светлая верхняя и тёмная нижняя кромки."""
    x0, x1 = UNIT_X0, UNIT_X1
    d.rectangle([x0, y0, x1, y1], fill=face)
    d.line([x0, y0, x1, y0], fill=tuple(min(c + 40, 255) for c in face))
    d.line([x0, y1, x1, y1], fill=tuple(max(c - 45, 0) for c in face))
    d.rectangle([x0, y0, x1, y1], outline=(8, 9, 11))
    # ушки крепёжных винтов
    for cx in (x0 + 14, x1 - 14):
        d.ellipse([cx - 5, y0 + 8, cx + 5, y0 + 18], fill=(120, 124, 130))


def draw_ports(d: ImageDraw.ImageDraw, x_right: float, cy: float, n: int,
               pw: int = 22, ph: int = 13, gap: int = 9) -> None:
    """Ряд портов с зелёными огоньками, выровненный по правому краю."""
    total = n * pw + (n - 1) * gap
    x = x_right - total
    for i in range(n):
        p0, p1 = x + i * (pw + gap), x + i * (pw + gap) + pw
        d.rectangle([p0, cy - ph / 2, p1, cy + ph / 2],
                    fill=(14, 15, 18), outline=(70, 74, 80))
        led_cy = cy - ph / 2 - 3
        color = GREEN if (i % 5 != 3) else AMBER
        d.ellipse([p0 + 3, led_cy - 2, p0 + 7, led_cy + 2], fill=color)


def draw_outlets(d: ImageDraw.ImageDraw, x_right: float, cy: float,
                 n: int) -> None:
    """Розетки PDU: светлые квадраты с парой тёмных точек."""
    ow, oh, gap = 30, 22, 14
    total = n * ow + (n - 1) * gap
    x = x_right - total
    for i in range(n):
        o0, o1 = x + i * (ow + gap), x + i * (ow + gap) + ow
        d.rectangle([o0, cy - oh / 2, o1, cy + oh / 2],
                    fill=(196, 200, 206), outline=(70, 74, 80))
        d.ellipse([o0 + 7, cy - 3, o0 + 13, cy + 3], fill=(30, 32, 36))
        d.ellipse([o1 - 13, cy - 3, o1 - 7, cy + 3], fill=(30, 32, 36))


def draw_bays(d: ImageDraw.ImageDraw, x_right: float, cy: float,
              n: int) -> None:
    """Отсеки дисков сервера: тёмные квадраты с огоньком активности."""
    bw, gap = 38, 10
    total = n * bw + (n - 1) * gap
    x = x_right - total
    for i in range(n):
        b0, b1 = x + i * (bw + gap), x + i * (bw + gap) + bw
        top, bot = cy - 26, cy + 26
        d.rectangle([b0, top, b1, bot], fill=(24, 26, 30),
                    outline=(90, 94, 100))
        d.rectangle([b0 + 5, top + 6, b1 - 5, bot - 6],
                    fill=(38, 41, 46), outline=(60, 64, 70))
        d.ellipse([b0 + 7, bot - 12, b0 + 12, bot - 7], fill=GREEN)


def draw_lcd(d: ImageDraw.ImageDraw, x: float, y: float, w: int, h: int,
             lines: list[str], f_small: ImageFont.FreeTypeFont) -> None:
    """Небольшой ЖК-дисплей: тёмное поле, зелёный текст."""
    d.rectangle([x, y, x + w, y + h], fill=(6, 10, 8), outline=(70, 74, 80))
    ty = y + 4
    for line in lines:
        d.text((x + 6, ty), line, font=f_small, fill=(90, 230, 130))
        ty += 17


def draw_unit(d: ImageDraw.ImageDraw, f: dict, unit: tuple) -> None:
    (lo, hi), brand, model, face, text, sub, kind = unit
    y0, y1 = y_range(lo, hi)
    pad = 5
    y0 += pad / 2
    y1 -= pad / 2
    faceplate(d, y0, y1, face, text, sub)
    tx = UNIT_X0 + 34
    ty = y0 + 10
    label = brand or "PATCH PANEL 24"
    d.text((tx, ty), label, font=f["brand"], fill=text)
    if model:
        d.text((tx, ty + 30), model, font=f["model"], fill=sub)

    x_right = UNIT_X1 - 22
    cy_mid = (y0 + y1) / 2
    if kind == "switch24":       # 2U: два ряда по 12 портов
        draw_ports(d, x_right, y0 + 34, 12)
        draw_ports(d, x_right, y1 - 34, 12)
    elif kind == "switch16":     # 1U: один ряд 16
        draw_ports(d, x_right, cy_mid + 18, 16)
    elif kind == "patch":        # 24 гнезда одним рядом
        draw_ports(d, x_right, cy_mid, 24, pw=22, ph=17, gap=8)
    elif kind == "nvr":
        draw_lcd(d, x_right - 170, cy_mid - 2, 170, 42,
                 ["HIK-CLI  ON", "CH01 CH02 CH03"], f["small"])
        draw_ports(d, x_right - 190, cy_mid + 18, 4, pw=18, ph=12, gap=8)
    elif kind == "pdu":
        draw_outlets(d, x_right, cy_mid + 16, 8)
    elif kind == "router":
        draw_ports(d, x_right, cy_mid + 18, 5, pw=20, ph=13, gap=8)
        draw_lcd(d, x_right - 190, cy_mid - 6, 90, 30, ["RB2011"], f["small"])
    elif kind == "server":       # 2U: 8 дисковых отсеков
        draw_bays(d, x_right, cy_mid, 8)
    elif kind == "ups":
        draw_lcd(d, x_right - 150, cy_mid - 14, 150, 46,
                 ["LOAD 25%", "OUT 230V"], f["small"])
        d.ellipse([x_right - 170, cy_mid - 6, x_right - 160, cy_mid + 4],
                  fill=GREEN)


def draw_rail(d: ImageDraw.ImageDraw, f: dict) -> None:
    """Левая шина: отверстия + белые бирки с номером юнита."""
    d.rectangle([RAIL_X0, TOP_Y, RAIL_X1, BOTTOM_Y], fill=(66, 70, 76))
    for u in range(1, 11):
        _, y_bot = y_range(u, u)
        cy = (y_bot + y_bot - U_H) / 2  # центр U-ряда
        d.ellipse([RAIL_X0 + 10, cy - 4, RAIL_X0 + 18, cy + 4],
                  fill=(20, 22, 25))
        tag_w, tag_h = 44, 30
        tx0 = RAIL_X0 + 12
        ty0 = cy - tag_h / 2
        d.rectangle([tx0, ty0, tx0 + tag_w, ty0 + tag_h],
                    fill=(238, 238, 234), outline=(120, 124, 130))
        d.text((tx0 + 7, ty0 + 4), f"U{u}", font=f["tag"], fill=(16, 18, 22))


def add_photo_realism(img: Image.Image) -> Image.Image:
    """Лёгкий шум, виньетка и смётанный blur — чтобы не выглядело «рендером»."""
    import numpy as np

    img = img.filter(ImageFilter.GaussianBlur(0.6))
    arr = np.asarray(img).astype("float32")
    rng = np.random.default_rng(42)
    arr += rng.normal(0.0, 5.5, arr.shape)
    # виньетка: угол темнее
    yy, xx = np.mgrid[0:H, 0:W]
    dist = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    vignette = 1.0 - 0.22 * np.clip(dist - 0.55, 0, 1) ** 2
    arr *= vignette[:, :, None]
    return Image.fromarray(np.clip(arr, 0, 255).astype("uint8"), "RGB")


def main() -> None:
    random.seed(7)
    f = fonts()
    img = Image.new("RGB", (W, H), (24, 26, 30))
    d = ImageDraw.Draw(img)
    # фон шкафа: тёмное нутро
    d.rectangle([FRAME, FRAME, W - FRAME, H - FRAME], fill=(30, 32, 37))
    d.rectangle([0, 0, W, H], outline=(56, 60, 66), width=FRAME * 2)
    draw_rail(d, f)
    for unit in UNITS:
        draw_unit(d, f, unit)
    img = add_photo_realism(img)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "JPEG", quality=88)
    print(f"Готово: {OUT} ({OUT.stat().st_size / 1024:.0f} КБ, {W}x{H})")


if __name__ == "__main__":
    main()

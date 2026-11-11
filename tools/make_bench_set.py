# tools/make_bench_set.py
"""Бенчмарк-набор синтетических стоек для A/B-тестов промпта.

Из базового макета (make_rack_mock) делает 4 усложнённых варианта:
- rack_dark.jpg    — тёмный кадр (плохой свет) + шум
- rack_glare.jpg   — блики/отражения на двери шкафа
- rack_partial.jpg — обрезка: видна только нижняя половина (тест
  анти-галлюцинации «не дорисовывай невидимое»)
- rack_dense15.jpg — плотная 15U стойка, 13 юнитов (в т.ч. дубль
  бренда/модели на разных позициях — тест дедупликации по позиции)

Каждому варианту соответствует эталон — tools/assets/bench/expected.json;
сверку результатов делает tools/debrief_report.py --expected.

Запуск: python -X utf8 tools/make_bench_set.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_rack_mock as mrm  # noqa: E402  (импорт соседнего генератора)

ASSETS = mrm.ROOT / "tools" / "assets" / "bench"

# Эталон плотной стойки 15U: 13 юнитов, U10 сверху → U1 снизу (в списке сверху вниз).
DENSE_UNITS = [
    ((14, 15), "D-Link", "DGS-1210-28", (38, 41, 46), (232, 234, 238),
     (150, 156, 164), "switch24"),
    ((13, 13), "", "", (16, 18, 20), (210, 214, 220), (140, 145, 152), "patch"),
    ((12, 12), "Zyxel", "GS1920-24", (52, 56, 62), (240, 242, 246),
     (160, 166, 174), "switch16"),
    ((11, 11), "Ubiquiti", "UniFi Switch 24", (24, 28, 32), (240, 242, 246),
     (160, 166, 174), "switch16"),
    ((10, 10), "Netgear", "GS108Ev3", (44, 48, 54), (238, 240, 244),
     (160, 166, 174), "switch16"),
    ((9, 9), "HIKVISION", "DS-7608NI-K2", (12, 13, 15), (235, 238, 242),
     (150, 156, 164), "nvr"),
    ((8, 8), "APC", "AP7921", (35, 38, 42), (238, 240, 244),
     (160, 166, 174), "pdu"),
    ((7, 7), "D-Link", "DES-3200-18", (30, 34, 40), (235, 238, 242),
     (150, 156, 164), "switch16"),
    ((6, 6), "MikroTik", "CRS125-24G", (29, 43, 51), (238, 240, 244),
     (150, 156, 164), "router"),
    ((4, 5), "HP", "ProLiant DL380 G9", (158, 163, 168), (10, 16, 28),
     (60, 66, 74), "server"),
    ((3, 3), "APC", "AP7921", (35, 38, 42), (238, 240, 244),
     (160, 166, 174), "pdu"),  # дубль бренда/модели на другой позиции
    ((2, 2), "Eaton", "5P 750 Rack", (26, 29, 33), (238, 240, 244),
     (160, 166, 174), "ups"),
    ((1, 1), "APC", "Smart-UPS SMT750R", (18, 20, 24), (240, 242, 246),
     (165, 170, 178), "ups"),
]


def _truth(*items: tuple[str, str, str, str]) -> list[dict]:
    return [
        {"position": p, "brand": b, "model": m, "type": t}
        for p, b, m, t in items
    ]


BASE_TRUTH = _truth(
    ("U9-U10", "Cisco", "WS-C2960+24TC-L", "switch"),
    ("U8", "", "", "patch"),
    ("U7", "TP-LINK", "TL-SG1016", "switch"),
    ("U6", "HIKVISION", "DS-7608NI-K2", "nvr"),
    ("U5", "APC", "AP7921", "psu"),
    ("U4", "MikroTik", "RB2011UiAS-IN", "router"),
    ("U2-U3", "HP", "ProLiant DL360 G9", "server"),
    ("U1", "APC", "Smart-UPS SMT750R", "ups"),
)

DENSE_TRUTH = _truth(
    ("U14-U15", "D-Link", "DGS-1210-28", "switch"),
    ("U13", "", "", "patch"),
    ("U12", "Zyxel", "GS1920-24", "switch"),
    ("U11", "Ubiquiti", "UniFi Switch 24", "switch"),
    ("U10", "Netgear", "GS108Ev3", "switch"),
    ("U9", "HIKVISION", "DS-7608NI-K2", "nvr"),
    ("U8", "APC", "AP7921", "psu"),
    ("U7", "D-Link", "DES-3200-18", "switch"),
    ("U6", "MikroTik", "CRS125-24G", "router"),
    ("U4-U5", "HP", "ProLiant DL380 G9", "server"),
    ("U3", "APC", "AP7921", "psu"),
    ("U2", "Eaton", "5P 750 Rack", "ups"),
    ("U1", "APC", "Smart-UPS SMT750R", "ups"),
)

PARTIAL_TRUTH = [u for u in BASE_TRUTH if u["position"] in
                 ("U1", "U2-U3", "U4", "U5")]


def darken(img: Image.Image) -> Image.Image:
    """Тёмный кадр: приглушить свет, добавить зерно, чуть смазать."""
    img = ImageEnhance.Brightness(img).enhance(0.5)
    img = ImageEnhance.Color(img).enhance(0.85)
    img = img.filter(ImageFilter.GaussianBlur(0.9))
    arr = np.asarray(img).astype("float32")
    rng = np.random.default_rng(13)
    arr += rng.normal(0.0, 12.0, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype("uint8"), "RGB")


def add_glare(img: Image.Image) -> Image.Image:
    """Блики: две светлые диагональные полосы, как отражение двери шкафа."""
    overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
    d = ImageDraw.Draw(overlay)
    w, h = img.size
    d.polygon([(w * 0.55, 0), (w * 0.78, 0), (w * 0.30, h), (w * 0.12, h)],
              fill=(255, 255, 255, 60))
    d.polygon([(w * 0.86, 0), (w * 0.95, 0), (w * 0.55, h), (w * 0.42, h)],
              fill=(255, 255, 255, 35))
    overlay = overlay.filter(ImageFilter.GaussianBlur(18))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def crop_bottom(img: Image.Image, top: int) -> Image.Image:
    """Обрезка: оставить нижнюю часть стойки от границы `top`."""
    return img.crop((0, top, img.width, img.height))


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    base = mrm.build(mrm.UNITS)          # 10U, 8 юнитов
    # Граница обрезки — ровно между U5 и U6; вычислять ДО плотной сборки
    # (build перезаписывает глобальную геометрию U_H под 15U).
    partial_top = int(mrm.BOTTOM_Y - 5 * mrm.U_H)
    mrm.save(base, mrm.OUT)              # базовый макет — тем же рендером

    dense = mrm.build(DENSE_UNITS, rack_u=15)
    mrm.save(dense, ASSETS / "rack_dense15.jpg")
    mrm.save(darken(base), ASSETS / "rack_dark.jpg")
    mrm.save(add_glare(base), ASSETS / "rack_glare.jpg")
    mrm.save(crop_bottom(base, partial_top), ASSETS / "rack_partial.jpg")

    expected = {
        "rack_mock.jpg": BASE_TRUTH,
        "rack_dark.jpg": BASE_TRUTH,
        "rack_glare.jpg": BASE_TRUTH,
        "rack_partial.jpg": PARTIAL_TRUTH,
        "rack_dense15.jpg": DENSE_TRUTH,
    }
    path = ASSETS / "expected.json"
    path.write_text(json.dumps(expected, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print(f"Эталон: {path} ({len(expected)} кадров)")


if __name__ == "__main__":
    main()

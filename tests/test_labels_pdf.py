# tests/test_labels_pdf.py
"""Печатный лист TIA-606: чтение labels.csv и PDF-A4 с наклейками."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))  # noqa: E402

from labels_pdf import (  # noqa: E402
    main,
    read_labels_csv,
    render_labels_pdf,
)
from tia606 import write_labels_csv  # noqa: E402

_APP = None


def _app():
    """QApplication один на сессию тестов (QImage, QPdfWriter)."""
    global _APP
    if QApplication.instance() is None:
        _APP = QApplication([])
    return QApplication.instance()


def _row(index: int = 1, level: str = "rack") -> dict:
    row = {
        "label": f"OBEKT-101-SH{index}",
        "level": level,
        "site": "Объект Демо",
        "room": "Корпус А - 1 - 101",
        "rack": f"Ш-{index}",
        "panel": "",
        "port": "",
    }
    if level == "port":
        row.update(label=f"OBEKT-101-SH{index}-A1-P01",
                   panel="A1", port="P01")
    return row


def _pdf_sane(path: Path) -> bytes:
    assert path.exists()
    data = path.read_bytes()
    assert data.startswith(b"%PDF")
    assert len(data) > 2000  # рамки + текст, не пустой файл
    return data


def test_read_labels_csv_roundtrip(tmp_path: Path) -> None:
    rows = [_row(1), _row(2, level="port")]
    path = write_labels_csv(tmp_path, rows)
    loaded = read_labels_csv(path)
    assert loaded == [{key: str(row[key]) for key in
                       ("label", "level", "site", "room", "rack",
                        "panel", "port")} for row in rows]


def test_read_labels_csv_with_excel_bom(tmp_path: Path) -> None:
    """Excel при пересохранении добавляет BOM — колонка label не должна пропасть."""
    path = tmp_path / "bom.csv"
    path.write_text(
        "\ufefflabel,level,site,room,rack,panel,port\n"
        "OBEKT-101-SH1,rack,Объект,101,Ш-1,,\n", encoding="utf-8")
    loaded = read_labels_csv(path)
    assert loaded[0]["label"] == "OBEKT-101-SH1"
    assert "\ufefflabel" not in loaded[0]


def test_render_labels_pdf(tmp_path: Path) -> None:
    _app()
    out = tmp_path / "labels.pdf"
    assert render_labels_pdf([_row(1), _row(2, level="port")], out) == 2
    _pdf_sane(out)


def test_render_labels_pdf_pagination(tmp_path: Path) -> None:
    """25 наклеек = 2 страницы A4 (24 на лист)."""
    _app()
    out = tmp_path / "many.pdf"
    rows = [_row(i) for i in range(1, 26)]
    assert render_labels_pdf(rows, out) == 25
    data = _pdf_sane(out)
    assert data.count(b"/Type /Page") >= 2 or data.count(b"/Type/Page") >= 2


def test_render_labels_pdf_empty_skips_file(tmp_path: Path) -> None:
    _app()
    out = tmp_path / "empty.pdf"
    assert render_labels_pdf([], out) == 0
    assert not out.exists()


def test_main_cli(tmp_path: Path) -> None:
    _app()
    csv_path = write_labels_csv(tmp_path, [_row(1)])
    out = tmp_path / "cli.pdf"
    assert main(["--csv", str(csv_path), "--out", str(out)]) == 0
    _pdf_sane(out)


def test_main_cli_missing_csv(tmp_path: Path) -> None:
    assert main(["--csv", str(tmp_path / "нет.csv")]) == 1

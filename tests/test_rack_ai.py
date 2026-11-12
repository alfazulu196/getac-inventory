# tests/test_rack_ai.py
"""Тесты распознавания стойки: парсер JSON, алиасы типов, статус ollama."""
from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from inventory.services import rack_ai
from inventory.services.rack_ai import (
    MAX_DEVICES,
    RackAnalysis,
    RackDevice,
    _parse_devices,
    ollama_status,
    pick_model,
)


# ---------------------------------------------------------------- title
def test_device_title_brand_model():
    assert RackDevice(brand="D-Link", model="DES-1210").title == "D-Link DES-1210"


def test_prompt_matches_prompt_lab_combo():
    """Промпт приложения = проверенный «combo» из промпт-лабы (v1.6.3).

    Лаба собирает варианты из констант приложения (_PROMPT_V162 +
    PROMPT_ADDITIONS_V163), так что расхождение возможно только при
    правке композиции — этот тест её ловит.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import prompt_lab  # noqa: PLC0415 — импорт ленивый, Qt не требует

    assert rack_ai._PROMPT == prompt_lab.VARIANTS["combo"]
    assert "ДОПОЛНИТЕЛЬНО" in rack_ai._PROMPT  # добавки на месте


def test_device_title_only_brand():
    assert RackDevice(brand="APC").title == "APC"


def test_device_title_empty_falls_back_to_detail():
    assert RackDevice(detail="панель портов").title == "панель портов"
    assert RackDevice().title == "Юнит без описания"


# ---------------------------------------------------------------- parse
def test_parse_plain_json():
    raw = json.dumps(
        {"devices": [
            {"type": "switch", "brand": "D-Link", "model": "DES-1210",
             "position": "U5", "detail": "24 порта"},
            {"type": "ups", "brand": "", "model": "", "position": "",
             "detail": "корпус с индикаторами"},
        ]}
    )
    devices = _parse_devices(raw)
    assert len(devices) == 2
    assert devices[0].type_code == "switch"
    assert devices[0].brand == "D-Link"
    assert devices[0].model == "DES-1210"
    assert devices[0].position == "U5"
    assert devices[0].detail == "24 порта"
    assert devices[1].type_code == "ups"


def test_parse_json_wrapped_in_fences_and_prose():
    raw = (
        "Вот что видно на фото:\n```json\n"
        '{"devices": [{"type": "patch", "position": "U1", "detail": "патч-панель"}]}\n'
        "```\nПримечание: тени."
    )
    devices = _parse_devices(raw)
    assert len(devices) == 1
    assert devices[0].type_code == "patch"
    assert devices[0].position == "U1"


def test_parse_not_json_raises():
    with pytest.raises(RuntimeError, match="не JSON"):
        _parse_devices("На фото три коммутатора.")


def test_parse_no_devices_list_raises():
    with pytest.raises(RuntimeError, match="списка devices"):
        _parse_devices('{"items": []}')


def test_parse_aliases_russian_and_english():
    raw = json.dumps(
        {"devices": [
            {"type": "коммутатор"},
            {"type": "ИБП"},
            {"type": "PDU"},
            {"type": "патч-панель"},
            {"type": "видеорегистратор"},
            {"type": "48-port switch"},
            {"type": "что-то невнятное"},
        ]}
    )
    devices = _parse_devices(raw)
    assert [d.type_code for d in devices] == [
        "switch", "ups", "psu", "patch", "nvr", "switch", "other",
    ]


def test_parse_defaults_and_what_seen_fallback():
    raw = json.dumps({"devices": [
        {"type": "switch", "brand": None, "model": None,
         "position": None, "what_seen": "моргание светодиодов"},
    ]})
    (device,) = _parse_devices(raw)
    assert device.brand == ""
    assert device.position == ""
    assert device.detail == "моргание светодиодов"


def test_parse_skips_non_dict_items():
    raw = json.dumps({"devices": ["мусор", {"type": "ups"}]})
    (device,) = _parse_devices(raw)
    assert device.type_code == "ups"


def test_parse_caps_at_max_devices():
    raw = json.dumps({"devices": [{"type": "switch"}] * (MAX_DEVICES + 10)})
    assert len(_parse_devices(raw)) == MAX_DEVICES


def test_analysis_summary_counts():
    analysis = RackAnalysis(ok=True, devices=[
        RackDevice(type_code="switch"), RackDevice(type_code="switch"),
        RackDevice(type_code="ups"),
    ])
    assert analysis.summary == "2 шт, 1 шт"
    bad = RackAnalysis(ok=False, error="Нет связи")
    assert bad.summary == "Нет связи"


# ---------------------------------------------------------------- status
def test_ollama_status_fast_fail_on_dead_port(monkeypatch):
    monkeypatch.setattr(rack_ai, "OLLAMA_BASE", "http://127.0.0.1:59999")
    ok, reason = ollama_status()
    assert ok is False
    assert "ollama" in reason.lower()


def test_pick_model_prefers_preferred_candidates():
    assert pick_model(["llava:7b", "qwen2.5vl:3b"]) == "qwen2.5vl:3b"
    assert pick_model(["qwen2.5:7b", "llava:13b"]) == "llava:13b"


def test_pick_model_no_vision_raises():
    with pytest.raises(RuntimeError, match="vision"):
        pick_model(["qwen2.5:7b"])


def test_analyse_unreachable_ollama_degrades(monkeypatch):
    monkeypatch.setattr(rack_ai, "OLLAMA_BASE", "http://127.0.0.1:59999")
    analysis = rack_ai.analyse_rack_photo("не-важно.jpg")
    assert analysis.ok is False
    assert analysis.error
    assert analysis.elapsed_s >= 0


# ---------------------------------------------------------------- v1.6.1
def test_build_payload_has_format_and_caps():
    payload = rack_ai._build_payload("qwen2.5vl:3b", "AAAA", use_format=True)
    assert payload["format"] == "json"
    assert payload["keep_alive"] == rack_ai.KEEP_ALIVE
    assert payload["options"]["num_predict"] == rack_ai.NUM_PREDICT
    without = rack_ai._build_payload("m", "AAAA", use_format=False)
    assert "format" not in without


def test_classify_error_400():
    claim = rack_ai._classify_error_400
    assert claim("model context window is full") == "shrink"
    assert claim("HTTP 400: Prompt size exceeds limit") == "shrink"
    assert claim("invalid format option") == "no_format"
    assert claim("что-то иное") == "give_up"


def test_position_sort_key_orders_units():
    order = ["U2", "U5", "U5-U8", "нижняя треть", ""]
    assert sorted(order, key=rack_ai.position_sort_key) == order


def test_vl_candidates_order_and_preferred():
    installed = ["qwen2.5vl:3b", "qwen2.5:7b", "llava:13b"]
    assert rack_ai._vl_candidates(installed) == ["qwen2.5vl:3b", "llava:13b"]
    assert rack_ai._vl_candidates(installed, preferred=None)[0] == "qwen2.5vl:3b"
    swapped = rack_ai._vl_candidates(installed, preferred="llava:13b")
    assert swapped[0] == "llava:13b"


def test_write_log_jsonl(tmp_path):
    analysis = RackAnalysis(
        ok=True,
        model_used="m",
        elapsed_s=12.5,
        devices=[RackDevice(type_code="switch", brand="B", model="M")],
        raw_text="{}",
    )
    rack_ai._write_log(tmp_path, "стойка 1.jpg", analysis)
    lines = (Path(tmp_path) / "rack_ai_log.jsonl").read_text(encoding="utf-8")
    entry = json.loads(lines.splitlines()[-1])
    assert entry["photo"] == "стойка 1.jpg"
    assert entry["ok"] is True and entry["n_devices"] == 1
    assert entry["devices"][0]["brand"] == "B"


class _FakeOllama:
    """Подмена urlopen: /api/tags, /api/ps и сценарий /api/chat."""

    def __init__(self, installed: list[str], chat_script: list[dict]):
        self.installed = installed
        self.chat_script = list(chat_script)
        self.chat_calls: list[dict] = []

    def __call__(self, request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else request
        if url.endswith("/api/tags"):
            payload = json.dumps(
                {"models": [{"name": n} for n in self.installed]}
            ).encode()
        elif url.endswith("/api/ps"):
            payload = b"{\"models\": []}"
        else:
            body = json.loads(request.data.decode("utf-8"))
            self.chat_calls.append(body)
            step = self.chat_script.pop(0) if self.chat_script else {"ok": True}
            if not step.get("ok"):
                raise urllib.error.HTTPError(
                    url, 400, "Bad Request", {}, io.BytesIO(step["msg"].encode())
                )
            payload = json.dumps(
                {"message": {"content": step.get("content", "{}")}}
            ).encode()
        return io.BytesIO(payload)


_CANNED_CONTENT = json.dumps(
    {"devices": [{"type": "switch", "brand": "D-Link", "model": "DES-1210"}]}
)


@pytest.fixture()
def fake_urlopen(monkeypatch):
    """Вешает подмену urlopen только внутри rack_ai; возвращает хелпер."""
    holder: dict[str, _FakeOllama] = {}

    def install(instance: _FakeOllama) -> _FakeOllama:
        holder["fake"] = instance
        monkeypatch.setattr(
            rack_ai.urllib.request, "urlopen", instance, raising=False
        )
        monkeypatch.setattr(rack_ai, "OLLAMA_BASE", "http://127.0.0.1:59999")
        return instance

    yield install
    holder.clear()


def test_analyse_retries_with_shrunk_image(fake_urlopen, monkeypatch):
    fake = fake_urlopen(
        _FakeOllama(
            ["qwen2.5vl:3b"],
            [
                {"ok": False, "msg": "HTTP 400: context window exceeded"},
                {"ok": True, "content": _CANNED_CONTENT},
            ],
        )
    )
    sides: list[int] = []

    def fake_load(path, max_side=rack_ai.MAX_IMAGE_SIDE):
        sides.append(max_side)
        return "B64"

    monkeypatch.setattr(rack_ai, "_load_image_payload", fake_load)
    analysis = rack_ai.analyse_rack_photo("photo.jpg", log_dir=None)
    assert analysis.ok is True
    assert len(fake.chat_calls) == 2
    assert sides == [rack_ai.MAX_IMAGE_SIDE, int(rack_ai.MAX_IMAGE_SIDE * 0.7)]
    # Оба запроса остались с format:json — сжатие кадра формат не трогает.
    assert fake.chat_calls[0]["format"] == "json"
    assert fake.chat_calls[1]["format"] == "json"


def test_analyse_drops_format_on_format_error(fake_urlopen, monkeypatch):
    fake = fake_urlopen(
        _FakeOllama(
            ["qwen2.5vl:3b"],
            [
                {"ok": False, "msg": "HTTP 400: unknown format option"},
                {"ok": False, "msg": "HTTP 400: context window exceeded"},
                {"ok": True, "content": _CANNED_CONTENT},
            ],
        )
    )
    monkeypatch.setattr(
        rack_ai, "_load_image_payload", lambda path, max_side=None: "B64"
    )
    analysis = rack_ai.analyse_rack_photo("photo.jpg")
    assert analysis.ok is True
    assert fake.chat_calls[0].get("format") == "json"
    assert "format" not in fake.chat_calls[-1]


def test_analyse_falls_back_to_next_vision_model(fake_urlopen, monkeypatch):
    fake = fake_urlopen(
        _FakeOllama(
            ["qwen2.5vl:3b", "qwen2.5vl:7b"],
            [
                {"ok": False, "msg": "HTTP 500: model exploded"},
                {"ok": True, "content": _CANNED_CONTENT},
            ],
        )
    )
    monkeypatch.setattr(
        rack_ai, "_load_image_payload", lambda path, max_side=None: "B64"
    )
    analysis = rack_ai.analyse_rack_photo("photo.jpg")
    assert analysis.ok is True
    assert analysis.model_used == "qwen2.5vl:7b"
    assert fake.chat_calls[0]["model"] == "qwen2.5vl:3b"


def test_analyse_logs_every_way(tmp_path, fake_urlopen, monkeypatch):
    fake_urlopen(
        _FakeOllama(
            ["qwen2.5vl:3b"],
            [{"ok": True, "content": _CANNED_CONTENT}],
        )
    )
    monkeypatch.setattr(
        rack_ai, "_load_image_payload", lambda path, max_side=None: "B64"
    )
    analysis = rack_ai.analyse_rack_photo("стойка.jpg", log_dir=tmp_path)
    assert analysis.ok is True
    entry = json.loads(
        (Path(tmp_path) / "rack_ai_log.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )
    assert entry["model"] == "qwen2.5vl:3b"
    assert entry["photo"] == "стойка.jpg"
    assert entry["n_devices"] == 1

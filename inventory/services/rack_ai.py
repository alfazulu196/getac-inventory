# inventory/services/rack_ai.py
"""Распознавание оборудования серверной стойки по фото через локальную ollama.

Полностью офлайн: ollama запущена на этом же ноутбуке (127.0.0.1), пакет
requests не нужен — только urllib. Функция опциональна: если ollama не
запущена или vision-модель не скачана, приложение работает как раньше,
а кноки «Стойка» сообщают причину.

Модель обещает список ЮНИТОВ, ВИДИМЫХ на фото (по виду и надписям на
панелях). S/N и MAC на расстоянии стойки не читаются — их вносят потом
лазером или крупным кадром этикетки («🔍 Этикетка»).

Устойчивость (v1.6.1):
- `format: json` — модель обязана вернуть строгий JSON;
- ошибка 400 «контекст переполнен» → кадр до 3 раз пересжимается;
- сбой конкретной модели → перебор прочих установленных vision-моделей;
- `keep_alive` — модель остаётся в памяти между кадрами;
- все попытки пишутся в `data/logs/rack_ai_log.jsonl` (если передан log_dir).
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

OLLAMA_BASE = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5vl:3b"
# Кандидаты на случай, если дефолтной модели нет на ноуте.
VL_FALLBACKS = (
    DEFAULT_MODEL,
    "qwen2.5vl:7b",
    "qwen2.5vl",
    "llama3.2-vision",
    "minicpm-v",
    "llava",
    "moondream",
)
# Qwen2.5-VL превращает картинку в «визуальные токены»: при стороне 4000 px их
# выходит ~20 000 — не влезает в контекст (и мгновенный 400 от ollama), на CPU
# такой префилл занимает бесконечность. 1280 px ≈ 500–800 токенов: текст на
# панелях читается, кадр разбора занимает 1–2 минуты на CPU.
MAX_IMAGE_SIDE = 1280
MAX_DEVICES = 40           # больше юнитов в одной стойке не бывает
REQUEST_TIMEOUT = 600      # сек: CPU-инференс тягучий, кадр не один
NUM_PREDICT = 2048         # потолок генерации: защита от «залипания» модели
KEEP_ALIVE = "30m"         # модель в памяти после кадра: разогрев не сбрасывается
SHRINK_ATTEMPTS = 3        # пересжатия при ошибке контекста (1280→896→627→439)

_PROMPT_V162 = """Ты помощник полевой инвентаризации. Посмотри на фото серверной
стойки или телекоммуникационного шкафа. Составь список ЕДИНИЦ ОБОРУДОВАНИЯ,
РЕАЛЬНО ВИДИМЫХ на фото: каждый занятый юнит или блок — отдельная запись
(коммутатор, регистратор, сервер, ИБП, патч-панель, блок розеток/PDU,
полка, маршрутизатор, точка доступа, камера). ПИШИ ТОЛЬКО ТО, ЧТО ВИДНО:
не придумывай производителей и модели; если надпись не читается — оставь
поле пустым и опиши вид в detail по-русски.
ВАЖНО: если кадр крупный и на нём видно только ОДНО устройство (например,
этикетка одного коммутатора) — вернуть ровно одну запись; не дорисовывать
юниты ИБП/патч-панелей, которых не видно, и не вписывать устройства,
«которые обычно стоят рядом». Число записей = число реально видимых юнитов.

Для каждой записи:
- type — ОДНО из: switch, nvr, dvr, router, ap, ups, psu, patch, server,
  workstation, camera, other;
- brand — только если читается на панели (HP, Cisco, D-Link, APC...);
- model — только если надпись модели читается (иначе пусто);
- position — юниты стойки, например "U5-U8", если понятно, иначе пусто;
- detail — короткое описание по-русски: панель портов/индикаторы/вентиляторы.

Ответ — ТОЛЬКО JSON-объект БЕЗ пояснений:
{"devices":[{"type":"switch","brand":"","model":"","position":"","detail":""}]}
"""

# Добавки промпта v1.6.3 — проверены промпт-лабой на бенчмарке
# (полнота 65% → 72%, пропуски 6→1, цикл повторения убит). Текст ровно тот,
# что в tools/prompt_lab.py; тест test_prompt_matches_prompt_lab_combo
# следит за паритетом. Каждая добавка начинается с "\n\n".
PROMPT_ADDITIONS_V163: tuple[str, ...] = (
    "\n\nДОПОЛНИТЕЛЬНО: каждый юнит — ровно ОДНА запись. Уже названный юнит не"
    "\nповторяйте и не добавляйте строки «для порядка»; закончив список видимых"
    "\nюнитов, сразу завершите ответ.",
    "\n\nДОПОЛНИТЕЛЬНО: сверяйте позиции с номерами на бирках левой шины. Если"
    "\nустройство занимает НЕСКОЛЬКО рядов — пишите диапазон нижнего-верхнего"
    "\nряда (например «U2-U3»), а не один из них. Корпуса высотой 2U (серверы,"
    "\nИБП) почти всегда дают диапазон из двух номеров.",
    "\n\nДОПОЛНИТЕЛЬНО: тип определяйте по форме, а не по догадке: узкая полоса"
    "\nс розетками = psu (PDU); крупный корпус с индикаторами батарей = ups;"
    "\nпанель с гнёздами RJ-45 без индикаторов = patch; корпус с портами и"
    "\nиндикаторами = switch; блок с отсеками для дисков = server или nvr.",
)

_PROMPT = _PROMPT_V162 + "".join(PROMPT_ADDITIONS_V163)
# Переведём свободные формулировки модели в коды типов приложения.
_TYPE_ALIASES: dict[str, str] = {
    "switch": "switch", "коммутатор": "switch", "свич": "switch",
    "nvr": "nvr", "регистратор": "nvr", "видеорегистратор": "nvr",
    "dvr": "dvr", "аналоговый регистратор": "dvr",
    "router": "router", "маршрутизатор": "router", "роутер": "router",
    "ap": "ap", "точка доступа": "ap", "wifi": "ap", "wi-fi": "ap",
    "ups": "ups", "ибп": "ups",
    "psu": "psu", "блок питания": "psu", "pdu": "psu", "блок розеток": "psu",
    "patch": "patch", "патч-панель": "patch", "патч панель": "patch",
    "панель": "patch",
    "server": "server", "сервер": "server",
    "workstation": "workstation", "компьютер": "workstation",
    "рабочая станция": "workstation",
    "camera": "camera", "камера": "camera", "ip-камера": "camera",
    "shelf": "other", "полка": "other", "organizer": "other", "other": "other",
    "неизвестно": "other", "unknown": "other", "прочее": "other",
    "блок": "other", "module": "other", "модуль": "other",
}


@dataclass
class RackDevice:
    """Строка черновика стойки: одно видимое устройство/блок."""

    type_code: str = "other"
    brand: str = ""
    model: str = ""
    position: str = ""
    detail: str = ""

    @property
    def title(self) -> str:
        name = self.brand or ""
        if self.model:
            name = (name + " " + self.model).strip()
        if not name:
            name = self.detail or "Юнит без описания"
        return name


@dataclass
class RackAnalysis:
    """Результат разбора фото стойки (может быть ошибкой — ок/error)."""

    devices: list[RackDevice] = field(default_factory=list)
    model_used: str = ""
    ok: bool = False
    error: str | None = None
    elapsed_s: float = 0.0
    raw_text: str = ""

    @property
    def summary(self) -> str:
        if not self.ok:
            return self.error or "Разбор не удался"
        counts: dict[str, int] = {}
        for device in self.devices:
            counts[device.type_code] = counts.get(device.type_code, 0) + 1
        return ", ".join(f"{count} шт" for count in counts.values()) or "Пусто"


# ----------------------------------------------------------------------------
# Доступность ollama и моделей
# ----------------------------------------------------------------------------
def _fetch_models() -> tuple[bool, Any]:
    """(ok, список моделей | причина) — GET /api/tags."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"ollama не запущена ({exc.__class__.__name__})"
    return True, [str(item.get("name", "")) for item in data.get("models", [])]


def loaded_models() -> list[str]:
    """Какие модели прямо сейчас сидят в памяти (GET /api/ps)."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE}/api/ps", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return [str(item.get("name", "")) for item in data.get("models", [])]
    except (urllib.error.URLError, OSError, ValueError):
        return []


def ollama_status(model: str = DEFAULT_MODEL) -> tuple[bool, str]:
    """(доступен, причина) для распознавания стойки."""
    ok, data = _fetch_models()
    if not ok:
        return False, str(data)
    models = data
    if not models:
        return False, "ollama запущена, но моделей не скачано ни одной"
    for candidate in VL_FALLBACKS:
        for name in models:
            if name == candidate or name.split(":")[0] == candidate:
                return True, name
    return False, "vision-модель не скачана (ollama pull qwen2.5vl:3b)"


def _vl_candidates(
    installed: list[str], preferred: str | None = None
) -> list[str]:
    """Установленные vision-модели в порядке предпочтения (чистая функция)."""
    ordered: list[str] = []
    for candidate in VL_FALLBACKS:
        found = next(
            (n for n in installed if n == candidate or n.split(":")[0] == candidate),
            None,
        )
        if found and found not in ordered:
            ordered.append(found)
    if preferred:
        exact = next((n for n in ordered if n == preferred), None)
        if exact is None:
            exact = next(
                (n for n in ordered if n.split(":")[0] == preferred.split(":")[0]),
                None,
            )
        if exact:
            ordered.remove(exact)
            ordered.insert(0, exact)
    return ordered


def pick_model(models: list[str] | None = None) -> str:
    """Выбрать vision-модель из установленных; кидает RuntimeError."""
    if models is None:
        ok, info = ollama_status()
        if not ok:
            raise RuntimeError(info)
        models = [info]
    candidates = _vl_candidates(models)
    if not candidates:
        raise RuntimeError("vision-модель не найдена")
    return candidates[0]


def warm_up_model(model: str | None = None) -> str:
    """Прогреть модель в память, чтобы не грузиться на первом кадре.

    Возвращает '' при успехе или текст причины. Таймаут весь (загрузка
    3.2 ГБ с диска занимает до минуты).
    """
    name = model
    if not name:
        ok, info = ollama_status()
        if not ok:
            return info
        name = info
    payload = {
        "model": name,
        "messages": [{"role": "user", "content": "OK"}],
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "options": {"num_predict": 1},
    }
    try:
        _post_chat(payload)
        return ""
    except RuntimeError as exc:
        return str(exc)


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
def _build_payload(model: str, image_b64: str, use_format: bool = True) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": _PROMPT, "images": [image_b64]}],
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "options": {"temperature": 0.1, "num_ctx": 4096, "num_predict": NUM_PREDICT},
    }
    if use_format:
        payload["format"] = "json"  # модель обязана вернуть строгий JSON
    return payload


def _classify_error_400(message: str) -> str:
    """Классификация отказа ollama: 'shrink' | 'no_format' | 'give_up'."""
    m = message.lower()
    if any(
        key in m
        for key in ("context window", "prompt size", "token limit", "too long",
                    "exceed")
    ):
        return "shrink"
    if "format" in m:
        return "no_format"
    return "give_up"


def _post_chat(payload: dict) -> str:
    """POST /api/chat → текст ответа модели. Ошибки → RuntimeError."""
    request = urllib.request.Request(
        f"{OLLAMA_BASE}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — тело может отсутствовать
            pass
        raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RuntimeError(f"связь с ollama: {exc}") from exc
    message = body.get("message") or {}
    return str(message.get("content", ""))


# ----------------------------------------------------------------------------
# Разбор фото
# ----------------------------------------------------------------------------
def _load_image_payload(path: str | Path, max_side: int = MAX_IMAGE_SIDE) -> str:
    """Фото → базовая64 JPEG (с урезанием мегапикселей ради скорости)."""
    import cv2
    import numpy as np

    data = np.fromfile(str(path), dtype=np.uint8)  # пути с кириллицей
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("файл не читается как изображение")
    height, width = img.shape[:2]
    side = max(height, width)
    if side > max_side:
        scale = max_side / side
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise RuntimeError("не удалось пересжать фото")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _parse_devices(raw: str) -> list[RackDevice]:
    text = raw.strip()
    # Модель любит обернуть JSON в ```json ... ``` или проза — вырезаем блок.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise RuntimeError(f"модель ответила не JSON: {exc}") from exc
    items = payload.get("devices") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise RuntimeError("в ответе модели нет списка devices")
    devices: list[RackDevice] = []
    for item in items[:MAX_DEVICES]:
        if not isinstance(item, dict):
            continue
        type_raw = str(item.get("type", "") or "").strip().lower()
        type_code = _TYPE_ALIASES.get(type_raw)
        if type_code is None:
            # Ищем подстроку среди алиасов («48-port switch» → switch)
            type_code = "other"
            for alias, code in _TYPE_ALIASES.items():
                if alias in type_raw:
                    type_code = code
                    break
        devices.append(
            RackDevice(
                type_code=type_code,
                brand=str(item.get("brand") or "").strip(),
                model=str(item.get("model") or "").strip(),
                position=str(item.get("position") or "").strip(),
                detail=(str(item.get("detail") or "").strip() or
                        str(item.get("what_seen") or "").strip()),
            )
        )
    return devices


def _write_log(log_dir: str | Path, path: str, analysis: "RackAnalysis") -> None:
    """Журнал разборов → data/logs/rack_ai_log.jsonl (для дебрифинга)."""
    try:
        base = Path(log_dir)
        base.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "photo": Path(path).name,
            "model": analysis.model_used,
            "ok": analysis.ok,
            "error": analysis.error,
            "elapsed_s": round(analysis.elapsed_s, 1),
            "n_devices": len(analysis.devices),
            "devices": [
                {
                    "type": d.type_code, "brand": d.brand, "model": d.model,
                    "position": d.position, "detail": d.detail,
                }
                for d in analysis.devices
            ],
            "raw": (analysis.raw_text or "")[:400],
        }
        with (base / "rack_ai_log.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # журнал — полезность, а не обязательность


def position_sort_key(text: str) -> tuple[int, int, str]:
    """Ключ сортировки юнитов: U2 < U5 < U5-U8; ненормированные; пустые."""
    raw = (text or "").strip()
    if not raw:
        return (2, 0, "")
    match = re.match(r"u(\d+)", raw, re.IGNORECASE)
    if not match:
        match = re.match(r"(\d+)", raw)
    if match:
        return (0, int(match.group(1)), raw)
    return (1, 0, raw.lower())


def analyse_rack_photo(
    path: str | Path,
    model: str | None = None,
    *,
    log_dir: str | Path | None = None,
) -> RackAnalysis:
    """Полный разбор фото стойки. Блокирующий; из Qt вызывать в воркере.

    Цепочка деградации: на 400 «context window» кадр до SHRINK_ATTEMPTS раз
    пересжимается (1280→896→…); на отказ «format» формат снимается; на
    провал модели — перебор прочих установленных vision-моделей.
    """
    analysis = RackAnalysis()
    t0 = time.perf_counter()
    try:
        ok, data = _fetch_models()
        if not ok:
            analysis.error = str(data)
            return analysis
        installed = data
        candidates = _vl_candidates(installed, preferred=model)
        if not candidates:
            analysis.error = "в ollama нет установленных vision-моделей"
            return analysis
        thumb_cache: dict[int, str] = {}
        use_format = True
        side = MAX_IMAGE_SIDE
        raw = ""
        last_error = ""
        done = False
        for attempt_model in candidates:
            analysis.model_used = attempt_model
            for _ in range(1 + SHRINK_ATTEMPTS):
                if side not in thumb_cache:
                    thumb_cache[side] = _load_image_payload(path, max_side=side)
                try:
                    raw = _post_chat(
                        _build_payload(attempt_model, thumb_cache[side], use_format)
                    )
                    done = True
                    break
                except RuntimeError as exc:
                    last_error = str(exc)
                    verdict = _classify_error_400(str(exc))
                    if verdict == "no_format" and use_format:
                        use_format = False
                        continue
                    if verdict == "shrink" and side > 512:
                        side = int(side * 0.7)
                        continue
                    break  # эта модель не подходит — пробуем следующую
            if done:
                break
        if not done:
            analysis.error = last_error or "обращения к ollama не удались"
            return analysis
        analysis.raw_text = raw
        analysis.devices = _parse_devices(raw)
        analysis.ok = True
        return analysis
    except Exception as exc:  # noqa: BLE001 — весь конвейер мягко деградирует
        analysis.error = f"{exc.__class__.__name__}: {exc}"
        return analysis
    finally:
        analysis.elapsed_s = time.perf_counter() - t0
        if log_dir:
            _write_log(log_dir, path, analysis)

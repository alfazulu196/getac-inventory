# inventory/services/photo_server.py
"""Приём фотографий с телефона по Wi-Fi (полностью офлайн).

Сценарий «труднодоступные места»: телефон подключён к той же сети, что и
ноутбук (хот-спот Windows на ноутбуке, либо хот-спот телефона). В приложении
есть адрес с токеном в виде QR-кода — телефон открывает его камерой и в
браузере попадает на страницу с кнопками «Снять фото» / «Из галереи».
Каждое принятое фото сохраняется в data/photos и привязывается к активному
устройству из главного окна.

Сервер не трогает базу сам: колбэк on_photo вызывается из потока сервера,
поэтому в UI он превращается в Qt-сигнал, а запись в БД идёт из главного
потока приложения.
"""
from __future__ import annotations

import logging
import re
import socket
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_PORT = 8790
MAX_BODY_BYTES = 30 * 1024 * 1024  # 30 МБ на запрос с несколькими фото
JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def local_addresses() -> list[str]:
    """IPv4-адреса ноутбука, по которым может быть доступен телефон."""
    found: list[str] = []

    def add_checked(value: str) -> None:
        if value in found or not value or value.count(".") != 3:
            return
        if value.startswith(("127.", "169.254.", "0.")):
            return
        if value.startswith(("192.168.", "10.", "172.")):
            found.append(value)

    # 1) Основной маршрутизируемый адрес (работает и в hotspot-сети).
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            add_checked(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass

    # 2) Полный перечень интерфейсов через psutil (если установлен).
    try:
        import psutil

        for addresses in psutil.net_if_addrs().values():
            for item in addresses:
                if item.family == socket.AF_INET:
                    add_checked(item.address)
    except Exception:  # noqa: BLE001
        pass

    # 3) Имя хоста.
    try:
        for item in socket.gethostbyname_ex(socket.gethostname())[2]:
            add_checked(item)
    except OSError:
        pass

    return found


def _safe_prefix(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-.]+", "_", value).strip("_") or "photo"


def _image_ext(payload: bytes) -> str | None:
    if payload.startswith(JPEG_MAGIC):
        return "jpg"
    if payload.startswith(PNG_MAGIC):
        return "png"
    return None


def _parse_multipart(body: bytes, boundary: str) -> list[bytes]:
    """Достаёт содержимое файловых частей multipart-запроса."""
    delim = b"--" + boundary.encode("latin-1")
    payloads: list[bytes] = []
    for chunk in body.split(delim):
        chunk = chunk.strip(b"\r\n")
        if not chunk or chunk == b"--":
            continue
        head, _, payload = chunk.partition(b"\r\n\r\n")
        if not payload:
            continue
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        if b'filename="' not in head.lower():
            continue
        payloads.append(payload)
    return payloads


@dataclass
class ReceivedPhoto:
    path: str
    device_name: str
    location_label: str
    saved_at: str


PAGE_HTML = """<!doctype html><html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=3">
<title>Фото устройства</title>
<style>
 body {{ background:#0f1319; color:#d6eaf8; font-family:-apple-system,'Segoe UI',Roboto,sans-serif;
        margin:0; padding:16px; }}
 h1 {{ font-size:20px; margin:0 0 6px; }}
 .dev {{ color:#f1c40f; font-weight:700; font-size:18px; margin-bottom:4px; }}
 .loc {{ color:#9fb3c8; font-size:14px; }}
 .card {{ background:#111820; border-radius:14px; padding:16px; margin-top:14px; }}
 .btn {{ display:block; width:100%; box-sizing:border-box; border:none; margin-top:12px;
        padding:18px 10px; font-size:18px; font-weight:700; border-radius:12px; text-align:center; }}
 .take {{ background:#2e7d32; color:#fff; }}
 .gal  {{ background:#1e2a38; color:#d6eaf8; }}
 .status {{ margin-top:12px; font-size:15px; color:#9fb3c8; }}
 .status.ok {{ color:#2ecc71; }}
 .status.err {{ color:#e67e22; }}
 li {{ margin-top:6px; word-break:break-all; }}
 input[type=file] {{ display:none; }}
</style></head><body>
<h1>📷 Фотография устройства</h1>
<div class="dev" id="dev">…</div>
<div class="loc" id="loc">…</div>
<div class="card">
  <input id="take" type="file" accept="image/*" capture="environment">
  <label class="btn take" for="take">📸 Снять фото</label>
  <input id="gal" type="file" accept="image/*" multiple>
  <label class="btn gal" for="gal">🖼 Выбрать из галереи</label>
  <div class="status" id="status">Загрузка…</div>
  <ul id="done"></ul>
</div>
<script>
const T = "{token}";
async function info() {{
  try {{
    const r = await fetch(('/' + T + '/info'), {{cache:'no-store'}});
    const d = await r.json();
    document.getElementById('dev').textContent = d.device || 'Нет активного устройства';
    document.getElementById('loc').textContent = d.location || '';
    if (!d.device) {{
      document.getElementById('status').textContent = 'На ноутбуке нет активного устройства';
      document.getElementById('status').className = 'status err';
    }} else {{
      document.getElementById('status').textContent = 'Готово к отправке';
      document.getElementById('status').className = 'status';
    }}
  }} catch (e) {{
    document.getElementById('status').textContent = 'Нет связи с ноутбуком';
    document.getElementById('status').className = 'status err';
  }}
}}
async function send(files) {{
  const st = document.getElementById('status');
  if (!files || !files.length) return;
  st.textContent = 'Отправка…'; st.className = 'status';
  const fd = new FormData();
  for (const f of files) fd.append('file', f);
  try {{
    const r = await fetch(('/' + T + '/upload'), {{method:'POST', body:fd}});
    const d = await r.json();
    if (!d.ok) {{ st.textContent = 'Ошибка: ' + (d.error || 'нет ответа'); st.className = 'status err'; return; }}
    st.textContent = 'Принято: ' + d.saved.length;
    st.className = 'status ok';
    const ul = document.getElementById('done');
    for (const name of d.saved) {{
      const li = document.createElement('li');
      li.textContent = '✅ ' + name;
      ul.prepend(li);
    }}
  }} catch (e) {{
    st.textContent = 'Не отправилось: ' + e; st.className = 'status err';
  }}
  info();
}}
document.getElementById('take').addEventListener('change', e => send(e.target.files));
document.getElementById('gal').addEventListener('change', e => send(e.target.files));
document.addEventListener('visibilitychange', info);
info();
</script>
</body></html>
"""

RESULT_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
</head><body><p>{text}</p></body></html>"""


class PhotoServer:
    """Локальный HTTP-сервер приёма фото с телефона.

    Вызов start() поднимает сервер и возвращает список адресов страницы;
    колбэк on_photo(path, device_name) вызывается из потока сервера.
    """

    def __init__(self, photos_dir: Path, on_photo=None, port_hint: int = DEFAULT_PORT):
        self.photos_dir = Path(photos_dir)
        self.on_photo = on_photo
        self.token = uuid.uuid4().hex[:8]
        self.device_name = ""
        self.location_label = ""
        self.port = 0
        self.urls: list[str] = []
        self.received: list[ReceivedPhoto] = []
        self._port_hint = port_hint
        self._context_lock = threading.Lock()
        self._received_lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Жизненный цикл
    # ------------------------------------------------------------------
    def _bind(self) -> None:
        last_error: OSError | None = None
        for port in range(self._port_hint, self._port_hint + 10):
            try:
                self._server = _PhotoHTTPServer(("0.0.0.0", port), _PhotoHandler)
                self.port = port
                return
            except OSError as exc:
                last_error = exc
        raise RuntimeError(f"Не найден свободный порт {self._port_hint}+: {last_error}")

    def start(self) -> list[str]:
        if self._server is not None:
            return self.urls
        self._bind()
        self._server.server_ref = self  # noqa: type — доступ обработчика к состоянию
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="photo-server", daemon=True
        )
        self._thread.start()
        self.urls = [f"http://{ip}:{self.port}/{self.token}" for ip in local_addresses()]
        return self.urls

    def stop(self) -> None:
        if self._server is None:
            return
        try:
            self._server.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._server.server_close()
        except Exception:  # noqa: BLE001
            pass
        self._server = None
        self._thread = None

    # ------------------------------------------------------------------
    # Контекст: какое устройство сейчас активно
    # ------------------------------------------------------------------
    def set_context(self, device_name: str, location_label: str) -> None:
        with self._context_lock:
            self.device_name = device_name
            self.location_label = location_label

    def snapshot_context(self) -> tuple[str, str]:
        with self._context_lock:
            return self.device_name, self.location_label

    # ------------------------------------------------------------------
    # Приём файла (вызывается из потока обработчика)
    # ------------------------------------------------------------------
    def store_photos(self, payloads: list[bytes]) -> dict:
        device, location = self.snapshot_context()
        prefix = _safe_prefix(device or "no-device")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved: list[str] = []
        rejected = 0
        try:
            self.photos_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "error": f"папка фото недоступна: {exc}"}
        for index, payload in enumerate(payloads, start=1):
            ext = _image_ext(payload)
            if ext is None:
                rejected += 1
                continue
            index = len(saved) + 1
            base = f"{prefix}_{stamp}"
            if index > 1:
                base = f"{base}_{index}"
            candidate = self.photos_dir / f"{base}.{ext}"
            counter = 2
            while candidate.exists():
                candidate = self.photos_dir / f"{base}_{counter}.{ext}"
                counter += 1
            try:
                candidate.write_bytes(payload)
            except OSError as exc:
                return {"ok": False, "error": f"не сохранить файл: {exc}"}
            logging.getLogger("inventory.photo").info(
                "Фото с телефона принято: %s (устройство %s)", candidate.name, device or "—"
            )
            saved.append(candidate.name)
            record = ReceivedPhoto(
                path=str(candidate),
                device_name=device,
                location_label=location,
                saved_at=datetime.now().strftime("%H:%M:%S"),
            )
            with self._received_lock:
                self.received.append(record)
            if self.on_photo:
                try:
                    self.on_photo(record.path, record.device_name)
                except Exception:  # noqa: BLE001
                    pass
        if not saved:
            error = (
                "формат фото не JPEG/PNG — переключите камеру телефона на JPEG"
                if rejected
                else "фото не найдено в данных"
            )
            return {"ok": False, "error": error, "saved": []}
        return {"ok": True, "saved": saved}

    def recent_received(self) -> list[ReceivedPhoto]:
        with self._received_lock:
            return list(self.received)


def find_free_port(hint: int = DEFAULT_PORT, attempts: int = 10) -> int | None:
    """Первый реально свободный порт в диапазоне (None — ни один).

    Лёгкая проверка для preflight: создаём и тут же закрываем сервер-сокет.
    """
    for port in range(hint, hint + attempts):
        server = None
        try:
            server = _PhotoHTTPServer(("0.0.0.0", port), _PhotoHandler)
            return port
        except OSError:
            continue
        finally:
            if server is not None:
                try:
                    server.server_close()
                except OSError:
                    pass
    return None


class _PhotoHTTPServer(ThreadingHTTPServer):
    # allow_reuse_address=False: на Windows SO_REUSEADDR позволяет двум
    # процессам держать один порт одновременно (например, старый и новый
    # экземпляры приложения) — тогда приёма фото уходит старому процессу
    # с чужим токеном и отбиваются как «нет доступа». Явная ошибка занятого
    # порта лучше молчаливой подмены: _bind() возьмёт следующий порт.
    allow_reuse_address = False


class _PhotoHandler(BaseHTTPRequestHandler):
    server: ThreadingHTTPServer

    def log_message(self, *args) -> None:  # тишина в логах сервера
        return

    # ------------------------------------------------------------------
    def _auth(self) -> tuple[PhotoServer, str] | None:
        state: PhotoServer = getattr(self.server, "server_ref")
        token = urlparse(self.path).path.strip("/").split("/")[0]
        if token != state.token:
            self._answer(403, "НЕТ ДОСТУПА", state)
            return None
        return state, token

    def _answer(self, code: int, text: str, state: PhotoServer) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        try:
            self.wfile.write(RESULT_HTML.format(text=text).encode("utf-8"))
        except Exception:  # noqa: BLE001
            pass

    def _json(self, data: dict) -> None:
        import json

        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        auth = self._auth()
        if not auth:
            return
        state, token = auth
        route = urlparse(self.path).path.strip("/")
        if route == token:
            device, location = state.snapshot_context()
            page = PAGE_HTML.format(token=state.token, dev=device, loc=location)
            payload = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif route == f"{token}/info":
            device, location = state.snapshot_context()
            self._json({"device": device, "location": location})
        else:
            state_urls = ", ".join(state.urls) or "—"
            self._answer(404, "Страница не найдена. Откройте адрес из приложения.", state)

    def do_POST(self) -> None:  # noqa: N802
        auth = self._auth()
        if not auth:
            return
        state, token = auth
        route = urlparse(self.path).path.strip("/")
        if route != f"{token}/upload":
            self._answer(404, "Неизвестный маршрут.", state)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY_BYTES:
            self._json({"ok": False, "error": "слишком большой или пустой запрос"})
            return
        ctype = self.headers.get("Content-Type", "")
        boundary = ""
        if ctype.startswith("multipart/form-data") and "boundary=" in ctype:
            boundary = ctype.split("boundary=", 1)[1].strip().strip('"')
        if not boundary:
            self._json({"ok": False, "error": "нужен multipart/form-data"})
            return
        try:
            body = self.rfile.read(length)
        except Exception:  # noqa: BLE001
            self._json({"ok": False, "error": "обрыв связи"})
            return
        self._json(state.store_photos(_parse_multipart(body, boundary)))

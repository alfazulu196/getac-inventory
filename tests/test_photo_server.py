# tests/test_photo_server.py
"""Проверки локального сервера приёма фото с телефона."""
from __future__ import annotations

import http.client
import json
import socket
from contextlib import closing
from pathlib import Path

from inventory.services.photo_server import PhotoServer

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40
JPEG = b"\xff\xd8\xff" + b"1" * 40


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(port: int, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    payload = response.read()
    conn.close()
    return response.status, payload


def _multipart(boundary: str, payloads: list[bytes]) -> bytes:
    chunks = []
    for index, payload in enumerate(payloads, start=1):
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="pic{index}.jpg"\r\n'
                f"Content-Type: image/jpeg\r\n\r\n"
            ).encode("ascii") + payload + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks)


def make_server(tmp_path: Path) -> tuple[PhotoServer, list[tuple[str, str]]]:
    calls: list[tuple[str, str]] = []
    server = PhotoServer(tmp_path / "photos", on_photo=lambda path, device: calls.append((path, device)))
    server.token = "tok12345"
    server.start()
    return server, calls


def test_wrong_token_rejected(tmp_path: Path):
    server, _ = make_server(tmp_path)
    try:
        status, _ = _request(server.port, "GET", "/WRONG")
        assert status == 403
    finally:
        server.stop()


def test_page_and_info_endpoints(tmp_path: Path):
    server, _ = make_server(tmp_path)
    try:
        status, page = _request(server.port, "GET", f"/{server.token}")
        assert status == 200
        assert "Снять фото".encode() in page
        server.set_context("CAM-007", "Этаж 2 · Помещение 204")
        status, body = _request(server.port, "GET", f"/{server.token}/info")
        data = json.loads(body.decode("utf-8"))
        assert status == 200
        assert data["device"] == "CAM-007"
        assert data["location"] == "Этаж 2 · Помещение 204"
    finally:
        server.stop()


def test_upload_saves_png_and_jpeg_with_callback(tmp_path: Path):
    server, calls = make_server(tmp_path)
    try:
        server.set_context("CAM-007", "")
        status, body = _request(
            server.port,
            "POST",
            f"/{server.token}/upload",
            body=_multipart("bNdr", [PNG, JPEG]),
            headers={"Content-Type": "multipart/form-data; boundary=bNdr"},
        )
        data = json.loads(body.decode("utf-8"))
        assert status == 200
        assert data["ok"] is True
        assert len(data["saved"]) == 2
        for name in data["saved"]:
            assert (tmp_path / "photos" / name).exists()
        assert all(name.startswith("CAM-007_") for name in data["saved"])
        assert len(calls) == 2
        assert all(device == "CAM-007" for _, device in calls)
    finally:
        server.stop()


def test_upload_rejects_non_image_payload(tmp_path: Path):
    server, calls = make_server(tmp_path)
    try:
        status, body = _request(
            server.port,
            "POST",
            f"/{server.token}/upload",
            body=_multipart("bNdr", [b"not-an-image"]),
            headers={"Content-Type": "multipart/form-data; boundary=bNdr"},
        )
        data = json.loads(body.decode("utf-8"))
        assert status == 200
        assert data["ok"] is False
        assert data["saved"] == []
        assert calls == []
    finally:
        server.stop()


def test_stop_is_idempotent(tmp_path: Path):
    server, _ = make_server(tmp_path)
    server.stop()
    server.stop()

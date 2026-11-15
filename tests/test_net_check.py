# tests/test_net_check.py
"""Тесты сетевой автопроверки (v1.6.8): парсеры ping/arp, сводка check_device."""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from inventory.core.db import Database  # noqa: E402
from inventory.core.repository import InventoryRepository  # noqa: E402
from inventory.services.net_check import (  # noqa: E402
    arp_table,
    check_device,
    normalize_mac,
    ping_host,
    probe_port,
)


@dataclass
class _Proc:
    stdout: str
    stderr: str = ""


def _runner(ping_out: str = "", arp_out: str = ""):
    """Фейковый subprocess.run: по команде отдаёт заготовленный вывод."""
    def run(cmd, **kwargs):
        return _Proc(ping_out if cmd[0] == "ping" else arp_out)
    return run


_PING_RU_OK = "Ответ от 192.168.4.201: число байт=32 время=3мс TTL=64"
_PING_RU_FAIL = "Превышен интервал ожидания для запроса."


# ------------------------------------------------------------------ ping
def test_ping_success_russian_and_english() -> None:
    ok, latency = ping_host("192.168.4.201", runner=_runner(_PING_RU_OK))
    assert ok and latency == 3
    ok, latency = ping_host(
        "192.168.4.201",
        runner=_runner("Reply from 192.168.4.201: bytes=32 time=12ms TTL=63"))
    assert ok and latency == 12


def test_ping_sub_millisecond_and_failures() -> None:
    ok, latency = ping_host(
        "192.168.4.201",
        runner=_runner("Reply from 192.168.4.201: bytes=32 time<1ms TTL=64"))
    assert ok and latency is None
    assert not ping_host("192.168.4.201", runner=_runner(_PING_RU_FAIL))[0]
    assert not ping_host(
        "192.168.4.201", runner=_runner("Request timed out."))[0]


# ------------------------------------------------------------------- arp
def test_arp_table_russian_output_and_normalization() -> None:
    out = (
        "\nИнтерфейс: 192.168.4.10 --- 0x8\n"
        "  адрес в Интернете     Физический адрес      Тип\n"
        "  192.168.4.1           cc-25-e8-31-22-33     динамический\n"
        "  192.168.4.201         A4-2B-b0-d1-E2-f3     динамический\n"
        "  224.0.0.22            01-00-5e-00-00-16     статический\n"
        "  fe80::1%12            a4-2b-b0-d1-e2-f3     постоянный\n"
    )
    table = arp_table(runner=_runner(arp_out=out))
    assert table["192.168.4.1"] == "cc:25:e8:31:22:33"
    assert table["192.168.4.201"] == "a4:2b:b0:d1:e2:f3"
    assert table["224.0.0.22"] == "01:00:5e:00:00:16"
    assert "fe80::1%12" not in table


def test_normalize_mac_variants() -> None:
    assert normalize_mac("a4-2b-b0-d1-e2-f3") == "a4:2b:b0:d1:e2:f3"
    assert normalize_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("AABBCCDDEEFF") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("") == ""


# ----------------------------------------------------------- check_device
def test_check_no_ip_and_bad_ip() -> None:
    assert check_device(None, None, runner=_runner()).status == "no_ip"
    assert check_device("   ", None, runner=_runner()).status == "no_ip"
    assert check_device("999.1.2.3", None, runner=_runner()).status == "bad_ip"
    assert check_device("абв", None, runner=_runner()).status == "bad_ip"


def test_check_alive_with_mac_confirmed(monkeypatch) -> None:
    from inventory.services import net_check

    monkeypatch.setattr(net_check, "local_ipv4", lambda: None)  # без TCP-проб
    result = check_device(
        "192.168.4.201", "A4:2B:B0:D1:E2:F3", runner=_runner(_PING_RU_OK),
        arp={"192.168.4.201": "a4:2b:b0:d1:e2:f3"},
    )
    assert result.status == "alive"
    assert result.latency_ms == 3
    assert result.arp_mac == "a4:2b:b0:d1:e2:f3"
    assert "MAC подтверждён" in result.detail


def test_check_mac_mismatch_is_reported_but_device_alive(monkeypatch) -> None:
    from inventory.services import net_check

    monkeypatch.setattr(net_check, "local_ipv4", lambda: None)
    result = check_device(
        "192.168.4.201", "AA:BB:CC:DD:EE:01", runner=_runner(_PING_RU_OK),
        arp={"192.168.4.201": "a4:2b:b0:d1:e2:f3"},
    )
    assert result.status == "mac_mismatch"
    assert result.is_alive  # коробка жива, просто не та, что записана
    assert "≠" in result.detail


def test_check_alive_without_mac_data(monkeypatch) -> None:
    from inventory.services import net_check

    monkeypatch.setattr(net_check, "local_ipv4", lambda: None)
    result = check_device(
        "192.168.4.201", None, runner=_runner(_PING_RU_OK), arp={})
    assert result.status == "alive"
    assert "MAC не записан" in result.detail

    result = check_device(
        "192.168.4.201", "AA:BB:CC:DD:EE:01", runner=_runner(_PING_RU_OK),
        arp={},
    )
    assert result.status == "alive"
    assert "не обновился" in result.detail


def test_check_unreachable() -> None:
    result = check_device("192.168.4.201", None, runner=_runner(_PING_RU_FAIL))
    assert result.status == "unreachable"
    assert not result.is_alive


def test_check_ping_execution_failure() -> None:
    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 5)

    result = check_device("192.168.4.201", None, runner=boom)
    assert result.status == "unreachable"
    assert "ping не выполнился" in result.detail


def test_check_subnet_note(monkeypatch) -> None:
    from inventory.services import net_check

    monkeypatch.setattr(net_check, "local_ipv4", lambda: "10.0.0.5")
    result = net_check.check_device(
        "192.168.4.201", None, runner=_runner(_PING_RU_OK), arp={})
    assert result.detail.startswith("не в подсети ноутбука; ")
    same = net_check.check_device(
        "10.0.0.201", None, runner=_runner(_PING_RU_OK), arp={})
    assert "не в подсети" not in same.detail


# ------------------------------------------------------------------ порты
def test_probe_port_loopback_open_and_closed() -> None:
    """Реальный (но офлайн) тест: слушающий порт открыт, после close — нет."""
    import socket as socket_mod

    server = socket_mod.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    assert probe_port("127.0.0.1", port) is True
    server.close()
    assert probe_port("127.0.0.1", port) is False


def test_probe_port_refused_timeout_open(monkeypatch) -> None:
    """Отказ/таймаут → False; успешное соединение → True (фейковый connector)."""

    def refusing(addr, timeout=None):
        raise ConnectionRefusedError()

    def filtering(addr, timeout=None):
        raise TimeoutError()

    def opened(addr, timeout=None):
        import contextlib

        return contextlib.nullcontext()

    assert probe_port("192.168.4.201", 554, connector=refusing) is False
    assert probe_port("192.168.4.201", 80, connector=filtering) is False
    assert probe_port("192.168.4.201", 554, connector=opened) is True


def test_check_alive_probes_ports_in_same_subnet(monkeypatch) -> None:
    from inventory.services import net_check

    monkeypatch.setattr(net_check, "local_ipv4", lambda: "192.168.4.10")
    monkeypatch.setattr(
        net_check, "probe_service_ports",
        lambda ip: {554: True, 80: False},
    )
    result = check_device(
        "192.168.4.201", None, runner=_runner(_PING_RU_OK), arp={})
    assert result.ports == {554: True, 80: False}
    assert "RTSP 554: открыт" in result.detail
    assert "HTTP 80: не отвечает" in result.detail


def test_check_alive_skips_ports_other_subnet(monkeypatch) -> None:
    from inventory.services import net_check

    monkeypatch.setattr(net_check, "local_ipv4", lambda: "10.0.0.5")
    result = check_device(
        "192.168.4.201", None, runner=_runner(_PING_RU_OK), arp={})
    assert result.ports is None
    assert "порты не проверялись" in result.detail


def test_check_alive_without_local_net_skips_ports(monkeypatch) -> None:
    """Ноутбук без сети: local_ipv4 None — порты не пробуем, статья честная."""
    from inventory.services import net_check

    monkeypatch.setattr(net_check, "local_ipv4", lambda: None)
    result = check_device(
        "192.168.4.201", None, runner=_runner(_PING_RU_OK), arp={})
    assert result.ports is None
    assert "RTSP" not in result.detail


# ------------------------------------------------------------- UI-вязка
def test_card_net_button_reports_no_ip_offscreen(tmp_path: Path) -> None:
    """Кнопка «🌐 Сеть» в карточке: пустой IP → человеческий статус, кнопка вернулась."""
    app = QApplication.instance() or QApplication([])
    repo = InventoryRepository(Database(tmp_path / "card.db"))
    session = repo.create_session("Объект", "Инспектор")
    location = repo.create_location(session.id, "2", "204", "Корпус А", "")
    from inventory.ui.device_card import DeviceCardDialog

    dialog = DeviceCardDialog(
        repo, session.id,
        {"photos": tmp_path / "photos", "logs": tmp_path / "logs"},
        location=location,
    )
    dialog.net_btn.click()
    worker = dialog._net_worker
    assert worker is not None
    worker.wait(2000)
    for _ in range(100):
        app.processEvents()
        if dialog._net_worker is None:
            break
        time.sleep(0.02)
    assert "IP не записан" in dialog.net_status.text()
    assert dialog.net_btn.isEnabled()

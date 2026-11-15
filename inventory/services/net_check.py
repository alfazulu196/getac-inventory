# inventory/services/net_check.py
"""Сетевая автопроверка устройства (v1.6.8): ping + сверка MAC по ARP.

Офлайн, ноль зависимостей: `ping`/`arp` Windows запускаются subprocess'ом,
вывод разбирается по неизменяемым маркерам (`TTL=`, пары hex в MAC) —
русский или английский язык консоли не важен.

Смысл: в поле устройство записано с IP и MAC. Кнопка «🌐 Сеть» отвечает
на два вопроса ДИФФЕРЕНЦИРОВАННО (скилл network-device-audit):
1) живо ли оно вообще (ping);
2) то ли это устройство отвечает (MAC в ARP-таблице == записанному) —
   ловит «IP переехал на другую коробку» и IP-конфликты.

Причины отказа всегда человеческие: нет IP / кривой IP / не отвечает /
MAC не совпал. MAC-сверка мягкая: ARP мог не обновиться — это не провал.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
from dataclasses import dataclass
from ipaddress import IPv4Address

PING_TIMEOUT_MS = 1200

# Успех ping: маркер TTL не зависит от языка консоли.
_PING_OK_RE = re.compile(r"TTL[=:]\s*\d+", re.IGNORECASE)
# «время=3мс» / «time=12ms» / «time<1ms»: группа 1 — знак, группа 2 — число;
# знак «<» означает суб-миллисекунду — честного числа нет.
_LATENCY_RE = re.compile(
    r"(?:время|time)\s*([=<])\s*(\d+)\s*(?:мс|ms)", re.IGNORECASE)
# Строка ARP-таблицы: IPv4 + MAC (дефисы или двоеточия, любые регистры).
_ARP_LINE_RE = re.compile(
    r"(\d{1,3}(?:\.\d{1,3}){3})\s+((?:[0-9A-Fa-f]{1,2}[-:]){5}[0-9A-Fa-f]{1,2})"
)


@dataclass
class NetCheckResult:
    """Итог проверки: status — machine-код, detail — текст для человека."""

    status: str  # alive | mac_mismatch | unreachable | no_ip | bad_ip
    detail: str
    latency_ms: int | None = None
    arp_mac: str | None = None
    # TCP-пробы служб (v1.6.10) — только когда коробка в своей подсети;
    # None = пробы не делались (другая подсеть / не доходили).
    ports: dict[int, bool] | None = None

    @property
    def is_alive(self) -> bool:
        return self.status in ("alive", "mac_mismatch")


def normalize_mac(raw: str) -> str:
    """«a4-2b-b0-d1-e2-f3» / «A4:2B:…» → «a4:2b:…» (пары до 2 цифр паддинг)."""
    pairs = re.findall(r"[0-9A-Fa-f]{1,2}", raw or "")
    return ":".join(f"{int(pair, 16):02x}" for pair in pairs[-6:]) \
        if pairs else ""


def _creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _oem_encoding() -> str:
    """Кодировка консольных утилит Windows (ping/arp): OEM, не UTF-8!

    Приложение запускается с -X utf8: subprocess text=True тогда декодирует
    вывод ping как UTF-8 и падает на кириллице (байт 0x8E из cp866).
    Берём кодовую страницу консоли напрямую (GetOEMCP).
    """
    try:
        import ctypes

        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except Exception:  # noqa: BLE001 — не Windows / нет ctypes
        return "utf-8"


_OEM = _oem_encoding()


def _decode_output(raw: bytes | str | None) -> str:
    """Вывод ping/arp → str. Маркеры-ASCII выживают при любой кодировке."""
    if raw is None:
        return ""
    if isinstance(raw, str):  # фейковый runner в тестах отдаёт str
        return raw
    return raw.decode(_OEM, errors="replace")


def ping_host(
    ip: str,
    timeout_ms: int = PING_TIMEOUT_MS,
    runner=subprocess.run,
) -> tuple[bool, int | None]:
    """Один ping: (отвечает, латентность мс или None если «<1 мс»)."""
    proc = runner(
        ["ping", "-n", "1", "-w", str(timeout_ms), ip],
        capture_output=True,
        timeout=timeout_ms / 1000 + 4,
        creationflags=_creation_flags(),
    )
    out = f"{_decode_output(proc.stdout)}\n{_decode_output(proc.stderr)}"
    if not _PING_OK_RE.search(out):
        return False, None
    match = _LATENCY_RE.search(out)
    if match is None or match.group(1) == "<":
        return True, None  # «<1 мс» — честной цифры нет
    return True, int(match.group(2))


def arp_table(runner=subprocess.run) -> dict[str, str]:
    """`arp -a` → {IP: нормализованный MAC}; язык консоли не важен."""
    proc = runner(
        ["arp", "-a"],
        capture_output=True, timeout=10,
        creationflags=_creation_flags(),
    )
    table: dict[str, str] = {}
    for line in _decode_output(proc.stdout).splitlines():
        match = _ARP_LINE_RE.search(line)
        if match:
            table[match.group(1)] = normalize_mac(match.group(2))
    return table


def local_ipv4() -> str | None:
    """Основной локальный IP (UDP-«подключение» пакетов не отправляет)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


# Службы, по которым поле судит «жива коробка или отдаёт работу»:
# RTSP — видеопоток камер/регистраторов, HTTP — веб-интерфейс.
DEFAULT_SERVICE_PORTS = {554: "RTSP", 80: "HTTP"}
PORT_TIMEOUT_S = 0.8


def probe_port(
    ip: str,
    port: int,
    timeout_s: float = PORT_TIMEOUT_S,
    connector=socket.create_connection,
) -> bool:
    """TCP-проба: True — порт принимает соединение (служба жива).

    Закрытый порт на живом хосте отказывает сразу (RST) — быстро;
    таймаут — только если порт фильтруется брандмауэром.
    """
    try:
        with connector((ip, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def probe_service_ports(
    ip: str,
    ports: dict[int, str] | None = None,
    connector=socket.create_connection,
) -> dict[int, bool]:
    """Пачка TCP-проб {порт: открыт?} для устройства."""
    check_ports = ports or DEFAULT_SERVICE_PORTS
    return {
        port: probe_port(ip, port, connector=connector)
        for port in check_ports
    }


def ports_summary(ports: dict[int, bool]) -> str:
    parts = []
    for port, is_open in sorted(ports.items()):
        name = DEFAULT_SERVICE_PORTS.get(port, f"порт {port}")
        parts.append(f"{name} {port}: {'открыт' if is_open else 'не отвечает'}")
    return "; ".join(parts)


def check_device(
    ip: str | None,
    expected_mac: str | None,
    runner=subprocess.run,
    arp: dict[str, str] | None = None,
) -> NetCheckResult:
    """Полная проверка: ping → ARP → сверка MAC.

    runner/arp — инъекции для тестов (реальный вызов их не передаёт).
    """
    if not (ip or "").strip():
        return NetCheckResult(
            "no_ip", "IP не записан — заполните поле IP-адрес и сохраните")
    try:
        addr = IPv4Address(ip.strip())
    except ValueError:
        return NetCheckResult("bad_ip", f"«{ip.strip()}» не похож на IPv4")
    ip_s = str(addr)

    # Оговорка о подсети: в поле ноутбук и оборудование обычно в одном /24.
    # TCP-пробы портов честны только в своей подсети — через маршрутизатор
    # может резаться всё подряд.
    note = ""
    local = local_ipv4()
    same_subnet = (
        local is not None
        and ip_s.rsplit(".", 1)[0] == local.rsplit(".", 1)[0]
    )
    if local and not same_subnet:
        note = "не в подсети ноутбука; "

    try:
        alive, latency = ping_host(ip_s, runner=runner)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return NetCheckResult(
            "unreachable", f"{note}ping не выполнился ({exc.__class__.__name__})")
    if not alive:
        return NetCheckResult(
            "unreachable",
            f"{note}не отвечает на ping: выключено, в другой сети "
            "или брандмауэр режет ICMP")

    if arp is None:
        try:
            arp = arp_table(runner=runner)
        except (subprocess.TimeoutExpired, OSError):
            arp = {}
    arp_mac = arp.get(ip_s)

    expected = normalize_mac(expected_mac or "")
    if expected and arp_mac and arp_mac != expected:
        return NetCheckResult(
            "mac_mismatch",
            f"отвечает за {latency or '<1'} мс, НО MAC в ARP {arp_mac} ≠ "
            f"записанному {expected} — IP занят другим устройством или конфликт",
            latency, arp_mac)

    if expected and arp_mac:
        detail = f"{note}в сети за {latency or '<1'} мс, MAC подтверждён"
    elif expected:
        detail = (f"{note}в сети за {latency or '<1'} мс; MAC в ARP ещё "
                  "не обновился — сверка отложена до следующей проверки")
    else:
        detail = f"{note}в сети за {latency or '<1'} мс (MAC не записан — сверять нечем)"

    # TCP-пробы служб (v1.6.10): камера «жива», но поток не отдаёт — видно сразу.
    ports_map: dict[int, bool] | None = None
    if same_subnet:
        try:
            ports_map = probe_service_ports(ip_s)
            detail += f"; {ports_summary(ports_map)}"
        except OSError:
            detail += "; TCP-пробы служб не выполнились"
    elif note:
        detail += "; порты не проверялись (другая подсеть)"
    return NetCheckResult("alive", detail, latency, arp_mac, ports_map)

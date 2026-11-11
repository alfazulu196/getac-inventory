# inventory/services/scanner.py
"""Сканер штрихкодов: COM-порт, режим клавиатуры, лазер."""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

import serial
import serial.tools.list_ports
from PyQt6.QtCore import QThread, pyqtSignal

from inventory.config import ScannerConfig
from inventory.core.parser import ParsedCode, parse_code


@dataclass
class SerialPortInfo:
    device: str
    description: str


def list_serial_ports() -> list[SerialPortInfo]:
    return [
        SerialPortInfo(port.device, port.description or "")
        for port in serial.tools.list_ports.comports()
    ]


class ComScanner:
    """Низкоуровневое чтение COM-порта. Без Qt — удобно тестировать."""

    def __init__(self, config: ScannerConfig):
        self.config = config
        self.ser: serial.Serial | None = None

    @property
    def is_open(self) -> bool:
        return bool(self.ser and self.ser.is_open)

    def open(self) -> None:
        if self.is_open:
            return
        self.ser = serial.Serial(
            port=self.config.port,
            baudrate=self.config.baudrate,
            bytesize=self.config.bytesize,
            parity=self.config.parity,
            stopbits=self.config.stopbits,
            timeout=self.config.timeout,
        )

    def close(self) -> None:
        if self.ser:
            try:
                self.ser.close()
            except serial.SerialException:
                pass
            self.ser = None

    def _clean(self, text: str) -> str:
        text = text.strip("\r\n\x00 ")
        if self.config.strip_prefix and text.startswith(self.config.strip_prefix):
            text = text[len(self.config.strip_prefix) :]
        if self.config.strip_suffix and text.endswith(self.config.strip_suffix):
            text = text[: -len(self.config.strip_suffix)]
        return text.strip("\r\n\x00 ")

    def read_code(self) -> str | None:
        if not self.is_open:
            return None
        try:
            line = self.ser.readline()  # type: ignore[union-attr]
        except (serial.SerialException, OSError):
            self.close()
            raise
        if not line:
            return None
        text = line.decode(self.config.encoding, errors="ignore")
        text = self._clean(text)
        return text or None

    def test_connection(self) -> bool:
        try:
            self.open()
            self.close()
            return True
        except (serial.SerialException, OSError):
            self.close()
            return False


class KeyboardScannerBuffer:
    """Собирает быстрые нажатия HID-сканера в одну строку."""

    def __init__(
        self,
        max_interval: float = 0.08,
        min_length: int = 3,
        strip_prefix: str = "",
        strip_suffix: str = "",
    ):
        self.max_interval = max_interval
        self.min_length = min_length
        self.strip_prefix = strip_prefix
        self.strip_suffix = strip_suffix
        self._buffer: list[str] = []
        self._last_time: float | None = None

    def reset(self) -> None:
        self._buffer.clear()
        self._last_time = None

    def feed(self, text: str, timestamp: float | None = None) -> str | None:
        if not text:
            return None
        now = timestamp if timestamp is not None else time.monotonic()
        if text in ("\n", "\r"):
            code = "".join(self._buffer).strip()
            self.reset()
            code = self._clean(code)
            return code if len(code) >= self.min_length else None
        if self._last_time is not None and now - self._last_time > self.max_interval:
            self.reset()
        self._buffer.append(text)
        self._last_time = now
        return None

    def _clean(self, text: str) -> str:
        if self.strip_prefix and text.startswith(self.strip_prefix):
            text = text[len(self.strip_prefix) :]
        if self.strip_suffix and text.endswith(self.strip_suffix):
            text = text[: -len(self.strip_suffix)]
        return text.strip()


class ScannerThread(QThread):
    """Фоновое чтение COM-порта с автопереподключением."""

    code_scanned = pyqtSignal(str)
    status_changed = pyqtSignal(str, bool)  # сообщение, подключено
    error_occurred = pyqtSignal(str)

    def __init__(self, config: ScannerConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.scanner = ComScanner(config)
        self._stop_event = threading.Event()
        self._last_code: str | None = None
        self._last_time = 0.0

    def run(self) -> None:
        if self.config.mode != "com":
            self.status_changed.emit(
                "Сканер в режиме HID/ручного ввода — COM-порт не используется", True
            )
            return

        while not self._stop_event.is_set():
            try:
                self.scanner.open()
                self.status_changed.emit(
                    f"Сканер подключён: {self.config.port}", True
                )
                while not self._stop_event.is_set():
                    code = self.scanner.read_code()
                    if not code:
                        continue
                    now = time.monotonic()
                    if (
                        code == self._last_code
                        and now - self._last_time < self.config.dedup_seconds
                    ):
                        continue
                    self._last_code = code
                    self._last_time = now
                    self.code_scanned.emit(code)
            except Exception as exc:  # noqa: BLE001 — поток не должен падать
                self.scanner.close()
                self.status_changed.emit(f"Сканер недоступен: {exc}", False)
                self.error_occurred.emit(str(exc))
                self._stop_event.wait(2.0)

        self.scanner.close()
        self.status_changed.emit("Сканер остановлен", False)

    def stop(self) -> None:
        self._stop_event.set()
        self.scanner.close()
        self.wait(3000)


def parse_scan(raw: str) -> ParsedCode:
    return parse_code(raw)


_KEY_CODES = {
    "F1": 0x70,
    "F2": 0x71,
    "F3": 0x72,
    "F4": 0x73,
    "F5": 0x74,
    "F6": 0x75,
    "F7": 0x76,
    "F8": 0x77,
    "F9": 0x78,
    "F10": 0x79,
    "F11": 0x7A,
    "F12": 0x7B,
    "ENTER": 0x0D,
    "TAB": 0x09,
}

# --- Программный триггер лазера через вендорский HID-интерфейс сканера -----
# Встроенный сканер Getac V110 (Zebra/Symbol) имеет второй HID-интерфейс
# VID_2BAF&PID_0004, сборка «vendor control»: UsagePage 0xFF89, Usage 0x10.
# MTC-сервис Getac при нажатии физической кнопки шлёт туда FeatureReport
# [0x5A, 0x23, 0x55] («TRIG_Lo») — модуль опускает линию триггера, как при
# нажатии кнопки: лазер зажигается, этикетка читается, код приходит в HID-
# клавиатуру. Воспроизводим этот же вызов напрямую (без сервиса).

_HID_GUID: "ctypes.Structure | None" = None
_HID_LIBS: "tuple | None" = None

# строки FeatureReport из MTCService.exe (IL ExactBytes):
#   EnableCustomUSBDevice_BarCode (триггер кнопки, TRIG_Lo):
#     [0]=0x5A [1]=0x06 [2]=0x23 [3]=0x01 [4]=0x08 [5]=0x01 [6]=0x55, [7..16]=0
#   InitialCustomUSBDev_BarCode (инициализация при старте сервиса):
#     [0]=0x5A [1]=0x07 [2]=0x03 [3]=0x16 [4]=0xF0 [5]=0x01, [6..16]=0
_BARCODE_VID = 0x2BAF
_BARCODE_USAGE_PAGE = 0xFF89
_BARCODE_USAGE = 0x10
_TRIG_LO = (0x5A, 0x06, 0x23, 0x01, 0x08, 0x01, 0x55)


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("InterfaceClassGuid", _GUID),
        ("Flags", ctypes.c_ulong),
        ("Reserved", ctypes.c_size_t),
    ]


class _HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", ctypes.c_ushort),
        ("UsagePage", ctypes.c_ushort),
        ("InputReportByteLength", ctypes.c_ushort),
        ("OutputReportByteLength", ctypes.c_ushort),
        ("FeatureReportByteLength", ctypes.c_ushort),
        ("Reserved", ctypes.c_ushort * 17),
        ("NumberLinkCollectionNodes", ctypes.c_ushort),
        ("NumberInputButtonCaps", ctypes.c_ushort),
        ("NumberInputValueCaps", ctypes.c_ushort),
        ("NumberInputDataIndices", ctypes.c_ushort),
        ("NumberOutputButtonCaps", ctypes.c_ushort),
        ("NumberOutputValueCaps", ctypes.c_ushort),
        ("NumberOutputDataIndices", ctypes.c_ushort),
        ("NumberFeatureButtonCaps", ctypes.c_ushort),
        ("NumberFeatureValueCaps", ctypes.c_ushort),
        ("NumberFeatureDataIndices", ctypes.c_ushort),
    ]


def _hid_setup_prototypes(hid, setupapi, kernel32) -> None:
    """Прототипы WinAPI для ctypes. Критично: HANDLE — c_void_p (64-бит).

    Без явного restype ctypes возвращает HANDLE как 32-битный int: на x64
    верхние биты реального указателя теряются, API получает порченый
    дескриптор и SetupDiEnumDeviceInterfaces сразу фейлится (ошибка 6).
    """
    from ctypes import wintypes

    hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(_GUID)]
    hid.HidD_GetHidGuid.restype = None

    setupapi.SetupDiGetClassDevsW.argtypes = [
        ctypes.POINTER(_GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD
    ]
    setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p

    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(_GUID),
        wintypes.DWORD,
        ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
    ]
    setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL

    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL

    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = wintypes.BOOL

    hid.HidD_GetPreparsedData.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
    ]
    hid.HidD_GetPreparsedData.restype = wintypes.BOOL
    hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
    hid.HidD_FreePreparsedData.restype = wintypes.BOOL
    hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(_HIDP_CAPS)]
    hid.HidP_GetCaps.restype = ctypes.c_long
    hid.HidD_SetFeature.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG
    ]
    hid.HidD_SetFeature.restype = wintypes.BOOL


def _hid_libs():
    """Загружает hid/setupapi/kernel32 (Windows); None — если не win32/нет DLL.

    Инстансы и их прототипы кэшируются: argtypes привязаны к объектам
    функций конкретного WinDLL-инстанса, при пересоздании они бы потерялись.
    """
    global _HID_GUID, _HID_LIBS
    if _HID_LIBS is not None:
        return _HID_LIBS
    try:
        hid = ctypes.WinDLL("hid.dll", use_last_error=True)
        setupapi = ctypes.WinDLL("setupapi.dll", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    except OSError:
        return None
    _hid_setup_prototypes(hid, setupapi, kernel32)
    guid = _GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    _HID_GUID = guid
    _HID_LIBS = (hid, setupapi, kernel32)
    return _HID_LIBS


def _hid_find_control_path() -> str | None:
    r"""Путь интерфейса сканера \\?\hid#vid_2baf...col01#..."""
    libs = _hid_libs()
    if libs is None:
        return None
    hid, setupapi, kernel32 = libs
    guid = _HID_GUID
    if guid is None:
        return None

    DIGCF_PRESENT = 0x02
    DIGCF_DEVICEINTERFACE = 0x10
    INVALID_HANDLE = ctypes.c_void_p(-1).value

    handle = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if not handle or handle == INVALID_HANDLE:
        return None

    try:
        index = 0
        iface = _SP_DEVICE_INTERFACE_DATA()
        iface.cbSize = ctypes.sizeof(iface)
        candidate: str | None = None
        while setupapi.SetupDiEnumDeviceInterfaces(
            handle, None, ctypes.byref(guid), index, ctypes.byref(iface)
        ):
            index += 1
            needed = ctypes.c_ulong(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                handle,
                ctypes.byref(iface),
                None,
                0,
                ctypes.byref(needed),
                None,
            )
            if needed.value <= 4:
                continue
            buf = ctypes.create_string_buffer(needed.value)
            # x64: cbSize = 8 (DWORD cbSize + первый WCHAR пути)
            ctypes.memset(buf, 0, needed.value)
            ctypes.cast(buf, ctypes.POINTER(ctypes.c_ulong))[0] = 8
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                handle,
                ctypes.byref(iface),
                buf,
                needed.value,
                ctypes.byref(needed),
                None,
            ):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            low = path.lower()
            if f"vid_{_BARCODE_VID:04x}" in low:
                candidate = path
                # col01 — вендорский управляющий интерфейс; берём его сразу
                if "col01" in low:
                    break
        return candidate
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(handle)


def hid_trigger_laser() -> bool:
    """Программный триггер: FeatureReport TRIG_Lo в вендорский HID сканера.

    Тот же вызов, что делает MTC-сервис Getac при нажатии физической кнопки:
    модуль зажигает лазер, читает этикетку, код приходит в HID-клавиатуру.
    True — команда принята устройством.
    """
    if sys.platform != "win32":
        return False
    libs = _hid_libs()
    if libs is None:
        return False
    hid, _setupapi, kernel32 = libs
    try:
        path = _hid_find_control_path()
        if not path:
            return False
        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        INVALID_HANDLE = ctypes.c_void_p(-1).value
        handle = kernel32.CreateFileW(
            path,
            GENERIC_READ | GENERIC_WRITE,
            0x3,  # FILE_SHARE_READ | FILE_SHARE_WRITE
            None,
            3,  # OPEN_EXISTING
            0,
            None,
        )
        if not handle or handle == INVALID_HANDLE:
            return False
        try:
            preparsed = ctypes.c_void_p(0)
            if not hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)):
                return False
            try:
                caps = _HIDP_CAPS()
                if hid.HidP_GetCaps(preparsed, ctypes.byref(caps)) <= 0:
                    return False
                if (
                    caps.UsagePage != _BARCODE_USAGE_PAGE
                    or caps.Usage != _BARCODE_USAGE
                ):
                    return False  # вендорский интерфейс не совпал — не наш путь
                report_len = max(len(_TRIG_LO), caps.FeatureReportByteLength)
                report = (ctypes.c_ubyte * report_len)()
                for i, value in enumerate(_TRIG_LO):
                    report[i] = value
                return bool(
                    hid.HidD_SetFeature(handle, ctypes.byref(report), report_len)
                )
            finally:
                hid.HidD_FreePreparsedData(preparsed)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 — любой сбой должен уйти в fallback
        return False


def trigger_laser(keys: list[str], delay: float = 0.05) -> bool:
    """Поджигает лазер: сначала HID-триггер Getac, затем клавиатурные F-клавиши.

    HID-путь — это тот же FeatureReport TRIG_Lo, который MTC-сервис шлёт при
    нажатии физической кнопки триггера; работает и без Getac Utility.
    F-клавиши оставлены как запасной путь для сканеров с привязкой кнопки.
    """
    if hid_trigger_laser():
        return True
    if sys.platform != "win32":
        return False
    try:
        for index, key in enumerate(keys):
            code = _KEY_CODES.get(key.upper())
            if code is None:
                continue
            ctypes.windll.user32.keybd_event(code, 0, 0, 0)
            ctypes.windll.user32.keybd_event(code, 0, 2, 0)
            if index < len(keys) - 1:
                time.sleep(delay)
        return True
    except Exception:  # noqa: BLE001 — на Getac может не быть прав
        return False


def feed_keyboard_scan(buffer: KeyboardScannerBuffer, text: str) -> str | None:
    """Удобная обёртка для UI: вернуть готовый код, когда пришёл терминатор."""
    return buffer.feed(text)

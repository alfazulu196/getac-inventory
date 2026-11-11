# tests/test_scanner.py
from inventory.services.scanner import KeyboardScannerBuffer, parse_scan


def test_keyboard_buffer_collects_fast_scan():
    buffer = KeyboardScannerBuffer(max_interval=0.2)
    assert buffer.feed("A", timestamp=0.0) is None
    assert buffer.feed("B", timestamp=0.01) is None
    assert buffer.feed("C", timestamp=0.02) is None
    assert buffer.feed("1", timestamp=0.03) is None
    assert buffer.feed("2", timestamp=0.04) is None
    assert buffer.feed("\n", timestamp=0.05) == "ABC12"


def test_keyboard_buffer_resets_on_slow_typing():
    buffer = KeyboardScannerBuffer(max_interval=0.05)
    assert buffer.feed("A", timestamp=0.0) is None
    assert buffer.feed("B", timestamp=1.0) is None
    assert buffer.feed("C", timestamp=1.01) is None
    assert buffer.feed("D", timestamp=1.02) is None
    assert buffer.feed("\n", timestamp=1.03) == "BCD"


def test_keyboard_buffer_ignores_short_code():
    buffer = KeyboardScannerBuffer(min_length=4)
    buffer.feed("A", timestamp=0.0)
    buffer.feed("B", timestamp=0.01)
    assert buffer.feed("\n", timestamp=0.02) is None


def test_parse_scan_wrapper():
    parsed = parse_scan("AA:BB:CC:DD:EE:FF")
    assert parsed.mac == "AA:BB:CC:DD:EE:FF"

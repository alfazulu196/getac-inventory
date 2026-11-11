# tests/test_parser.py
from inventory.core.parser import (
    CodeType,
    is_valid_ean,
    normalize_mac,
    normalize_mac_compact,
    normalize_serial,
    parse_code,
)


def test_mac_with_colons():
    parsed = parse_code("78:83:C4:5A:6B:7C")
    assert parsed.code_type == CodeType.MAC
    assert parsed.fields["mac"] == "78:83:C4:5A:6B:7C"
    assert parsed.confidence >= 0.9


def test_mac_with_dashes_and_dots():
    assert normalize_mac("78-83-C4-5A-6B-7C") == "78:83:C4:5A:6B:7C"
    assert normalize_mac("7883.C45A.6B7C") == "78:83:C4:5A:6B:7C"
    assert normalize_mac_compact("78:83:C4:5A:6B:7C") == "7883C45A6B7C"


def test_mac_plain_12_hex_is_ambiguous():
    parsed = parse_code("7883C45A6B7C")
    assert parsed.code_type == CodeType.MAC
    assert "серийным" in parsed.note
    assert parsed.confidence < 0.9


def test_valid_ean13():
    assert is_valid_ean("4006381333931")
    assert not is_valid_ean("4006381333932")


def test_ean_goes_to_goods_code_not_serial():
    parsed = parse_code("4006381333931")
    assert parsed.code_type == CodeType.EAN
    assert parsed.ean == "4006381333931"
    assert parsed.serial is None


def test_serial_hikvision():
    parsed = parse_code("DS-2CD2143G0-I12345678")
    assert parsed.code_type == CodeType.SERIAL
    assert parsed.fields["serial"] == "DS-2CD2143G0-I12345678"


def test_ip():
    parsed = parse_code("192.168.10.51")
    assert parsed.code_type == CodeType.IP
    assert parsed.fields["ip"] == "192.168.10.51"


def test_structured_pairs():
    parsed = parse_code("SN=ABC12345;MAC=AA:BB:CC:DD:EE:FF;IP=192.168.1.10;MODEL=DS-2CD")
    assert parsed.code_type == CodeType.QR
    assert parsed.fields["serial"] == "ABC12345"
    assert parsed.fields["mac"] == "AA:BB:CC:DD:EE:FF"
    assert parsed.fields["ip"] == "192.168.1.10"
    assert parsed.fields["model"] == "DS-2CD"


def test_structured_json():
    parsed = parse_code('{"sn": "ABC12345", "mac": "AA:BB:CC:DD:EE:FF", "model": "IPC-HDW"}')
    assert parsed.code_type == CodeType.QR
    assert parsed.fields["serial"] == "ABC12345"
    assert parsed.fields["mac"] == "AA:BB:CC:DD:EE:FF"
    assert parsed.fields["model"] == "IPC-HDW"


def test_structured_russian_pairs():
    parsed = parse_code(
        "СН: ABC12345; МАК: AA:BB:CC:DD:EE:FF; ИП: 192.168.1.10; "
        "МОДЕЛЬ: DS-2CD; ПРОИЗВОДИТЕЛЬ: Hikvision; ИНВ: INV-001"
    )
    assert parsed.code_type == CodeType.QR
    assert parsed.fields["serial"] == "ABC12345"
    assert parsed.fields["mac"] == "AA:BB:CC:DD:EE:FF"
    assert parsed.fields["ip"] == "192.168.1.10"
    assert parsed.fields["model"] == "DS-2CD"
    assert parsed.fields["manufacturer"] == "Hikvision"
    assert parsed.fields["asset_tag"] == "INV-001"


def test_structured_url_query():
    parsed = parse_code("https://example.local/device?sn=ABC12345&mac=AA-BB-CC-DD-EE-FF")
    assert parsed.code_type == CodeType.QR
    assert parsed.fields["serial"] == "ABC12345"
    assert parsed.fields["mac"] == "AA:BB:CC:DD:EE:FF"


def test_unknown_code():
    parsed = parse_code("HELLO")
    assert parsed.code_type == CodeType.UNKNOWN
    assert parsed.note


def test_normalize_serial():
    assert normalize_serial(" ab 12-34 ") == "AB12-34"


def test_empty_code():
    parsed = parse_code("   ")
    assert parsed.code_type == CodeType.UNKNOWN
    assert parsed.note == "Пустой код"

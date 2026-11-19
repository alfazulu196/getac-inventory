# tests/test_tia_labels.py
"""Общая логика TIA-606: метка локации, QR-префикс, поиск по скану."""
from __future__ import annotations

from types import SimpleNamespace

from inventory.services.tia_labels import (
    QR_PREFIX,
    generate,
    match_locations,
    natural_key,
    qr_payload,
    rack_label_for_location,
    room_segment,
    LabelSpec,
)


def _loc(building="Корпус А", floor="1", room="101", rack="Ш-1"):
    return SimpleNamespace(building=building, floor=floor, room=room,
                           rack=rack)


def test_room_segment_matches_netbox_name():
    assert room_segment("Корпус А", "1", "101") == "Корпус А - 1 - 101"
    assert room_segment("", "", "") == "Без помещения"


def test_rack_label_for_location():
    label = rack_label_for_location(_loc(), "Демо-объект (репетиция)")
    assert label == "DEMOOBEK-KORPUSA1101-SH1"
    assert rack_label_for_location(_loc(), "Демо", prefix=True) == (
        "TIA=DEMO-KORPUSA1101-SH1")


def test_rack_label_without_rack_or_site_is_none():
    assert rack_label_for_location(_loc(rack=""), "Демо") is None
    assert rack_label_for_location(_loc(), "") is None


def test_match_locations_by_label():
    locations = [
        _loc(room="101", rack="Ш-1"),
        _loc(room="102", rack="Ш-2"),
    ]
    found = match_locations(locations, "demo-korpusa1101-sh1", "Демо")
    assert found == [locations[0]]
    # QR несёт префикс TIA= — поиск должен его принимать.
    found = match_locations(locations, qr_payload("DEMO-KORPUSA1102-SH2"),
                            "Демо")
    assert found == [locations[1]]
    assert match_locations(locations, "", "Демо") == []


def test_match_locations_returns_all_duplicates():
    locations = [_loc(), _loc()]
    assert len(match_locations(locations, "DEMO-KORPUSA1101-SH1", "Демо")) == 2


def test_qr_payload_is_explicit():
    assert QR_PREFIX == "TIA="
    assert qr_payload(" demo-1-r1 ") == "TIA=DEMO-1-R1"


def test_generate_port_label():
    spec = LabelSpec("Демо", "Серверная", "Ш-4", "A1", "P01")
    assert generate(spec) == "DEMO-SERVERNAYA-SH4-A1-P01"


def test_natural_key_orders_numbers_and_mixed_fields():
    names = ["Ш-10", "Ш-2", "1", "A"]
    assert sorted(names, key=natural_key) == ["1", "A", "Ш-2", "Ш-10"]

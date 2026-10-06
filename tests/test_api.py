"""Unit tests for static GTFS parsing and the TFNSW extension decoder."""

from __future__ import annotations

from google.transit import gtfs_realtime_pb2

from custom_components.adelaide_metro.api import (
    _decode_tfnsw_vehicle_descriptor,
    _parse_tfnsw_vehicle_descriptor,
    parse_static_gtfs,
)

from .conftest import build_static_zip


def test_parse_static_gtfs() -> None:
    static = parse_static_gtfs(build_static_zip())
    assert static.stops["16490"].stop_name == "Seaford Meadows Railway Station"
    assert static.routes["SEAFRD"].route_long_name == "Seaford to City"
    assert static.direction_headsigns == {("SEAFRD", "0"): "City", ("SEAFRD", "1"): "Seaford"}
    assert static.route_stops == {"SEAFRD": {"16490", "16491"}}
    assert static.stop_directions_raw["16491"] == {("SEAFRD", "0"), ("SEAFRD", "1")}
    assert static.stop_directions["16491"] == ("SEAFRD", "0")


def test_decode_extension() -> None:
    # field 1999, length-delimited: air_conditioned=0, wheelchair_accessible=1
    payload = bytes([0x08, 0x00, 0x10, 0x01])
    raw = bytes([0xFA, 0x7C, len(payload)]) + payload
    assert _decode_tfnsw_vehicle_descriptor(raw) == (False, 1)


def test_decode_extension_absent() -> None:
    desc = gtfs_realtime_pb2.VehicleDescriptor(id="3020", label="3020")
    assert _parse_tfnsw_vehicle_descriptor(desc) == (False, 0)


def test_decode_truncated_extension_falls_back() -> None:
    class Truncated:
        def SerializeToString(self) -> bytes:
            return bytes([0xFA, 0x7C, 0x04, 0x08])  # says 4 bytes, has 1

    assert _parse_tfnsw_vehicle_descriptor(Truncated()) == (False, 0)

from __future__ import annotations

import csv
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO

from google.transit import gtfs_realtime_pb2
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    SERVICE_ALERTS_URL,
    STATIC_GTFS_URL,
    TRIP_UPDATES_URL,
    VEHICLE_POSITIONS_URL,
)


def _parse_tfnsw_vehicle_descriptor(vehicle_desc_msg) -> tuple[bool, int]:
    """Parse TFNSW extension (field 1999) from serialized VehicleDescriptor bytes.

    Adelaide Metro uses a custom protobuf extension on VehicleDescriptor
    (extension id 1999, namespace transit_realtime.tfnsw_vehicle_descriptor)
    with fields air_conditioned (bool, default true) and
    wheelchair_accessible (int32, 0 or 1, default 0).

    Returns (air_conditioned, wheelchair_accessible) or (False, 0) if absent.
    """
    raw = vehicle_desc_msg.SerializeToString()
    pos = 0
    while pos < len(raw):
        tag = 0
        shift = 0
        while True:
            b = raw[pos]
            pos += 1
            tag |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                break
        field_num = tag >> 3
        wire_type = tag & 0x7
        if wire_type == 2:  # length-delimited
            length = 0
            shift = 0
            while True:
                b = raw[pos]
                pos += 1
                length |= (b & 0x7F) << shift
                shift += 7
                if not (b & 0x80):
                    break
            if field_num == 1999:
                sub = raw[pos : pos + length]
                air_conditioned = True
                wheelchair_accessible = 0
                sp = 0
                while sp < len(sub):
                    stag = 0
                    sshift = 0
                    while True:
                        sb = sub[sp]
                        sp += 1
                        stag |= (sb & 0x7F) << sshift
                        sshift += 7
                        if not (sb & 0x80):
                            break
                    sfn = stag >> 3
                    sval = 0
                    sshift = 0
                    while True:
                        sb = sub[sp]
                        sp += 1
                        sval |= (sb & 0x7F) << sshift
                        sshift += 7
                        if not (sb & 0x80):
                            break
                    if sfn == 1:
                        air_conditioned = bool(sval)
                    elif sfn == 2:
                        wheelchair_accessible = sval
                return air_conditioned, wheelchair_accessible
            pos += length
        elif wire_type == 0:  # varint
            while pos < len(raw) and raw[pos] & 0x80:
                pos += 1
            pos += 1
        elif wire_type == 1:  # 64-bit
            pos += 8
        elif wire_type == 5:  # 32-bit
            pos += 4
    return False, 0


@dataclass
class StopInfo:
    stop_id: str
    stop_code: str | None
    stop_name: str | None
    stop_desc: str | None
    stop_lat: float | None
    stop_lon: float | None
    wheelchair_boarding: str | None


@dataclass
class RouteInfo:
    route_id: str
    agency_id: str | None
    route_short_name: str | None
    route_long_name: str | None
    route_desc: str | None
    route_type: str | None
    route_color: str | None
    route_text_color: str | None


@dataclass
class TripInfo:
    trip_id: str
    route_id: str | None
    trip_headsign: str | None
    direction_id: str | None
    wheelchair_accessible: str | None


class AdelaideMetroApiClient:
    def __init__(self, hass):
        self.hass = hass
        self._session = async_get_clientsession(hass)

    async def async_fetch_trip_updates(self):
        async with self._session.get(TRIP_UPDATES_URL) as resp:
            resp.raise_for_status()
            data = await resp.read()

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(data)
        return feed

    async def async_fetch_vehicle_positions(self) -> list[dict]:
        async with self._session.get(VEHICLE_POSITIONS_URL) as resp:
            resp.raise_for_status()
            data = await resp.read()

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(data)

        vehicles: list[dict] = []
        for entity in feed.entity:
            if not entity.HasField("vehicle"):
                continue
            v = entity.vehicle
            if not v.HasField("position") or not v.HasField("trip"):
                continue

            trip = v.trip
            pos = v.position
            vid = v.vehicle
            air_conditioned, wheelchair_accessible = _parse_tfnsw_vehicle_descriptor(vid)

            vehicles.append(
                {
                    "id": entity.id,
                    "trip_id": trip.trip_id,
                    "route_id": trip.route_id,
                    "direction_id": trip.direction_id if trip.HasField("direction_id") else None,
                    "latitude": pos.latitude,
                    "longitude": pos.longitude,
                    "bearing": pos.bearing if pos.HasField("bearing") else None,
                    "speed": pos.speed if pos.HasField("speed") else None,
                    "vehicle_id": vid.id if vid.HasField("id") else None,
                    "vehicle_label": vid.label if vid.HasField("label") else None,
                    "timestamp": v.timestamp,
                    "air_conditioned": air_conditioned,
                    "wheelchair_accessible": wheelchair_accessible,
                    "current_stop_sequence": v.current_stop_sequence if v.HasField("current_stop_sequence") else None,
                    "stop_id": v.stop_id if v.HasField("stop_id") else None,
                    "current_status": int(v.current_status) if v.HasField("current_status") else None,
                }
            )
        return vehicles

    async def async_fetch_service_alerts(self):
        async with self._session.get(SERVICE_ALERTS_URL) as resp:
            resp.raise_for_status()
            data = await resp.read()

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(data)
        return feed

    async def async_fetch_static_gtfs(self) -> tuple[
        dict[str, StopInfo],
        dict[str, RouteInfo],
        dict[str, TripInfo],
        dict[tuple[str, str], str],
        dict[str, tuple[str, str]],
        dict[str, set[tuple[str, str]]],
        dict[str, set[str]],
    ]:
        async with self._session.get(STATIC_GTFS_URL) as resp:
            resp.raise_for_status()
            data = await resp.read()

        zf = zipfile.ZipFile(BytesIO(data))
        stops = self._read_stops(zf)
        routes = self._read_routes(zf)
        trips = self._read_trips(zf)
        direction_headsigns = self._read_direction_headsigns(zf)
        stop_directions, stop_directions_raw = self._read_stop_directions(zf, trips)
        route_stops = self._read_route_stops(zf)
        return stops, routes, trips, direction_headsigns, stop_directions, stop_directions_raw, route_stops

    def _read_stops(self, zf: zipfile.ZipFile) -> dict[str, StopInfo]:
        with zf.open("stops.txt") as f:
            decoded = (line.decode("utf-8-sig") for line in f)
            reader = csv.DictReader(decoded)
            stops: dict[str, StopInfo] = {}
            for row in reader:
                stop_id = row.get("stop_id")
                if not stop_id:
                    continue
                stops[stop_id] = StopInfo(
                    stop_id=stop_id,
                    stop_code=row.get("stop_code") or None,
                    stop_name=row.get("stop_name") or None,
                    stop_desc=row.get("stop_desc") or None,
                    stop_lat=float(row["stop_lat"]) if row.get("stop_lat") else None,
                    stop_lon=float(row["stop_lon"]) if row.get("stop_lon") else None,
                    wheelchair_boarding=row.get("wheelchair_boarding") or None,
                )
        return stops

    def _read_routes(self, zf: zipfile.ZipFile) -> dict[str, RouteInfo]:
        with zf.open("routes.txt") as f:
            decoded = (line.decode("utf-8-sig") for line in f)
            reader = csv.DictReader(decoded)
            routes: dict[str, RouteInfo] = {}
            for row in reader:
                route_id = row.get("route_id")
                if not route_id:
                    continue
                routes[route_id] = RouteInfo(
                    route_id=route_id,
                    agency_id=row.get("agency_id") or None,
                    route_short_name=row.get("route_short_name") or None,
                    route_long_name=row.get("route_long_name") or None,
                    route_desc=row.get("route_desc") or None,
                    route_type=row.get("route_type") or None,
                    route_color=row.get("route_color") or None,
                    route_text_color=row.get("route_text_color") or None,
                )
        return routes

    def _read_trips(self, zf: zipfile.ZipFile) -> dict[str, TripInfo]:
        with zf.open("trips.txt") as f:
            decoded = (line.decode("utf-8-sig") for line in f)
            reader = csv.DictReader(decoded)
            trips: dict[str, TripInfo] = {}
            for row in reader:
                trip_id = row.get("trip_id")
                if not trip_id:
                    continue
                trips[trip_id] = TripInfo(
                    trip_id=trip_id,
                    route_id=row.get("route_id") or None,
                    trip_headsign=row.get("trip_headsign") or None,
                    direction_id=row.get("direction_id") or None,
                    wheelchair_accessible=row.get("wheelchair_accessible") or None,
                )
        return trips

    def _read_direction_headsigns(self, zf: zipfile.ZipFile) -> dict[tuple[str, str], str]:
        """Build a (route_id, direction_id) -> canonical headsign lookup from trips.txt."""
        headsigns: dict[tuple[str, str], set[str]] = defaultdict(set)
        with zf.open("trips.txt") as f:
            decoded = (line.decode("utf-8-sig") for line in f)
            reader = csv.DictReader(decoded)
            for row in reader:
                route_id = row.get("route_id")
                direction_id = row.get("direction_id")
                headsign = row.get("trip_headsign")
                if route_id and direction_id is not None and headsign:
                    headsigns[(route_id, direction_id)].add(headsign)
        # Pick the most common / first headsign per (route, direction)
        return {k: sorted(v)[0] for k, v in headsigns.items()}

    def _read_stop_directions(
        self, zf: zipfile.ZipFile, trips: dict[str, TripInfo]
    ) -> tuple[dict[str, tuple[str, str]], dict[str, set[tuple[str, str]]]]:
        """Build stop_id -> (route_id, direction_id) lookups from stop_times.txt.

        Returns (single_pick, raw) — single_pick picks one direction per stop,
        raw preserves the full set for user-route preference matching.
        """
        raw: dict[str, set[tuple[str, str]]] = defaultdict(set)
        with zf.open("stop_times.txt") as f:
            decoded = (line.decode("utf-8-sig") for line in f)
            reader = csv.DictReader(decoded)
            for row in reader:
                trip_id = row.get("trip_id")
                stop_id = row.get("stop_id")
                trip = trips.get(trip_id)
                if trip and stop_id and trip.route_id and trip.direction_id is not None:
                    raw[stop_id].add((trip.route_id, trip.direction_id))
        # Each stop typically maps to one (route, direction) — take first alphabetically
        single = {stop_id: sorted(dirs)[0] for stop_id, dirs in raw.items() if dirs}
        return single, dict(raw)

    def _read_route_stops(self, zf: zipfile.ZipFile) -> dict[str, set[str]]:
        """Build a route_id -> set(stop_ids) mapping from stop_times.txt.

        Used to auto-discover stops when none are manually configured.
        """
        route_stops: dict[str, set[str]] = defaultdict(set)
        with zf.open("trips.txt") as f:
            decoded = (line.decode("utf-8-sig") for line in f)
            reader = csv.DictReader(decoded)
            trip_routes: dict[str, str] = {
                row["trip_id"]: row["route_id"]
                for row in reader
                if row.get("trip_id") and row.get("route_id")
            }
        with zf.open("stop_times.txt") as f:
            decoded = (line.decode("utf-8-sig") for line in f)
            reader = csv.DictReader(decoded)
            for row in reader:
                trip_id = row.get("trip_id")
                stop_id = row.get("stop_id")
                route_id = trip_routes.get(trip_id)
                if route_id and stop_id:
                    route_stops[route_id].add(stop_id)
        return dict(route_stops)

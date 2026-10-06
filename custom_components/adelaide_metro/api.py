from __future__ import annotations

import csv
import logging
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from io import BytesIO

from google.transit import gtfs_realtime_pb2
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    SERVICE_ALERTS_URL,
    TRIP_UPDATES_URL,
    VEHICLE_POSITIONS_URL,
)
from .schedule import WEEKDAYS, ServiceCalendar, parse_gtfs_date, parse_gtfs_time

_LOGGER = logging.getLogger(__name__)


def _parse_tfnsw_vehicle_descriptor(vehicle_desc_msg) -> tuple[bool, int]:
    """Parse the TFNSW extension, falling back to defaults on malformed data."""
    try:
        return _decode_tfnsw_vehicle_descriptor(vehicle_desc_msg.SerializeToString())
    except (IndexError, ValueError):
        _LOGGER.debug("Could not decode TFNSW vehicle descriptor extension")
        return False, 0


def _decode_tfnsw_vehicle_descriptor(raw: bytes) -> tuple[bool, int]:
    """Parse TFNSW extension (field 1999) from serialized VehicleDescriptor bytes.

    Adelaide Metro uses a custom protobuf extension on VehicleDescriptor
    (extension id 1999, namespace transit_realtime.tfnsw_vehicle_descriptor)
    with fields air_conditioned (bool, default true) and
    wheelchair_accessible (int32, 0 or 1, default 0).

    Returns (air_conditioned, wheelchair_accessible) or (False, 0) if absent.
    Raises IndexError/ValueError on truncated or unsupported data.
    """
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
                    if stag & 0x7 != 0:
                        raise ValueError(f"Unexpected wire type in extension field {sfn}")
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
        else:
            raise ValueError(f"Unsupported wire type {wire_type}")
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
    service_id: str | None = None


@dataclass
class StaticGtfs:
    stops: dict[str, StopInfo]
    routes: dict[str, RouteInfo]
    trips: dict[str, TripInfo]
    direction_headsigns: dict[tuple[str, str], str]
    stop_directions: dict[str, tuple[str, str]]
    stop_directions_raw: dict[str, set[tuple[str, str]]]
    route_stops: dict[str, set[str]]
    calendar: ServiceCalendar = field(default_factory=ServiceCalendar)
    # stop_id -> [(seconds since service-day start, trip_id)], sorted; only for monitored stops/routes
    schedules: dict[str, list[tuple[int, str]]] = field(default_factory=dict)


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


def _read_csv(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as f:
        yield from csv.DictReader(line.decode("utf-8-sig") for line in f)


def _read_calendar(zf: zipfile.ZipFile) -> ServiceCalendar:
    calendar = ServiceCalendar()
    names = set(zf.namelist())
    if "calendar.txt" in names:
        for row in _read_csv(zf, "calendar.txt"):
            service_id = row.get("service_id")
            start = parse_gtfs_date(row.get("start_date", ""))
            end = parse_gtfs_date(row.get("end_date", ""))
            if not service_id or not start or not end:
                continue
            days = tuple(row.get(day, "0").strip() == "1" for day in WEEKDAYS)
            calendar.weekly[service_id] = (days, start, end)
    if "calendar_dates.txt" in names:
        for row in _read_csv(zf, "calendar_dates.txt"):
            service_id = row.get("service_id")
            day = parse_gtfs_date(row.get("date", ""))
            try:
                exception_type = int(row.get("exception_type", ""))
            except ValueError:
                continue
            if service_id and day:
                calendar.exceptions.setdefault(day, {})[service_id] = exception_type
    return calendar


def parse_static_gtfs(
    data: bytes,
    routes: set[str] | frozenset[str] = frozenset(),
    stops: set[str] | frozenset[str] = frozenset(),
) -> StaticGtfs:
    """Parse a static GTFS zip. Blocking; run in an executor.

    Timetables are only kept for ``stops`` (when given) or for every stop on
    ``routes`` (when no stops are given), to keep memory down.
    """
    with zipfile.ZipFile(BytesIO(data)) as zf:
        stops: dict[str, StopInfo] = {}
        for row in _read_csv(zf, "stops.txt"):
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

        routes: dict[str, RouteInfo] = {}
        for row in _read_csv(zf, "routes.txt"):
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

        # Single pass over trips.txt builds both the trip index and the
        # (route_id, direction_id) -> headsign lookup.
        trips: dict[str, TripInfo] = {}
        headsigns: dict[tuple[str, str], set[str]] = defaultdict(set)
        for row in _read_csv(zf, "trips.txt"):
            trip_id = row.get("trip_id")
            route_id = row.get("route_id")
            direction_id = row.get("direction_id")
            headsign = row.get("trip_headsign")
            if route_id and direction_id is not None and headsign:
                headsigns[(route_id, direction_id)].add(headsign)
            if not trip_id:
                continue
            trips[trip_id] = TripInfo(
                trip_id=trip_id,
                route_id=route_id or None,
                trip_headsign=headsign or None,
                direction_id=direction_id or None,
                wheelchair_accessible=row.get("wheelchair_accessible") or None,
                service_id=row.get("service_id") or None,
            )
        # Pick the alphabetically first headsign per (route, direction) so names are stable
        direction_headsigns = {k: min(v) for k, v in headsigns.items()}

        # Single pass over stop_times.txt (by far the largest file) builds both
        # stop -> {(route, direction)} and route -> {stops}.
        stop_directions_raw: dict[str, set[tuple[str, str]]] = defaultdict(set)
        route_stops: dict[str, set[str]] = defaultdict(set)
        schedules: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for row in _read_csv(zf, "stop_times.txt"):
            stop_id = row.get("stop_id")
            trip = trips.get(row.get("trip_id"))
            if not trip or not stop_id or not trip.route_id:
                continue
            route_stops[trip.route_id].add(stop_id)
            if (stop_id in stops) if stops else (trip.route_id in routes):
                secs = parse_gtfs_time(row.get("departure_time") or row.get("arrival_time") or "")
                if secs is not None:
                    schedules[stop_id].append((secs, trip.trip_id))
            if trip.direction_id is not None:
                stop_directions_raw[stop_id].add((trip.route_id, trip.direction_id))
        # Each stop typically maps to one (route, direction) — take first alphabetically
        stop_directions = {stop_id: min(dirs) for stop_id, dirs in stop_directions_raw.items() if dirs}
        for times in schedules.values():
            times.sort()
        calendar = _read_calendar(zf)

    return StaticGtfs(
        stops=stops,
        routes=routes,
        trips=trips,
        direction_headsigns=direction_headsigns,
        stop_directions=stop_directions,
        stop_directions_raw=dict(stop_directions_raw),
        route_stops=dict(route_stops),
        calendar=calendar,
        schedules=dict(schedules),
    )

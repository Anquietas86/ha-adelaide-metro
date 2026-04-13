from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
import csv
import zipfile
from collections import defaultdict

from google.transit import gtfs_realtime_pb2
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import SERVICE_ALERTS_URL, STATIC_GTFS_URL, TRIP_UPDATES_URL


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
    ]:
        async with self._session.get(STATIC_GTFS_URL) as resp:
            resp.raise_for_status()
            data = await resp.read()

        zf = zipfile.ZipFile(BytesIO(data))
        stops = self._read_stops(zf)
        routes = self._read_routes(zf)
        trips = self._read_trips(zf)
        direction_headsigns = self._read_direction_headsigns(zf)
        stop_directions = self._read_stop_directions(zf, trips)
        return stops, routes, trips, direction_headsigns, stop_directions

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

    def _read_stop_directions(self, zf: zipfile.ZipFile, trips: dict[str, TripInfo]) -> dict[str, tuple[str, str]]:
        """Build a stop_id -> (route_id, direction_id) lookup from stop_times.txt."""
        stop_directions: dict[str, set[tuple[str, str]]] = defaultdict(set)
        with zf.open("stop_times.txt") as f:
            decoded = (line.decode("utf-8-sig") for line in f)
            reader = csv.DictReader(decoded)
            for row in reader:
                trip_id = row.get("trip_id")
                stop_id = row.get("stop_id")
                trip = trips.get(trip_id)
                if trip and stop_id and trip.route_id and trip.direction_id is not None:
                    stop_directions[stop_id].add((trip.route_id, trip.direction_id))
        # Each stop typically maps to one (route, direction) — take first
        return {stop_id: sorted(dirs)[0] for stop_id, dirs in stop_directions.items() if dirs}

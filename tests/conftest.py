"""Fixtures: a tiny static GTFS bundle and realtime feeds served via aioclient_mock."""

from __future__ import annotations

import io
import time
import zipfile

import pytest
from google.transit import gtfs_realtime_pb2
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adelaide_metro.const import (
    DOMAIN,
    SERVICE_ALERTS_URL,
    STATIC_GTFS_URL,
    TRIP_UPDATES_URL,
    VEHICLE_POSITIONS_URL,
)

STATIC_FILES = {
    "stops.txt": "stop_id,stop_code,stop_name,stop_lat,stop_lon\n"
    "16490,16490,Seaford Meadows Railway Station,-35.17,138.49\n"
    "16491,16491,Adelaide Railway Station,-34.92,138.59\n",
    "routes.txt": "route_id,route_short_name,route_long_name,route_type\n"
    "SEAFRD,SEAFRD,Seaford to City,2\n",
    "trips.txt": "route_id,service_id,trip_id,trip_headsign,direction_id\n"
    "SEAFRD,WK,T1,City,0\n"
    "SEAFRD,WK,T2,Seaford,1\n",
    "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
    "T1,08:00:00,08:00:00,16490,1\n"
    "T1,08:45:00,08:45:00,16491,2\n"
    "T2,09:00:00,09:00:00,16491,1\n",
}


def build_static_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in STATIC_FILES.items():
            zf.writestr(name, content)
    return buf.getvalue()


def build_trip_updates(departure_ts: int | None) -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    if departure_ts is not None:
        ent = feed.entity.add(id="tu1")
        ent.trip_update.trip.trip_id = "T1"
        ent.trip_update.trip.route_id = "SEAFRD"
        ent.trip_update.trip.direction_id = 0
        ent.trip_update.vehicle.id = "3020"
        ent.trip_update.vehicle.label = "3020"
        stu = ent.trip_update.stop_time_update.add(stop_id="16490", stop_sequence=1)
        stu.departure.time = departure_ts
    return feed.SerializeToString()


def build_alerts(header: str | None = None) -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    if header:
        ent = feed.entity.add(id="A1")
        ent.alert.header_text.translation.add(text=header)
        ent.alert.informed_entity.add(route_id="SEAFRD")
    return feed.SerializeToString()


def build_vehicles(vehicle_ids: list[str]) -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    for vid in vehicle_ids:
        ent = feed.entity.add(id=vid)
        ent.vehicle.trip.trip_id = "T1"
        ent.vehicle.trip.route_id = "SEAFRD"
        ent.vehicle.position.latitude = -35.0
        ent.vehicle.position.longitude = 138.5
        ent.vehicle.vehicle.id = vid
        ent.vehicle.vehicle.label = vid
    return feed.SerializeToString()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def feeds(aioclient_mock):
    """Serve feeds; call the returned function to change what is served."""

    def serve(departure_in_s: int | None = 600, vehicles=("V1",), alert=None, static_status=200):
        aioclient_mock.clear_requests()
        aioclient_mock.get(STATIC_GTFS_URL, content=build_static_zip(), status=static_status)
        dep = int(time.time()) + departure_in_s if departure_in_s is not None else None
        aioclient_mock.get(TRIP_UPDATES_URL, content=build_trip_updates(dep))
        aioclient_mock.get(SERVICE_ALERTS_URL, content=build_alerts(alert))
        aioclient_mock.get(VEHICLE_POSITIONS_URL, content=build_vehicles(list(vehicles)))

    serve()
    return serve


@pytest.fixture
def config_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Adelaide Metro Realtime",
        unique_id="SEAFRD",
        data={"routes": ["SEAFRD"], "stops": ["16490"]},
    )
    entry.add_to_hass(hass)
    return entry

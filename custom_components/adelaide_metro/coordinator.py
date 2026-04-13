from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import AdelaideMetroApiClient
from .const import (
    CONF_MAX_DEPARTURES,
    CONF_REFRESH_INTERVAL,
    CONF_ROUTE_FILTERS,
    CONF_STOPS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class AdelaideMetroDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        self.entry = entry
        self.api = AdelaideMetroApiClient(hass)
        self.stops = entry.data[CONF_STOPS]
        self.route_filters = set(entry.data.get(CONF_ROUTE_FILTERS, []))
        self.max_departures = entry.data[CONF_MAX_DEPARTURES]
        self.stop_index = {}
        self.route_index = {}
        self.trip_index = {}

        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=entry.data[CONF_REFRESH_INTERVAL]),
        )

    async def _async_update_data(self):
        if not self.stop_index:
            self.stop_index, self.route_index, self.trip_index = await self.api.async_fetch_static_gtfs()

        feed = await self.api.async_fetch_trip_updates()
        now = datetime.now(UTC).timestamp()
        departures_by_stop: dict[str, list[dict]] = {stop_id: [] for stop_id in self.stops}

        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue

            trip_update = entity.trip_update
            route_id = trip_update.trip.route_id
            if self.route_filters and route_id not in self.route_filters:
                continue

            vehicle_id = trip_update.vehicle.id if trip_update.HasField("vehicle") else None
            vehicle_label = trip_update.vehicle.label if trip_update.HasField("vehicle") else None
            route = self.route_index.get(route_id)
            trip = self.trip_index.get(trip_update.trip.trip_id)

            for stu in trip_update.stop_time_update:
                stop_id = stu.stop_id
                if stop_id not in departures_by_stop:
                    continue

                event = None
                if stu.HasField("departure") and stu.departure.time:
                    event = stu.departure
                elif stu.HasField("arrival") and stu.arrival.time:
                    event = stu.arrival

                if event is None or event.time < now:
                    continue

                departures_by_stop[stop_id].append(
                    {
                        "trip_id": trip_update.trip.trip_id,
                        "route_id": route_id,
                        "route_short_name": route.route_short_name if route else None,
                        "route_long_name": route.route_long_name if route else None,
                        "trip_headsign": trip.trip_headsign if trip else None,
                        "direction_id": trip_update.trip.direction_id,
                        "stop_id": stop_id,
                        "stop_sequence": stu.stop_sequence if stu.stop_sequence else None,
                        "time": int(event.time),
                        "delay": event.delay if event.HasField("delay") else None,
                        "vehicle_id": vehicle_id,
                        "vehicle_label": vehicle_label,
                        "feed_timestamp": int(trip_update.timestamp) if trip_update.timestamp else None,
                    }
                )

        for stop_id, deps in departures_by_stop.items():
            deps.sort(key=lambda d: d["time"])
            departures_by_stop[stop_id] = deps[: self.max_departures]

        return {
            "stops": self.stop_index,
            "routes": self.route_index,
            "trips": self.trip_index,
            "departures": departures_by_stop,
        }

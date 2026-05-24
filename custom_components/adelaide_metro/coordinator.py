from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import AdelaideMetroApiClient
from .const import (
    CONF_ALERT_GRACE_MINUTES,
    CONF_MAX_DEPARTURES,
    CONF_REFRESH_INTERVAL,
    CONF_ROUTE_FILTERS,
    CONF_ROUTES,
    CONF_STATIC_GTFS_REFRESH_HOURS,
    CONF_STOPS,
    DEFAULT_ALERT_GRACE_MINUTES,
    DEFAULT_MAX_DEPARTURES,
    DEFAULT_REFRESH_INTERVAL,
    DEFAULT_STATIC_GTFS_REFRESH_HOURS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _translated_text(translated) -> str | None:
    if not translated or not translated.translation:
        return None
    return translated.translation[0].text or None


class AdelaideMetroDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        self.entry = entry

        # Routes: new CONF_ROUTES takes precedence; fall back to CONF_ROUTE_FILTERS for backward compat
        raw_routes = entry.options.get(
            CONF_ROUTES,
            entry.data.get(CONF_ROUTES, None),
        )
        if raw_routes is None:
            raw_routes = entry.options.get(
                CONF_ROUTE_FILTERS,
                entry.data.get(CONF_ROUTE_FILTERS, []),
            )
        self.routes = set(raw_routes) if raw_routes else set()

        # Stops: explicit list or empty (auto-discovered from routes)
        raw_stops = entry.options.get(
            CONF_STOPS,
            entry.data.get(CONF_STOPS, []),
        )
        self.stops = (
            [s.strip() for s in raw_stops] if isinstance(raw_stops, str)
            else raw_stops if raw_stops else []
        )

        self.api = AdelaideMetroApiClient(hass)
        self.max_departures = entry.options.get(
            CONF_MAX_DEPARTURES,
            entry.data.get(CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES),
        )
        self._static_gtfs_refresh_hours = entry.options.get(
            CONF_STATIC_GTFS_REFRESH_HOURS,
            entry.data.get(CONF_STATIC_GTFS_REFRESH_HOURS, DEFAULT_STATIC_GTFS_REFRESH_HOURS),
        )
        self.stop_index = {}
        self.route_index = {}
        self.trip_index = {}
        self.direction_headsigns: dict[tuple[str, str], str] = {}
        self.stop_directions: dict[str, tuple[str, str]] = {}
        self.route_stops: dict[str, set[str]] = {}
        self._last_static_gtfs_refresh: datetime | None = None
        self.alert_grace_minutes = entry.options.get(
            CONF_ALERT_GRACE_MINUTES,
            entry.data.get(CONF_ALERT_GRACE_MINUTES, DEFAULT_ALERT_GRACE_MINUTES),
        )

        refresh_secs = entry.options.get(
            CONF_REFRESH_INTERVAL,
            entry.data.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL),
        )
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=refresh_secs),
        )

    def relevant_vehicles(self) -> list[dict]:
        """Filter vehicles to those on the user's configured routes."""
        vehicles = self.data.get("vehicles", [])
        monitored_route_ids = {
            dep.get("route_id")
            for departures in self.data.get("departures", {}).values()
            for dep in departures
            if dep.get("route_id")
        }
        relevant = []
        for vehicle in vehicles:
            route_id = vehicle.get("route_id")
            if route_id and (route_id in self.routes or route_id in monitored_route_ids):
                relevant.append(vehicle)
        return relevant

    async def _async_update_data(self):
        now = datetime.now(UTC)

        # Refresh static GTFS data periodically (default: every 24h)
        needs_static_refresh = (
            not self.stop_index
            or (
                self._last_static_gtfs_refresh is not None
                and (now - self._last_static_gtfs_refresh).total_seconds() >= self._static_gtfs_refresh_hours * 3600
            )
        )
        if needs_static_refresh:
            (
                self.stop_index,
                self.route_index,
                self.trip_index,
                self.direction_headsigns,
                self.stop_directions,
                self.route_stops,
            ) = await self.api.async_fetch_static_gtfs()
            self._last_static_gtfs_refresh = now

            # Auto-discover stops from routes when none were manually configured
            if not self.stops:
                discovered: set[str] = set()
                for route_id in self.routes:
                    stops_for_route = self.route_stops.get(route_id, set())
                    discovered.update(stops_for_route)
                self.stops = sorted(discovered)
                _LOGGER.info(
                    "Auto-discovered %d stops across %d route(s): %s",
                    len(self.stops),
                    len(self.routes),
                    self.routes,
                )

            _LOGGER.debug("Refreshed static GTFS data")

        feed = await self.api.async_fetch_trip_updates()
        alerts_feed = await self.api.async_fetch_service_alerts()
        vehicles = await self.api.async_fetch_vehicle_positions()
        now_ts = now.timestamp()
        departures_by_stop: dict[str, list[dict]] = {stop_id: [] for stop_id in self.stops}

        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue

            trip_update = entity.trip_update
            route_id = trip_update.trip.route_id

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

                if event is None or event.time < now_ts:
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
                        "stop_sequence": stu.stop_sequence if stu.HasField("stop_sequence") else None,
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

        alerts: list[dict] = []
        for entity in alerts_feed.entity:
            if not entity.HasField("alert"):
                continue
            alert = entity.alert
            informed = []
            for selector in alert.informed_entity:
                informed.append(
                    {
                        "route_id": selector.route_id or None,
                        "stop_id": selector.stop_id or None,
                    }
                )
            alerts.append(
                {
                    "id": entity.id,
                    "header": _translated_text(alert.header_text),
                    "description": _translated_text(alert.description_text),
                    "url": _translated_text(alert.url),
                    "cause": int(alert.cause) if alert.HasField("cause") else None,
                    "effect": int(alert.effect) if alert.HasField("effect") else None,
                    "informed_entities": informed,
                }
            )

        return {
            "stops": self.stop_index,
            "routes": self.route_index,
            "trips": self.trip_index,
            "departures": departures_by_stop,
            "alerts": alerts,
            "vehicles": vehicles,
        }

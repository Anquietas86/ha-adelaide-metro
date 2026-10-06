from __future__ import annotations

import asyncio
import logging
import zipfile
from datetime import UTC, datetime, timedelta

from aiohttp import ClientError
from google.protobuf.message import DecodeError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AdelaideMetroApiClient
from .const import (
    CONF_ALERT_GRACE_MINUTES,
    CONF_EXPOSE_TO_ASSISTANTS,
    CONF_MAX_DEPARTURES,
    CONF_REFRESH_INTERVAL,
    CONF_ROUTE_FILTERS,
    CONF_ROUTES,
    CONF_STATIC_GTFS_REFRESH_HOURS,
    CONF_STOPS,
    DEFAULT_ALERT_GRACE_MINUTES,
    DEFAULT_EXPOSE_TO_ASSISTANTS,
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
        # Never refresh the static bundle more than hourly, whatever the options say
        self._static_gtfs_refresh_hours = max(
            1,
            entry.options.get(
                CONF_STATIC_GTFS_REFRESH_HOURS,
                entry.data.get(CONF_STATIC_GTFS_REFRESH_HOURS, DEFAULT_STATIC_GTFS_REFRESH_HOURS),
            ),
        )
        self.stop_index = {}
        self.route_index = {}
        self.trip_index = {}
        self.direction_headsigns: dict[tuple[str, str], str] = {}
        self.stop_directions: dict[str, tuple[str, str]] = {}
        self.stop_directions_raw: dict[str, set[tuple[str, str]]] = {}
        self.route_stops: dict[str, set[str]] = {}
        self.stop_to_route: dict[str, str] = {}
        self._last_static_gtfs_refresh: datetime | None = None
        self.alert_grace_minutes = max(
            0,
            entry.options.get(
                CONF_ALERT_GRACE_MINUTES,
                entry.data.get(CONF_ALERT_GRACE_MINUTES, DEFAULT_ALERT_GRACE_MINUTES),
            ),
        )

        self.expose_to_assistants = entry.options.get(
            CONF_EXPOSE_TO_ASSISTANTS,
            entry.data.get(CONF_EXPOSE_TO_ASSISTANTS, DEFAULT_EXPOSE_TO_ASSISTANTS),
        )

        refresh_secs = entry.options.get(
            CONF_REFRESH_INTERVAL,
            entry.data.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL),
        )
        super().__init__(
            hass,
            logger=_LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=refresh_secs),
        )

    def resolve_route_device(self, route_id: str | None) -> dict:
        """Build device_info dict grouped under this route, or a safe fallback."""
        if not route_id:
            return {
                "identifiers": {(DOMAIN, "network")},
                "name": "Service Alerts",
                "manufacturer": "Adelaide Metro",
                "model": "GTFS Realtime Feed",
            }
        route = self.route_index.get(route_id)
        route_label = (
            route.route_short_name if route and route.route_short_name
            else route.route_long_name if route and route.route_long_name
            else route_id
        )
        return {
            "identifiers": {(DOMAIN, f"route_{route_id}")},
            "name": route_label,
            "manufacturer": "Adelaide Metro",
            "model": "GTFS Realtime Route",
        }

    def stop_route_id(self, stop_id: str) -> str | None:
        """Return the route ID a stop belongs to, for device grouping."""
        return self.stop_to_route.get(stop_id)

    def relevant_vehicles(self) -> list[dict]:
        """Vehicles on the user's configured or monitored routes (computed once per update)."""
        return self.data.get("relevant_vehicles", [])

    def relevant_alerts(self) -> list[dict]:
        """Alerts touching the user's stops or routes (computed once per update)."""
        return self.data.get("relevant_alerts", [])

    def _monitored_route_ids(self, departures: dict[str, list[dict]]) -> set[str]:
        return {
            dep["route_id"]
            for deps in departures.values()
            for dep in deps
            if dep.get("route_id")
        }

    def _filter_vehicles(self, vehicles: list[dict], monitored_route_ids: set[str]) -> list[dict]:
        return [
            v for v in vehicles
            if v.get("route_id") and (v["route_id"] in self.routes or v["route_id"] in monitored_route_ids)
        ]

    def _filter_alerts(self, alerts: list[dict], monitored_route_ids: set[str]) -> list[dict]:
        stop_ids = set(self.stops)
        relevant = []
        for alert in alerts:
            for informed in alert.get("informed_entities", []):
                route_id = informed.get("route_id")
                stop_id = informed.get("stop_id")
                if (stop_id and stop_id in stop_ids) or (
                    route_id and (route_id in self.routes or route_id in monitored_route_ids)
                ):
                    relevant.append(alert)
                    break
        return relevant

    async def _async_refresh_static_gtfs(self) -> None:
        static = await self.api.async_fetch_static_gtfs()
        self.stop_index = static.stops
        self.route_index = static.routes
        self.trip_index = static.trips
        self.direction_headsigns = static.direction_headsigns
        self.stop_directions = static.stop_directions
        self.stop_directions_raw = static.stop_directions_raw
        self.route_stops = static.route_stops

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
            first_load = not self.stop_index
            try:
                await self._async_refresh_static_gtfs()
            except (TimeoutError, ClientError, zipfile.BadZipFile, KeyError, ValueError) as err:
                if first_load:
                    raise UpdateFailed(f"Error fetching static GTFS data: {err}") from err
                # Keep serving realtime data with the previous static bundle; retry next hour
                _LOGGER.warning("Static GTFS refresh failed, keeping previous data: %s", err)
                self._last_static_gtfs_refresh = now - timedelta(
                    hours=self._static_gtfs_refresh_hours - 1
                )
                needs_static_refresh = False
            else:
                self._last_static_gtfs_refresh = now

        if needs_static_refresh:

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

            # Build stop→route mapping for device grouping (user routes only)
            self.stop_to_route.clear()
            for route_id in self.routes:
                for stop_id in self.route_stops.get(route_id, set()):
                    # Assign to the user-configured route; first wins
                    if stop_id not in self.stop_to_route:
                        self.stop_to_route[stop_id] = route_id

            _LOGGER.debug("Refreshed static GTFS data")

        try:
            feed, alerts_feed, vehicles = await asyncio.gather(
                self.api.async_fetch_trip_updates(),
                self.api.async_fetch_service_alerts(),
                self.api.async_fetch_vehicle_positions(),
            )
        except (TimeoutError, ClientError, DecodeError) as err:
            raise UpdateFailed(f"Error fetching realtime data: {err}") from err
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
                        "direction_id": (
                            trip_update.trip.direction_id
                            if trip_update.trip.HasField("direction_id")
                            else None
                        ),
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

        monitored_route_ids = self._monitored_route_ids(departures_by_stop)
        return {
            "relevant_vehicles": self._filter_vehicles(vehicles, monitored_route_ids),
            "relevant_alerts": self._filter_alerts(alerts, monitored_route_ids),
            "stops": self.stop_index,
            "routes": self.route_index,
            "trips": self.trip_index,
            "departures": departures_by_stop,
            "alerts": alerts,
            "vehicles": vehicles,
        }

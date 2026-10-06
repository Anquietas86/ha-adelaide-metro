from __future__ import annotations

import asyncio
import logging
import zipfile
from datetime import UTC, date, datetime, timedelta

from aiohttp import ClientError
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AdelaideMetroApiClient, parse_static_gtfs
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
    MAX_AUTO_DISCOVERED_STOPS,
)
from .gtfs_cache import StaticGtfsCache
from .schedule import (
    UNDATED_EXCLUDE_WINDOW,
    ServiceCalendar,
    parse_gtfs_date,
    scheduled_departures,
    service_day_start,
)

_LOGGER = logging.getLogger(__name__)

# Live vs timetabled times further apart than this are treated as different runs
MAX_INFERRED_DELAY = timedelta(hours=3)


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
        self.calendar = ServiceCalendar()
        self.schedules: dict[str, list[tuple[int, str]]] = {}
        # stop_id -> trip_id -> [seconds since service-day start], for delay lookups
        self._trip_times: dict[str, dict[str, list[int]]] = {}
        self._configured_stops = list(self.stops)
        # Set when auto-discovery found more stops than MAX_AUTO_DISCOVERED_STOPS
        self.discovered_stop_count = 0
        self.missing_routes: set[str] = set()
        self.missing_stops: set[str] = set()
        self.gtfs_cache = StaticGtfsCache(hass)
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

    async def _async_refresh_static_gtfs(self, force: bool = False) -> None:
        data = await self.gtfs_cache.async_get(
            timedelta(hours=self._static_gtfs_refresh_hours), force=force
        )
        static = await self.hass.async_add_executor_job(
            parse_static_gtfs, data, frozenset(self.routes), frozenset(self._configured_stops)
        )
        self.stop_index = static.stops
        self.route_index = static.routes
        self.trip_index = static.trips
        self.direction_headsigns = static.direction_headsigns
        self.stop_directions = static.stop_directions
        self.stop_directions_raw = static.stop_directions_raw
        self.route_stops = static.route_stops
        self.calendar = static.calendar
        self.schedules = static.schedules
        self._trip_times = {}
        for stop_id, entries in self.schedules.items():
            by_trip: dict[str, list[int]] = {}
            for secs, trip_id in entries:
                by_trip.setdefault(trip_id, []).append(secs)
            self._trip_times[stop_id] = by_trip

    def _apply_static_gtfs(self) -> None:
        """Work out monitored stops and stop->route grouping from fresh static data."""
        if not self._configured_stops:
            discovered: set[str] = set()
            for route_id in self.routes:
                discovered.update(self.route_stops.get(route_id, set()))
            self.discovered_stop_count = len(discovered)
            # Only pick the stop set once per load so entities don't come and go
            if not self.stops:
                self.stops = sorted(discovered)[:MAX_AUTO_DISCOVERED_STOPS]
                _LOGGER.info(
                    "Auto-discovered %d stops across %d route(s), monitoring %d",
                    len(discovered),
                    len(self.routes),
                    len(self.stops),
                )

        # Build stop->route mapping for device grouping (user routes only)
        self.stop_to_route.clear()
        for route_id in sorted(self.routes):
            for stop_id in self.route_stops.get(route_id, set()):
                self.stop_to_route.setdefault(stop_id, route_id)

        self.missing_routes = {r for r in self.routes if r not in self.route_index}
        self.missing_stops = {s for s in self._configured_stops if s not in self.stop_index}
        self._update_repair_issues()

    def _update_repair_issues(self) -> None:
        entry_id = self.entry.entry_id
        issues = {
            "unknown_routes": (
                bool(self.missing_routes),
                {"items": ", ".join(sorted(self.missing_routes))},
            ),
            "unknown_stops": (
                bool(self.missing_stops),
                {"items": ", ".join(sorted(self.missing_stops))},
            ),
            "too_many_stops": (
                self.discovered_stop_count > MAX_AUTO_DISCOVERED_STOPS,
                {
                    "found": str(self.discovered_stop_count),
                    "limit": str(MAX_AUTO_DISCOVERED_STOPS),
                },
            ),
        }
        for key, (active, placeholders) in issues.items():
            issue_id = f"{key}_{entry_id}"
            if active:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=key,
                    translation_placeholders=placeholders,
                )
            else:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _departure(self, trip_id: str, route_id: str | None, stop_id: str, ts: int) -> dict:
        route = self.route_index.get(route_id) if route_id else None
        trip = self.trip_index.get(trip_id)
        return {
            "trip_id": trip_id,
            "route_id": route_id,
            "route_short_name": route.route_short_name if route else None,
            "route_long_name": route.route_long_name if route else None,
            "trip_headsign": trip.trip_headsign if trip else None,
            "direction_id": (
                int(trip.direction_id) if trip and trip.direction_id and trip.direction_id.isdigit() else None
            ),
            "stop_id": stop_id,
            "time": ts,
            "scheduled_time": None,
            "delay": None,
            "delay_minutes": None,
            "delay_source": None,
            "realtime": False,
            "vehicle_id": None,
            "vehicle_label": None,
        }

    def _timetabled_time(
        self, stop_id: str, trip_id: str, service_date: date | None, around_ts: int
    ) -> int | None:
        """Timetabled time of a trip at a stop, for the run closest to a live time.

        Used to work out delays, since Adelaide Metro's feed doesn't send them.
        """
        secs_list = self._trip_times.get(stop_id, {}).get(trip_id)
        if not secs_list:
            return None
        tz = dt_util.get_default_time_zone()
        trip = self.trip_index.get(trip_id)
        if service_date is not None:
            days = [service_date]
        else:
            live_day = datetime.fromtimestamp(around_ts, tz).date()
            days = [live_day - timedelta(days=1), live_day]
            if trip and trip.service_id:
                days = [d for d in days if trip.service_id in self.calendar.active_services(d)] or days
        best = None
        for day in days:
            base = service_day_start(day, tz).timestamp()
            for secs in secs_list:
                ts = int(base + secs)
                if best is None or abs(ts - around_ts) < abs(best - around_ts):
                    best = ts
        # A match more than a few hours off is a different run, not a delay
        if best is None or abs(best - around_ts) > MAX_INFERRED_DELAY.total_seconds():
            return None
        return best

    def _scheduled_time_for(
        self, stop_id: str, trip_id: str, service_date: date | None, now: datetime
    ) -> int | None:
        """Timetabled time of a realtime trip's run at a stop, if still upcoming (for cancellations)."""
        tz = dt_util.get_default_time_zone()
        now_ts = now.timestamp()
        entries = [e for e in self.schedules.get(stop_id, []) if e[1] == trip_id]
        if not entries:
            return None
        if service_date is not None:
            base = service_day_start(service_date, tz).timestamp()
            for secs, _ in entries:
                if base + secs >= now_ts:
                    return int(base + secs)
            return None
        for ts, _ in scheduled_departures(
            entries, _TripServiceView(self.trip_index), self.calendar, now, tz, 1
        ):
            if ts - now_ts < UNDATED_EXCLUDE_WINDOW.total_seconds():
                return ts
        return None

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
                await self._async_refresh_static_gtfs(force=not first_load)
            except (TimeoutError, ClientError, OSError, zipfile.BadZipFile, KeyError, ValueError) as err:
                if first_load:
                    raise UpdateFailed(f"Error fetching static GTFS data: {err}") from err
                # Keep serving realtime data with the previous static bundle; retry next hour
                _LOGGER.warning("Static GTFS refresh failed, keeping previous data: %s", err)
                self._last_static_gtfs_refresh = now - timedelta(
                    hours=self._static_gtfs_refresh_hours - 1
                )
            else:
                self._last_static_gtfs_refresh = now
                self._apply_static_gtfs()
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
        tz = dt_util.get_default_time_zone()
        monitored = set(self.stops)
        departures_by_stop: dict[str, list[dict]] = {stop_id: [] for stop_id in self.stops}
        cancellations_by_stop: dict[str, list[dict]] = {stop_id: [] for stop_id in self.stops}
        # Trips the realtime feed speaks for (-> service date, if given); their
        # timetable entries must not be shown too
        realtime_trips: dict[str, date | None] = {}
        cancelled_trips_by_route: dict[str, set[str]] = {}
        cancelled_trips: dict[str, date | None] = {}
        no_data_at_stop: dict[str, set[str]] = {}

        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue

            trip_update = entity.trip_update
            trip_id = trip_update.trip.trip_id
            route_id = trip_update.trip.route_id or (
                self.trip_index[trip_id].route_id if trip_id in self.trip_index else None
            )
            service_date = parse_gtfs_date(trip_update.trip.start_date) if trip_update.trip.start_date else None
            if trip_id:
                realtime_trips[trip_id] = service_date

            if trip_update.trip.schedule_relationship == gtfs_realtime_pb2.TripDescriptor.CANCELED:
                cancelled_trips[trip_id] = service_date
                if route_id:
                    cancelled_trips_by_route.setdefault(route_id, set()).add(trip_id)
                continue

            vehicle_id = trip_update.vehicle.id if trip_update.HasField("vehicle") else None
            vehicle_label = trip_update.vehicle.label if trip_update.HasField("vehicle") else None

            for stu in trip_update.stop_time_update:
                stop_id = stu.stop_id
                if stop_id not in monitored:
                    continue

                event = None
                if stu.HasField("departure") and stu.departure.time:
                    event = stu.departure
                elif stu.HasField("arrival") and stu.arrival.time:
                    event = stu.arrival

                if stu.schedule_relationship == gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.SKIPPED:
                    ts = (
                        int(event.time)
                        if event is not None
                        else self._scheduled_time_for(stop_id, trip_id, service_date, now)
                    )
                    if ts is not None and ts >= now_ts:
                        cancellations_by_stop[stop_id].append(
                            {**self._departure(trip_id, route_id, stop_id, ts), "scheduled_time": ts, "reason": "skipped"}
                        )
                    continue

                if stu.schedule_relationship == gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.NO_DATA:
                    # No prediction for this stop: let the timetable entry show instead
                    no_data_at_stop.setdefault(stop_id, set()).add(trip_id)
                    continue

                if event is None or event.time < now_ts:
                    continue

                dep = self._departure(trip_id, route_id, stop_id, int(event.time))
                delay = event.delay if event.HasField("delay") else None
                delay_source = "feed" if delay is not None else None
                if delay is None:
                    timetabled = self._timetabled_time(stop_id, trip_id, service_date, int(event.time))
                    if timetabled is not None:
                        delay = int(event.time) - timetabled
                        delay_source = "timetable"
                dep.update(
                    {
                        "direction_id": (
                            trip_update.trip.direction_id
                            if trip_update.trip.HasField("direction_id")
                            else dep["direction_id"]
                        ),
                        "stop_sequence": stu.stop_sequence if stu.HasField("stop_sequence") else None,
                        "delay": delay,
                        "delay_minutes": round(delay / 60) if delay is not None else None,
                        "delay_source": delay_source,
                        "scheduled_time": int(event.time) - delay if delay is not None else None,
                        "realtime": True,
                        "vehicle_id": vehicle_id,
                        "vehicle_label": vehicle_label,
                        "feed_timestamp": int(trip_update.timestamp) if trip_update.timestamp else None,
                    }
                )
                departures_by_stop[stop_id].append(dep)

        for stop_id in self.stops:
            deps = departures_by_stop[stop_id]
            exclude = realtime_trips
            if stop_id in no_data_at_stop:
                exclude = {t: d for t, d in realtime_trips.items() if t not in no_data_at_stop[stop_id]}
            # Fill gaps from the timetable for trips the live feed doesn't cover
            for ts, trip_id in scheduled_departures(
                self.schedules.get(stop_id, []),
                _TripServiceView(self.trip_index),
                self.calendar,
                now,
                tz,
                self.max_departures,
                exclude=exclude,
            ):
                trip = self.trip_index.get(trip_id)
                dep = self._departure(trip_id, trip.route_id if trip else None, stop_id, ts)
                dep["scheduled_time"] = ts
                deps.append(dep)
            deps.sort(key=lambda d: d["time"])
            departures_by_stop[stop_id] = deps[: self.max_departures]

            # Cancelled trips that would have served this stop
            for trip_id, service_date in cancelled_trips.items():
                ts = self._scheduled_time_for(stop_id, trip_id, service_date, now)
                if ts is not None:
                    trip = self.trip_index.get(trip_id)
                    cancellations_by_stop[stop_id].append(
                        {
                            **self._departure(trip_id, trip.route_id if trip else None, stop_id, ts),
                            "scheduled_time": ts,
                            "reason": "cancelled",
                        }
                    )
            cancellations_by_stop[stop_id].sort(key=lambda d: d["time"])

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
            "cancellations": cancellations_by_stop,
            "cancelled_trips_by_route": {r: len(t) for r, t in cancelled_trips_by_route.items()},
            "alerts": alerts,
            "vehicles": vehicles,
        }


class _TripServiceView:
    """Read-only trip_id -> service_id mapping over the trip index, without copying it."""

    def __init__(self, trips) -> None:
        self._trips = trips

    def get(self, trip_id, default=None):
        trip = self._trips.get(trip_id)
        return trip.service_id if trip else default

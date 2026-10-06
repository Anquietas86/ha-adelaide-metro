from __future__ import annotations

import logging
from datetime import UTC, datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import AssistantExposureMixin, remove_orphaned_entities

_LOGGER = logging.getLogger(__name__)

MAX_STATE_LENGTH = 255

ALERT_UNIQUE_ID_PREFIX = "adelaide_metro_alert_"
VEHICLE_UNIQUE_ID_PREFIX = "adelaide_metro_vehicle_"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]

    known_alert_ids: set[str] = set()
    alert_seen_time: dict[str, datetime] = {}
    known_vehicle_ids: set[str] = set()

    entities: list[SensorEntity] = [AdelaideMetroAlertsSensor(coordinator)]
    for stop_id in coordinator.stops:
        entities.append(AdelaideMetroNextDepartureSensor(coordinator, stop_id))
        entities.append(AdelaideMetroNextDepartureTimeSensor(coordinator, stop_id))
        entities.append(AdelaideMetroUpcomingDeparturesSensor(coordinator, stop_id))

    for alert in coordinator.relevant_alerts():
        alert_id = alert.get("id") or "unknown"
        entities.append(AdelaideMetroAlertEntity(coordinator, alert))
        known_alert_ids.add(alert_id)
        alert_seen_time[alert_id] = datetime.now(UTC)

    for vehicle in coordinator.relevant_vehicles():
        entities.append(AdelaideMetroVehicleSensor(coordinator, vehicle))
        known_vehicle_ids.add(vehicle["id"])

    # Alerts and vehicles from before a restart that are no longer active
    remove_orphaned_entities(hass, entry, "sensor", {e.unique_id for e in entities})
    async_add_entities(entities)

    @callback
    def _handle_coordinator_update() -> None:
        now = datetime.now(UTC)
        grace_seconds = coordinator.alert_grace_minutes * 60

        current_alerts = coordinator.relevant_alerts()
        current_ids = {a.get("id") or "unknown" for a in current_alerts}

        # Update last-seen time for alerts still active
        for alert_id in current_ids:
            alert_seen_time[alert_id] = now

        new_ids = current_ids - known_alert_ids
        stale_ids = known_alert_ids - current_ids

        if new_ids:
            new_entities = [
                AdelaideMetroAlertEntity(coordinator, a)
                for a in current_alerts
                if (a.get("id") or "unknown") in new_ids
            ]
            async_add_entities(new_entities)
            _LOGGER.debug("Added %d new alert entities: %s", len(new_entities), new_ids)

        # Remove stale alerts only after grace period has expired
        expired_ids = {
            aid
            for aid in stale_ids
            if (now - alert_seen_time.get(aid, now)).total_seconds() >= grace_seconds
        }
        if expired_ids:
            registry = er.async_get(hass)
            for alert_id in expired_ids:
                unique_id = f"{ALERT_UNIQUE_ID_PREFIX}{alert_id}"
                entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
                if entity_id:
                    registry.async_remove(entity_id)
                    _LOGGER.debug("Removed stale alert entity: %s (%s)", entity_id, alert_id)
                alert_seen_time.pop(alert_id, None)

        # Keep alerts that are within grace period in the set so they don't get re-created
        known_alert_ids.clear()
        known_alert_ids.update(current_ids)
        known_alert_ids.update(stale_ids - expired_ids)

        # Manage vehicle entities — appear and disappear as vehicles come and go
        current_vehicles = coordinator.relevant_vehicles()
        current_vehicle_ids = {v["id"] for v in current_vehicles}

        new_vehicle_ids = current_vehicle_ids - known_vehicle_ids
        stale_vehicle_ids = known_vehicle_ids - current_vehicle_ids

        if new_vehicle_ids:
            new_vehicle_entities = [
                AdelaideMetroVehicleSensor(coordinator, v)
                for v in current_vehicles
                if v["id"] in new_vehicle_ids
            ]
            async_add_entities(new_vehicle_entities)
            _LOGGER.debug("Added %d new vehicle entities: %s", len(new_vehicle_entities), new_vehicle_ids)

        if stale_vehicle_ids:
            registry = er.async_get(hass)
            for vehicle_id in stale_vehicle_ids:
                unique_id = f"{VEHICLE_UNIQUE_ID_PREFIX}{vehicle_id}"
                entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
                if entity_id:
                    registry.async_remove(entity_id)
                    _LOGGER.debug("Removed stale vehicle entity: %s (%s)", entity_id, vehicle_id)

        known_vehicle_ids.clear()
        known_vehicle_ids.update(current_vehicle_ids)

    entry.async_on_unload(coordinator.async_add_listener(_handle_coordinator_update))


class AdelaideMetroBaseSensor(AssistantExposureMixin, CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    # The departure list changes every poll; keep it out of the recorder database
    _unrecorded_attributes = frozenset({"departures", "cancellations"})

    def __init__(self, coordinator, stop_id: str, suffix: str) -> None:
        super().__init__(coordinator)
        self._stop_id = stop_id
        self._stop = coordinator.stop_index.get(stop_id)
        self._display_name = self._device_name
        route_id = coordinator.stop_route_id(stop_id)
        if route_id:
            self._attr_device_info = coordinator.resolve_route_device(route_id)
            self._attr_name = f"{self._display_name} {suffix}"
        else:
            # The stop gets its own device named after it, so don't repeat the stop name
            self._attr_device_info = {
                "identifiers": {(DOMAIN, f"stop_{stop_id}")},
                "name": self._display_name,
                "manufacturer": "Adelaide Metro",
                "model": "GTFS Realtime Stop",
            }
            self._attr_name = suffix[:1].upper() + suffix[1:]

    @property
    def _departures(self):
        return self.coordinator.data["departures"].get(self._stop_id, [])

    @property
    def _cancellations(self):
        return self.coordinator.data.get("cancellations", {}).get(self._stop_id, [])

    @property
    def _next(self) -> dict | None:
        return self._departures[0] if self._departures else None

    def _next_attributes(self) -> dict:
        """Details of the next departure, shared by the minutes and timestamp sensors."""
        dep = self._next
        if not dep:
            return {}
        scheduled = dep.get("scheduled_time")
        return {
            "arriving_at": dt_util.as_local(datetime.fromtimestamp(dep["time"], tz=UTC)).strftime("%H:%M"),
            "scheduled_at": (
                dt_util.as_local(datetime.fromtimestamp(scheduled, tz=UTC)).strftime("%H:%M")
                if scheduled
                else None
            ),
            "realtime": dep.get("realtime", False),
            "delay_minutes": dep.get("delay_minutes"),
            "delay_source": dep.get("delay_source"),
            "route_id": dep.get("route_id"),
            "trip_headsign": dep.get("trip_headsign"),
        }

    @property
    def _direction_suffix(self) -> str | None:
        """Derive stable direction label, preferring user's configured routes."""
        direction_headsigns = self.coordinator.direction_headsigns
        user_routes = self.coordinator.routes

        # Primary: prefer a (route, direction) pair on a user-configured route
        raw_dirs = self.coordinator.stop_directions_raw.get(self._stop_id, set())
        matching = [(r, d) for r, d in raw_dirs if r in user_routes]
        for r, d in sorted(matching):
            headsign = direction_headsigns.get((r, d))
            if headsign:
                return f"{headsign}-bound"

        # Fallback: use the first direction assigned to this stop
        route_dir = self.coordinator.stop_directions.get(self._stop_id)
        if route_dir:
            headsign = direction_headsigns.get(route_dir)
            if headsign:
                return f"{headsign}-bound"

        # Fallback: use live departure data if static lookup missed
        if self._departures:
            dep = self._departures[0]
            direction_id = dep.get("direction_id")
            headsign = None
            if direction_id is not None:
                headsign = direction_headsigns.get((dep.get("route_id"), str(direction_id)))
            headsign = headsign or dep.get("trip_headsign")
            if headsign:
                return f"{headsign}-bound"

        return None

    @property
    def _device_name(self) -> str:
        base = self._stop.stop_name if self._stop and self._stop.stop_name else f"Stop {self._stop_id}"
        suffix = self._direction_suffix
        return f"{base} ({suffix})" if suffix else base

    @property
    def extra_state_attributes(self):
        return {
            "stop_id": self._stop_id,
            "stop_name": self._stop.stop_name if self._stop else None,
            "display_name": self._display_name,
            "stop_code": self._stop.stop_code if self._stop else None,
            "latitude": self._stop.stop_lat if self._stop else None,
            "longitude": self._stop.stop_lon if self._stop else None,
            "wheelchair_boarding": self._stop.wheelchair_boarding if self._stop else None,
            "departures": self._departures,
            "cancellations": self._cancellations,
        }


class AdelaideMetroNextDepartureSensor(AdelaideMetroBaseSensor):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator, stop_id: str) -> None:
        super().__init__(coordinator, stop_id, "next departure")
        self._attr_unique_id = f"adelaide_metro_{stop_id}_next_departure"
        self._attr_icon = "mdi:bus-clock"

    @property
    def native_value(self):
        if not self._departures:
            # Unknown rather than 0, which would read as "departing now"
            return None
        next_time = self._departures[0]["time"]
        delta = int((next_time - datetime.now(UTC).timestamp()) // 60)
        return max(delta, 0)

    @property
    def extra_state_attributes(self):
        return {**super().extra_state_attributes, **self._next_attributes()}


class AdelaideMetroNextDepartureTimeSensor(AdelaideMetroBaseSensor):
    """Next departure as a timestamp, so dashboards can count down live."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, stop_id: str) -> None:
        super().__init__(coordinator, stop_id, "next departure time")
        self._attr_unique_id = f"adelaide_metro_{stop_id}_next_departure_time"
        self._attr_icon = "mdi:clock-outline"

    @property
    def native_value(self):
        dep = self._next
        return datetime.fromtimestamp(dep["time"], tz=UTC) if dep else None

    @property
    def extra_state_attributes(self):
        # The minutes sensor already carries the full departure list
        return self._next_attributes()


class AdelaideMetroUpcomingDeparturesSensor(AdelaideMetroBaseSensor):
    def __init__(self, coordinator, stop_id: str) -> None:
        super().__init__(coordinator, stop_id, "upcoming")
        self._attr_unique_id = f"adelaide_metro_{stop_id}_upcoming_departures"
        self._attr_icon = "mdi:format-list-bulleted"

    @property
    def native_value(self):
        return len(self._departures)


class AdelaideMetroAlertsSensor(AssistantExposureMixin, CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Service alerts"
        self._attr_unique_id = "adelaide_metro_service_alerts"
        self._attr_icon = "mdi:alert-circle-outline"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "network")},
            "name": "Service Alerts",
            "manufacturer": "Adelaide Metro",
            "model": "GTFS Realtime Feed",
        }

    @property
    def native_value(self):
        return len(self.coordinator.data.get("alerts", []))

    @property
    def extra_state_attributes(self):
        all_alerts = self.coordinator.data.get("alerts", [])
        # Limit stored alerts to avoid exceeding HA's 16KB attribute limit.
        # Each alert carries a full HTML description — keep just the headers.
        slim_alerts = [
            {"id": a.get("id"), "header": a.get("header"), "url": a.get("url")}
            for a in all_alerts
        ]
        return {
            "alerts": slim_alerts,
            "relevant_alert_count": len(self.coordinator.relevant_alerts()),
        }


class AdelaideMetroAlertEntity(AssistantExposureMixin, CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, alert: dict) -> None:
        super().__init__(coordinator)
        self._alert_id = alert.get("id") or "unknown"
        self._attr_name = alert.get("header") or f"Alert {self._alert_id}"
        self._attr_unique_id = f"{ALERT_UNIQUE_ID_PREFIX}{self._alert_id}"
        self._attr_icon = "mdi:alert"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "network")},
            "name": "Service Alerts",
            "manufacturer": "Adelaide Metro",
            "model": "GTFS Realtime Feed",
        }

    def _current_alert(self) -> dict | None:
        for alert in self.coordinator.relevant_alerts():
            if (alert.get("id") or "unknown") == self._alert_id:
                return alert
        return None

    @property
    def native_value(self):
        alert = self._current_alert()
        if not alert:
            return None
        # HA rejects states longer than 255 characters
        header = alert.get("header") or "Active"
        if len(header) > MAX_STATE_LENGTH:
            header = header[: MAX_STATE_LENGTH - 1] + "…"
        return header

    @property
    def available(self):
        return super().available and self._current_alert() is not None

    @property
    def extra_state_attributes(self):
        alert = self._current_alert()
        if not alert:
            return {}
        return {
            "description": alert.get("description"),
            "url": alert.get("url"),
            "cause": alert.get("cause"),
            "effect": alert.get("effect"),
            "informed_entities": alert.get("informed_entities", []),
        }


class AdelaideMetroVehicleSensor(AssistantExposureMixin, CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, vehicle: dict) -> None:
        super().__init__(coordinator)
        self._vehicle_id = vehicle["id"]
        route_id = vehicle.get("route_id") or "unknown"
        vehicle_label = vehicle.get("vehicle_label") or vehicle.get("vehicle_id") or "?"

        route = coordinator.route_index.get(route_id)
        route_label = (
            route.route_long_name if route and route.route_long_name
            else route.route_short_name if route and route.route_short_name
            else route_id
        )
        # Extract meaningful prefix: "Seaford to City — 3020" is too verbose.
        # Use short label from long name where possible: "Seaford line 3020"
        if " to " in (route_label or ""):
            parts = route_label.split(" to ", 1)
            prefix = f"{parts[0]} line"
        else:
            prefix = route_label

        self._attr_name = f"{prefix} {vehicle_label}"
        self._attr_unique_id = f"{VEHICLE_UNIQUE_ID_PREFIX}{self._vehicle_id}"
        self._attr_icon = "mdi:bus"
        self._attr_device_info = coordinator.resolve_route_device(route_id)

    @property
    def native_value(self):
        vehicle = self._current_vehicle()
        if not vehicle:
            return None
        return vehicle.get("vehicle_label") or vehicle.get("vehicle_id") or "Active"

    @property
    def available(self):
        return super().available and self._current_vehicle() is not None

    def _current_vehicle(self) -> dict | None:
        for vehicle in self.coordinator.relevant_vehicles():
            if vehicle["id"] == self._vehicle_id:
                return vehicle
        return None

    @property
    def extra_state_attributes(self):
        vehicle = self._current_vehicle()
        if not vehicle:
            return {}
        route_id = vehicle.get("route_id")
        route = self.coordinator.route_index.get(route_id)
        trip = self.coordinator.trip_index.get(vehicle.get("trip_id") or "")

        speed_ms = vehicle.get("speed")
        speed_kmh = None
        if speed_ms is not None:
            speed_ms = round(speed_ms, 1)
            speed_kmh = round(speed_ms * 3.6, 1)  # m/s → km/h

        current_status_map = {
            0: "INCOMING_AT",
            1: "STOPPED_AT",
            2: "IN_TRANSIT_TO",
        }

        route_name = None
        if route:
            route_name = route.route_long_name or route.route_short_name

        return {
            "route_id": route_id,
            "route_name": route_name,
            "trip_headsign": trip.trip_headsign if trip else None,
            "direction_id": vehicle.get("direction_id"),
            "latitude": vehicle.get("latitude"),
            "longitude": vehicle.get("longitude"),
            "bearing": vehicle.get("bearing"),
            "speed_ms": speed_ms,
            "speed_kmh": speed_kmh,
            "current_status": current_status_map.get(vehicle.get("current_status"), None),
            "air_conditioned": vehicle.get("air_conditioned"),
            "wheelchair_accessible": vehicle.get("wheelchair_accessible"),
            "last_updated": vehicle.get("timestamp"),
        }

"""Device tracker platform for Adelaide Metro vehicle positions."""

from __future__ import annotations

import logging

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]

    known_vehicle_ids: set[str] = set()

    entities: list[TrackerEntity] = []
    for vehicle in coordinator.relevant_vehicles():
        vehicle_id = vehicle["id"]
        entities.append(AdelaideMetroVehicleTracker(coordinator, vehicle))
        known_vehicle_ids.add(vehicle_id)

    async_add_entities(entities)

    @callback
    def _handle_coordinator_update() -> None:
        nonlocal known_vehicle_ids
        current_vehicles = coordinator.relevant_vehicles()
        current_ids = {v["id"] for v in current_vehicles}

        new_ids = current_ids - known_vehicle_ids
        stale_ids = known_vehicle_ids - current_ids

        if new_ids:
            new_entities = [
                AdelaideMetroVehicleTracker(coordinator, v)
                for v in current_vehicles
                if v["id"] in new_ids
            ]
            async_add_entities(new_entities)
            _LOGGER.debug("Added %d new vehicle trackers: %s", len(new_entities), new_ids)

        if stale_ids:
            registry = er.async_get(hass)
            for vehicle_id in stale_ids:
                unique_id = f"adelaide_metro_tracker_{vehicle_id}"
                entity_id = registry.async_get_entity_id("device_tracker", DOMAIN, unique_id)
                if entity_id:
                    registry.async_remove(entity_id)
                    _LOGGER.debug("Removed stale tracker: %s (%s)", entity_id, vehicle_id)

        known_vehicle_ids.clear()
        known_vehicle_ids.update(current_ids)

    coordinator.async_add_listener(_handle_coordinator_update)


class AdelaideMetroVehicleTracker(CoordinatorEntity, TrackerEntity):
    _attr_has_entity_name = True
    _attr_source_type = SourceType.GPS
    _attr_force_update = True
    _unrecorded_attributes = frozenset({})

    def __init__(self, coordinator, vehicle: dict) -> None:
        super().__init__(coordinator)
        self._vehicle_id = vehicle["id"]
        route_id = vehicle.get("route_id") or "unknown"
        vehicle_label = vehicle.get("vehicle_label") or vehicle.get("vehicle_id") or "?"

        route = coordinator.route_index.get(route_id)
        route_label = (
            route.route_short_name if route and route.route_short_name else route_id
        )

        self._attr_name = f"{route_label} — {vehicle_label}"
        self._attr_unique_id = f"adelaide_metro_tracker_{self._vehicle_id}"
        self._attr_icon = "mdi:bus"
        self._attr_device_info = coordinator.resolve_route_device(route_id)

        self._attr_latitude = vehicle.get("latitude")
        self._attr_longitude = vehicle.get("longitude")

    @property
    def available(self) -> bool:
        return self._current_vehicle() is not None

    def _current_vehicle(self) -> dict | None:
        for vehicle in self.coordinator.relevant_vehicles():
            if vehicle["id"] == self._vehicle_id:
                return vehicle
        return None

    @callback
    def _handle_coordinator_update(self) -> None:
        vehicle = self._current_vehicle()
        if vehicle:
            self._attr_latitude = vehicle.get("latitude")
            self._attr_longitude = vehicle.get("longitude")
        super()._handle_coordinator_update()

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
            speed_kmh = round(speed_ms * 3.6, 1)

        current_status_map = {0: "INCOMING_AT", 1: "STOPPED_AT", 2: "IN_TRANSIT_TO"}
        raw_status = vehicle.get("current_status")
        if raw_status is not None:
            status_label = current_status_map.get(raw_status)
        else:
            status_label = None

        return {
            "route_id": route_id,
            "route_name": (
                route.route_long_name
                if route and route.route_long_name
                else (route.route_short_name if route else None)
            ),
            "trip_headsign": trip.trip_headsign if trip else None,
            "bearing": vehicle.get("bearing"),
            "speed_ms": speed_ms,
            "speed_kmh": speed_kmh,
            "current_status": status_label,
            "air_conditioned": vehicle.get("air_conditioned"),
            "wheelchair_accessible": vehicle.get("wheelchair_accessible"),
        }

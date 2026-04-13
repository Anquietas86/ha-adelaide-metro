from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for stop_id in coordinator.stops:
        entities.append(AdelaideMetroNextDepartureSensor(coordinator, stop_id))
        entities.append(AdelaideMetroUpcomingDeparturesSensor(coordinator, stop_id))
    async_add_entities(entities)


class AdelaideMetroBaseSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, stop_id: str) -> None:
        super().__init__(coordinator)
        self._stop_id = stop_id
        self._stop = coordinator.stop_index.get(stop_id)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"stop_{stop_id}")},
            "name": self._device_name,
            "manufacturer": "Adelaide Metro",
            "model": "GTFS Realtime Stop",
        }

    @property
    def _departures(self):
        return self.coordinator.data["departures"].get(self._stop_id, [])

    @property
    def _direction_suffix(self) -> str | None:
        if not self._departures:
            return None
        dep = self._departures[0]
        headsign = dep.get("trip_headsign")
        if headsign:
            return f"{headsign}-bound"

        direction_id = dep.get("direction_id")
        if direction_id == 0:
            return "City-bound"
        if direction_id == 1:
            return "Outbound"
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
            "display_name": self._device_name,
            "stop_code": self._stop.stop_code if self._stop else None,
            "latitude": self._stop.stop_lat if self._stop else None,
            "longitude": self._stop.stop_lon if self._stop else None,
            "wheelchair_boarding": self._stop.wheelchair_boarding if self._stop else None,
            "departures": self._departures,
        }


class AdelaideMetroNextDepartureSensor(AdelaideMetroBaseSensor):
    def __init__(self, coordinator, stop_id: str) -> None:
        super().__init__(coordinator, stop_id)
        self._attr_name = "Next departure"
        self._attr_unique_id = f"adelaide_metro_{stop_id}_next_departure"
        self._attr_icon = "mdi:bus-clock"
        self._attr_native_unit_of_measurement = "min"

    @property
    def native_value(self):
        if not self._departures:
            return None
        next_time = self._departures[0]["time"]
        delta = int((next_time - datetime.now(UTC).timestamp()) // 60)
        return max(delta, 0)


class AdelaideMetroUpcomingDeparturesSensor(AdelaideMetroBaseSensor):
    def __init__(self, coordinator, stop_id: str) -> None:
        super().__init__(coordinator, stop_id)
        self._attr_name = "Upcoming departures"
        self._attr_unique_id = f"adelaide_metro_{stop_id}_upcoming_departures"
        self._attr_icon = "mdi:format-list-bulleted"

    @property
    def native_value(self):
        return len(self._departures)

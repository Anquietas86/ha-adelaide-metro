"""Per-route disruption binary sensors."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .entity import AssistantExposureMixin


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        AdelaideMetroRouteDisruptionSensor(coordinator, route_id) for route_id in sorted(coordinator.routes)
    )


class AdelaideMetroRouteDisruptionSensor(AssistantExposureMixin, CoordinatorEntity, BinarySensorEntity):
    """On when a service alert or a cancelled trip affects the route."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:alert-octagon-outline"

    def __init__(self, coordinator, route_id: str) -> None:
        super().__init__(coordinator)
        self._route_id = route_id
        self._attr_name = "Disruption"
        self._attr_unique_id = f"adelaide_metro_route_{route_id}_disruption"
        self._attr_device_info = coordinator.resolve_route_device(route_id)

    def _alerts(self) -> list[dict]:
        route_stops = self.coordinator.route_stops.get(self._route_id, set())
        return [
            alert
            for alert in self.coordinator.relevant_alerts()
            if any(
                informed.get("route_id") == self._route_id
                or (informed.get("stop_id") and informed["stop_id"] in route_stops)
                for informed in alert.get("informed_entities", [])
            )
        ]

    def _cancelled_trips(self) -> int:
        return self.coordinator.data.get("cancelled_trips_by_route", {}).get(self._route_id, 0)

    @property
    def is_on(self) -> bool:
        return bool(self._alerts()) or self._cancelled_trips() > 0

    @property
    def extra_state_attributes(self):
        return {
            "route_id": self._route_id,
            "alerts": [a.get("header") for a in self._alerts()],
            "cancelled_trips": self._cancelled_trips(),
        }

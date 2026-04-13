from __future__ import annotations

from datetime import UTC, datetime
import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CONF_EXPOSE_TO_ASSISTANTS, DEFAULT_EXPOSE_TO_ASSISTANTS, DOMAIN

_LOGGER = logging.getLogger(__name__)


def _expose_entity_to_voice_assistants(hass: HomeAssistant, entity_id: str) -> None:
    registry = er.async_get(hass)
    if entity_id and registry.async_get(entity_id):
        try:
            registry.async_update_entity_options(entity_id, "conversation", {"should_expose": True})
            registry.async_update_entity_options(entity_id, "cloud.google_assistant", {"should_expose": True})
        except Exception as e:
            _LOGGER.debug("Could not expose %s to voice assistants: %s", entity_id, e)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    expose_to_assistants = entry.options.get(
        CONF_EXPOSE_TO_ASSISTANTS, entry.data.get(CONF_EXPOSE_TO_ASSISTANTS, DEFAULT_EXPOSE_TO_ASSISTANTS)
    )

    entities = [AdelaideMetroAlertsSensor(coordinator)]
    for stop_id in coordinator.stops:
        entities.append(AdelaideMetroNextDepartureSensor(coordinator, stop_id))
        entities.append(AdelaideMetroUpcomingDeparturesSensor(coordinator, stop_id))

    relevant_alerts = _filter_relevant_alerts(coordinator)
    for alert in relevant_alerts:
        entities.append(AdelaideMetroAlertEntity(coordinator, alert))

    async_add_entities(entities)

    if expose_to_assistants:
        await hass.async_add_executor_job(_apply_assistant_exposure, hass, DOMAIN)


def _apply_assistant_exposure(hass: HomeAssistant, domain: str) -> None:
    registry = er.async_get(hass)
    for entity in list(registry.entities.values()):
        if entity.platform != domain:
            continue
        _expose_entity_to_voice_assistants(hass, entity.entity_id)


def _filter_relevant_alerts(coordinator):
    alerts = coordinator.data.get("alerts", [])
    stop_ids = set(coordinator.stops)
    route_filters = set(coordinator.route_filters)
    monitored_route_ids = {
        dep.get("route_id")
        for departures in coordinator.data.get("departures", {}).values()
        for dep in departures
        if dep.get("route_id")
    }
    relevant = []

    for alert in alerts:
        informed = alert.get("informed_entities", [])
        if not informed:
            continue

        matches = False
        for entity in informed:
            route_id = entity.get("route_id")
            stop_id = entity.get("stop_id")
            if stop_id and stop_id in stop_ids:
                matches = True
                break
            if route_filters and route_id and route_id in route_filters:
                matches = True
                break
            if not route_filters and route_id and route_id in monitored_route_ids:
                matches = True
                break
        if matches:
            relevant.append(alert)

    return relevant


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


class AdelaideMetroAlertsSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Service alerts"
        self._attr_unique_id = "adelaide_metro_service_alerts"
        self._attr_icon = "mdi:alert-circle-outline"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "network")},
            "name": "Adelaide Metro",
            "manufacturer": "Adelaide Metro",
            "model": "GTFS Realtime Feed",
        }

    @property
    def native_value(self):
        return len(self.coordinator.data.get("alerts", []))

    @property
    def extra_state_attributes(self):
        return {
            "alerts": self.coordinator.data.get("alerts", []),
            "relevant_alert_count": len(_filter_relevant_alerts(self.coordinator)),
        }


class AdelaideMetroAlertEntity(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, alert: dict) -> None:
        super().__init__(coordinator)
        self._alert_id = alert.get("id") or "unknown"
        self._attr_name = alert.get("header") or f"Alert {self._alert_id}"
        self._attr_unique_id = f"adelaide_metro_alert_{self._alert_id}"
        self._attr_icon = "mdi:alert"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "network")},
            "name": "Adelaide Metro",
            "manufacturer": "Adelaide Metro",
            "model": "GTFS Realtime Feed",
        }

    @property
    def native_value(self):
        for alert in _filter_relevant_alerts(self.coordinator):
            if alert.get("id") == self._alert_id:
                return alert.get("header") or "Active"
        return None

    @property
    def available(self):
        return any(alert.get("id") == self._alert_id for alert in _filter_relevant_alerts(self.coordinator))

    @property
    def extra_state_attributes(self):
        for alert in _filter_relevant_alerts(self.coordinator):
            if alert.get("id") == self._alert_id:
                return {
                    "description": alert.get("description"),
                    "url": alert.get("url"),
                    "cause": alert.get("cause"),
                    "effect": alert.get("effect"),
                    "informed_entities": alert.get("informed_entities", []),
                }
        return {}

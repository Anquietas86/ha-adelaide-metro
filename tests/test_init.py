"""Integration-level tests: setup, sensors, entity lifecycle and services."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.adelaide_metro.const import DOMAIN


def _next_departure_entity(hass: HomeAssistant) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, "adelaide_metro_16490_next_departure")
    assert entity_id
    return entity_id


async def test_setup_creates_entities(hass: HomeAssistant, feeds, config_entry) -> None:
    await hass.config.async_set_time_zone("Australia/Adelaide")
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(_next_departure_entity(hass))
    assert state is not None
    assert int(state.state) in (9, 10)
    assert state.attributes["unit_of_measurement"] == "min"
    assert state.attributes["device_class"] == "duration"

    # arriving_at is local Adelaide time, not UTC
    dep_ts = state.attributes["departures"][0]["time"]
    expected = dt_util.as_local(dt_util.utc_from_timestamp(dep_ts)).strftime("%H:%M")
    assert state.attributes["arriving_at"] == expected
    assert dt_util.get_default_time_zone().key == "Australia/Adelaide"

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, "adelaide_metro_vehicle_V1")
    assert registry.async_get_entity_id("device_tracker", DOMAIN, "adelaide_metro_tracker_V1")


async def test_no_departures_is_unknown(hass: HomeAssistant, feeds, config_entry) -> None:
    feeds(departure_in_s=None)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(_next_departure_entity(hass)).state == STATE_UNKNOWN


async def test_long_alert_header_is_truncated(hass: HomeAssistant, feeds, config_entry) -> None:
    feeds(alert="x" * 400)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "adelaide_metro_alert_A1")
    state = hass.states.get(entity_id)
    assert len(state.state) == 255


async def test_orphaned_vehicles_removed_on_reload(hass: HomeAssistant, feeds, config_entry) -> None:
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, "adelaide_metro_vehicle_V1")

    # V1 finished its trip while HA was "restarting"
    feeds(vehicles=("V2",))
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get_entity_id("sensor", DOMAIN, "adelaide_metro_vehicle_V1") is None
    assert registry.async_get_entity_id("device_tracker", DOMAIN, "adelaide_metro_tracker_V1") is None
    assert registry.async_get_entity_id("sensor", DOMAIN, "adelaide_metro_vehicle_V2")


async def test_refresh_service_survives_reload(hass: HomeAssistant, feeds, config_entry) -> None:
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    with patch.object(coordinator, "async_request_refresh") as refresh:
        await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    refresh.assert_called_once()


async def test_static_refresh_failure_keeps_realtime(hass: HomeAssistant, feeds, config_entry) -> None:
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    # Static bundle now broken; force it to be due
    feeds(static_status=500)
    coordinator._last_static_gtfs_refresh -= timedelta(days=2)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()

    assert coordinator.last_update_success
    assert coordinator.stop_index  # previous static data kept
    assert hass.states.get(_next_departure_entity(hass)).state not in ("unavailable", STATE_UNKNOWN)


async def test_unload(hass: HomeAssistant, feeds, config_entry) -> None:
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.entry_id not in hass.data[DOMAIN]


async def test_expose_respects_user_choice(hass: HomeAssistant, feeds, config_entry) -> None:
    from homeassistant.components.homeassistant.exposed_entities import (
        async_expose_entity,
        async_should_expose,
    )
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "homeassistant", {})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    entity_id = _next_departure_entity(hass)
    assert async_should_expose(hass, "conversation", entity_id)

    # User un-exposes it; a reload must not flip it back
    async_expose_entity(hass, "conversation", entity_id, False)
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert not async_should_expose(hass, "conversation", entity_id)

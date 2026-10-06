"""Timetable fallback, delays, cancellations, disruption, cache, repairs and diagnostics."""

from __future__ import annotations

import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.adelaide_metro.const import DOMAIN, STATIC_GTFS_URL
from custom_components.adelaide_metro.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import SCHEDULED_IN_S


def _entity(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id, unique_id
    return entity_id


async def _setup(hass: HomeAssistant, entry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_timetable_fills_gaps(hass: HomeAssistant, feeds, config_entry) -> None:
    feeds(departure_in_s=None, scheduled_in_s=SCHEDULED_IN_S)
    await _setup(hass, config_entry)

    state = hass.states.get(_entity(hass, "sensor", "adelaide_metro_16490_next_departure"))
    assert int(state.state) in (19, 20)
    assert state.attributes["realtime"] is False
    assert state.attributes["departures"][0]["trip_id"] == "T3"


async def test_realtime_replaces_timetable_entry(hass: HomeAssistant, feeds, config_entry) -> None:
    # The live feed reports trip T1 only; T3 still comes from the timetable
    feeds(departure_in_s=300, delay=120, scheduled_in_s=SCHEDULED_IN_S)
    await _setup(hass, config_entry)

    state = hass.states.get(_entity(hass, "sensor", "adelaide_metro_16490_next_departure"))
    deps = state.attributes["departures"]
    assert [(d["trip_id"], d["realtime"]) for d in deps[:2]] == [("T1", True), ("T3", False)]
    # Today's timetabled T1 is replaced by the live one; only a run 12h+ away may remain
    assert all(d["realtime"] or d["trip_id"] != "T1" or d["time"] - time.time() > 12 * 3600 for d in deps)
    assert state.attributes["delay_minutes"] == 2
    assert deps[0]["scheduled_time"] == deps[0]["time"] - 120


async def test_timestamp_sensor(hass: HomeAssistant, feeds, config_entry) -> None:
    await _setup(hass, config_entry)
    state = hass.states.get(_entity(hass, "sensor", "adelaide_metro_16490_next_departure_time"))
    assert state.attributes["device_class"] == "timestamp"
    value = dt_util.parse_datetime(state.state)
    assert abs(value.timestamp() - (time.time() + 600)) < 5
    assert "departures" not in state.attributes


async def test_cancelled_trip(hass: HomeAssistant, feeds, config_entry) -> None:
    feeds(departure_in_s=None, scheduled_in_s=SCHEDULED_IN_S, cancel_trip="T3")
    await _setup(hass, config_entry)

    state = hass.states.get(_entity(hass, "sensor", "adelaide_metro_16490_next_departure"))
    soon = [d for d in state.attributes["departures"] if d["time"] - time.time() < 12 * 3600]
    assert all(d["trip_id"] != "T3" for d in soon)
    assert [c["trip_id"] for c in state.attributes["cancellations"]] == ["T3"]

    disruption = hass.states.get(_entity(hass, "binary_sensor", "adelaide_metro_route_SEAFRD_disruption"))
    assert disruption.state == "on"
    assert disruption.attributes["cancelled_trips"] == 1


async def test_disruption_from_alert(hass: HomeAssistant, feeds, config_entry) -> None:
    await _setup(hass, config_entry)
    entity_id = _entity(hass, "binary_sensor", "adelaide_metro_route_SEAFRD_disruption")
    assert hass.states.get(entity_id).state == "off"

    feeds(alert="Buses replace trains")
    await hass.data[DOMAIN][config_entry.entry_id].async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert state.attributes["alerts"] == ["Buses replace trains"]


async def test_timetable_cached_across_restarts(hass: HomeAssistant, feeds, config_entry, aioclient_mock) -> None:
    await _setup(hass, config_entry)
    downloads = sum(1 for call in aioclient_mock.mock_calls if str(call[1]) == STATIC_GTFS_URL)
    assert downloads == 1

    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    downloads = sum(1 for call in aioclient_mock.mock_calls if str(call[1]) == STATIC_GTFS_URL)
    assert downloads == 1  # second load came from disk


async def test_cached_timetable_used_when_download_fails(
    hass: HomeAssistant, feeds, config_entry, isolated_gtfs_cache
) -> None:
    await _setup(hass, config_entry)
    # Make the cache stale, then break the server
    meta = isolated_gtfs_cache / "google_transit.json"
    meta.write_text('{"fetched_at": "2020-01-01T00:00:00+00:00"}')
    feeds(static_status=500)
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.data[DOMAIN][config_entry.entry_id].last_update_success


async def test_unknown_route_raises_repair(hass: HomeAssistant, feeds) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"routes": ["SEAFRD", "NOPE"], "stops": ["16490"]})
    entry.add_to_hass(hass)
    await _setup(hass, entry)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"unknown_routes_{entry.entry_id}")
    assert issue is not None
    assert issue.translation_placeholders == {"items": "NOPE"}


async def test_auto_discovery_is_capped(hass: HomeAssistant, feeds, monkeypatch) -> None:
    monkeypatch.setattr("custom_components.adelaide_metro.coordinator.MAX_AUTO_DISCOVERED_STOPS", 1)
    entry = MockConfigEntry(domain=DOMAIN, data={"routes": ["SEAFRD"], "stops": []})
    entry.add_to_hass(hass)
    await _setup(hass, entry)

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.stops == ["16490"]
    assert coordinator.discovered_stop_count == 2
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"too_many_stops_{entry.entry_id}") is not None


async def test_diagnostics(hass: HomeAssistant, feeds, config_entry) -> None:
    await _setup(hass, config_entry)
    diag = await async_get_config_entry_diagnostics(hass, config_entry)
    assert diag["coordinator"]["routes"] == ["SEAFRD"]
    assert diag["static_gtfs"]["stops"] == 2
    assert diag["static_gtfs"]["last_fetched"]
    assert diag["realtime"]["realtime_departures"] == 1


async def test_timetable_across_midnight(hass: HomeAssistant, feeds, config_entry, freezer) -> None:
    await hass.config.async_set_time_zone("Australia/Adelaide")
    freezer.move_to("2026-10-05T23:55:00+10:30")
    feeds(departure_in_s=None, scheduled_in_s=SCHEDULED_IN_S)  # 24:15:00 on Monday's service day
    await _setup(hass, config_entry)

    state = hass.states.get(_entity(hass, "sensor", "adelaide_metro_16490_next_departure"))
    assert state.attributes["departures"][0]["trip_id"] == "T3"
    assert state.attributes["arriving_at"] == "00:15"

"""Config and options flow tests."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.adelaide_metro.const import DOMAIN, STATIC_GTFS_URL

BASE = {
    "routes": ["SEAFRD"],
    "max_departures": 5,
    "refresh_interval": 60,
    "expose_to_assistants": False,
    "static_gtfs_refresh_hours": 24,
    "alert_grace_minutes": 30,
}


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


def _options(schema, key: str) -> dict[str, str]:
    for field, selector in schema.schema.items():
        if field == key:
            return {o["value"]: o["label"] for o in selector.config["options"]}
    raise KeyError(key)


async def test_create_entry_with_pickers(hass: HomeAssistant, feeds) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert _options(result["data_schema"], "routes") == {"SEAFRD": "SEAFRD · Seaford to City"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], BASE)
    assert result["step_id"] == "stops"
    stops = _options(result["data_schema"], "stops")
    assert stops["16490"] == "Seaford Meadows Railway Station (16490) → City"
    assert stops["16491"] == "Adelaide Railway Station (16491) → City, Seaford"

    with patch("custom_components.adelaide_metro.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"stops": ["16490"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["routes"] == ["SEAFRD"]
    assert result["data"]["stops"] == ["16490"]


async def test_aborts_when_timetable_unavailable(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(STATIC_GTFS_URL, status=500)
    result = await _start(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_rejects_zero_static_refresh(hass: HomeAssistant, feeds) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**BASE, "static_gtfs_refresh_hours": 0}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"static_gtfs_refresh_hours": "invalid_static_gtfs_refresh_hours"}


async def test_rejects_negative_grace(hass: HomeAssistant, feeds) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**BASE, "alert_grace_minutes": -5})
    assert result["errors"] == {"alert_grace_minutes": "invalid_alert_grace_minutes"}


async def test_options_flow_reuses_loaded_timetable(hass: HomeAssistant, feeds, config_entry) -> None:
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    with patch("custom_components.adelaide_metro.config_flow.async_load_static") as load:
        result = await hass.config_entries.options.async_init(config_entry.entry_id)
    load.assert_not_called()
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(result["flow_id"], BASE)
    assert result["step_id"] == "stops"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"stops": ["16490", "16491"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert config_entry.options["stops"] == ["16490", "16491"]

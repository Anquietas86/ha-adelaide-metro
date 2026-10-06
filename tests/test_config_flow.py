"""Config flow tests."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.adelaide_metro.const import DOMAIN

BASE = {
    "routes": "SEAFRD, GLNELG",
    "stops": "",
    "max_departures": 5,
    "refresh_interval": 60,
    "expose_to_assistants": False,
    "static_gtfs_refresh_hours": 24,
    "alert_grace_minutes": 30,
}


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def test_create_entry(hass: HomeAssistant) -> None:
    result = await _start(hass)
    with patch(
        "custom_components.adelaide_metro.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], BASE)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["routes"] == ["SEAFRD", "GLNELG"]
    assert result["data"]["stops"] == []


async def test_rejects_zero_static_refresh(hass: HomeAssistant) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**BASE, "static_gtfs_refresh_hours": 0}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"static_gtfs_refresh_hours": "invalid_static_gtfs_refresh_hours"}


async def test_rejects_negative_grace(hass: HomeAssistant) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**BASE, "alert_grace_minutes": -5})
    assert result["errors"] == {"alert_grace_minutes": "invalid_alert_grace_minutes"}

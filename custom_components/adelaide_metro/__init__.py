from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_MAX_DEPARTURES,
    CONF_REFRESH_INTERVAL,
    CONF_ROUTE_FILTERS,
    CONF_STOPS,
    DEFAULT_MAX_DEPARTURES,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import AdelaideMetroDataUpdateCoordinator


def _entry_value(entry: ConfigEntry, key: str, default=None):
    return entry.options.get(key, entry.data.get(key, default))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not entry.options:
        hass.config_entries.async_update_entry(
            entry,
            options={
                CONF_STOPS: entry.data.get(CONF_STOPS, []),
                CONF_ROUTE_FILTERS: entry.data.get(CONF_ROUTE_FILTERS, []),
                CONF_MAX_DEPARTURES: entry.data.get(CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES),
                CONF_REFRESH_INTERVAL: entry.data.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL),
            },
        )

    coordinator = AdelaideMetroDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok

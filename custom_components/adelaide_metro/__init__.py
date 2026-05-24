from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import (
    CONF_EXPOSE_TO_ASSISTANTS,
    CONF_MAX_DEPARTURES,
    CONF_REFRESH_INTERVAL,
    CONF_ROUTE_FILTERS,
    CONF_ROUTES,
    CONF_STATIC_GTFS_REFRESH_HOURS,
    CONF_STOPS,
    DEFAULT_EXPOSE_TO_ASSISTANTS,
    DEFAULT_MAX_DEPARTURES,
    DEFAULT_REFRESH_INTERVAL,
    DEFAULT_STATIC_GTFS_REFRESH_HOURS,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import AdelaideMetroDataUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not entry.options:
        # First-time setup — migrate old route_filters to routes if needed
        routes = entry.data.get(CONF_ROUTES) or entry.data.get(CONF_ROUTE_FILTERS, [])
        stops = entry.data.get(CONF_STOPS, [])
        gtfs_hrs = entry.data.get(CONF_STATIC_GTFS_REFRESH_HOURS, DEFAULT_STATIC_GTFS_REFRESH_HOURS)
        hass.config_entries.async_update_entry(
            entry,
            options={
                CONF_ROUTES: routes,
                CONF_STOPS: stops,
                CONF_MAX_DEPARTURES: entry.data.get(CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES),
                CONF_REFRESH_INTERVAL: entry.data.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL),
                CONF_EXPOSE_TO_ASSISTANTS: entry.data.get(CONF_EXPOSE_TO_ASSISTANTS, DEFAULT_EXPOSE_TO_ASSISTANTS),
                CONF_STATIC_GTFS_REFRESH_HOURS: gtfs_hrs,
            },
        )

    coordinator = AdelaideMetroDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register the refresh service
    async def _handle_refresh(call: ServiceCall) -> None:
        await coordinator.async_request_refresh()

    if not hass.services.has_service(DOMAIN, "refresh"):
        hass.services.async_register(DOMAIN, "refresh", _handle_refresh)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok

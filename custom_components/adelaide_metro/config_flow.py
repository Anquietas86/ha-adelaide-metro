from __future__ import annotations

import zipfile
from datetime import timedelta
from typing import Any

import voluptuous as vol
from aiohttp import ClientError
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import RouteInfo, StaticGtfs, parse_static_gtfs
from .const import (
    CONF_ALERT_GRACE_MINUTES,
    CONF_EXPOSE_TO_ASSISTANTS,
    CONF_MAX_DEPARTURES,
    CONF_REFRESH_INTERVAL,
    CONF_ROUTES,
    CONF_STATIC_GTFS_REFRESH_HOURS,
    CONF_STOPS,
    DEFAULT_ALERT_GRACE_MINUTES,
    DEFAULT_EXPOSE_TO_ASSISTANTS,
    DEFAULT_MAX_DEPARTURES,
    DEFAULT_REFRESH_INTERVAL,
    DEFAULT_STATIC_GTFS_REFRESH_HOURS,
    DOMAIN,
    MAX_AUTO_DISCOVERED_STOPS,
)
from .gtfs_cache import StaticGtfsCache


def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not user_input.get(CONF_ROUTES):
        errors[CONF_ROUTES] = "no_routes"
    elif user_input[CONF_MAX_DEPARTURES] < 1:
        errors[CONF_MAX_DEPARTURES] = "invalid_max_departures"
    elif user_input[CONF_REFRESH_INTERVAL] < 15:
        errors[CONF_REFRESH_INTERVAL] = "invalid_refresh_interval"
    elif user_input.get(CONF_STATIC_GTFS_REFRESH_HOURS, DEFAULT_STATIC_GTFS_REFRESH_HOURS) < 1:
        errors[CONF_STATIC_GTFS_REFRESH_HOURS] = "invalid_static_gtfs_refresh_hours"
    elif user_input.get(CONF_ALERT_GRACE_MINUTES, DEFAULT_ALERT_GRACE_MINUTES) < 0:
        errors[CONF_ALERT_GRACE_MINUTES] = "invalid_alert_grace_minutes"
    return errors


def _entry_data(user_input: dict[str, Any], stops: list[str]) -> dict[str, Any]:
    return {
        CONF_ROUTES: list(user_input[CONF_ROUTES]),
        CONF_STOPS: stops,
        CONF_MAX_DEPARTURES: user_input[CONF_MAX_DEPARTURES],
        CONF_REFRESH_INTERVAL: user_input[CONF_REFRESH_INTERVAL],
        CONF_EXPOSE_TO_ASSISTANTS: user_input.get(CONF_EXPOSE_TO_ASSISTANTS, DEFAULT_EXPOSE_TO_ASSISTANTS),
        CONF_STATIC_GTFS_REFRESH_HOURS: user_input.get(
            CONF_STATIC_GTFS_REFRESH_HOURS, DEFAULT_STATIC_GTFS_REFRESH_HOURS
        ),
        CONF_ALERT_GRACE_MINUTES: user_input.get(CONF_ALERT_GRACE_MINUTES, DEFAULT_ALERT_GRACE_MINUTES),
    }


def _route_label(route: RouteInfo) -> str:
    if route.route_short_name and route.route_long_name and route.route_short_name != route.route_long_name:
        return f"{route.route_short_name} · {route.route_long_name}"
    return route.route_long_name or route.route_short_name or route.route_id


def _route_options(static: StaticGtfs, extra: list[str]) -> list[SelectOptionDict]:
    options = [
        SelectOptionDict(value=route_id, label=_route_label(route))
        for route_id, route in static.routes.items()
    ]
    # Keep already-configured routes selectable (and removable) even if the feed dropped them
    options += [
        SelectOptionDict(value=route_id, label=f"{route_id} (not in timetable)")
        for route_id in extra
        if route_id not in static.routes
    ]
    return sorted(options, key=lambda o: o["label"].casefold())


def _stop_options(static: StaticGtfs, routes: list[str], extra: list[str]) -> list[SelectOptionDict]:
    route_set = set(routes)
    stop_ids: set[str] = set()
    for route_id in routes:
        stop_ids |= static.route_stops.get(route_id, set())
    options = []
    for stop_id in stop_ids:
        stop = static.stops.get(stop_id)
        name = stop.stop_name if stop and stop.stop_name else f"Stop {stop_id}"
        code = stop.stop_code if stop and stop.stop_code else stop_id
        headsigns = sorted(
            {
                static.direction_headsigns[(r, d)]
                for r, d in static.stop_directions_raw.get(stop_id, set())
                if r in route_set and (r, d) in static.direction_headsigns
            }
        )
        towards = f" → {', '.join(headsigns)}" if headsigns else ""
        options.append(SelectOptionDict(value=stop_id, label=f"{name} ({code}){towards}"))
    options += [
        SelectOptionDict(value=stop_id, label=f"Stop {stop_id} (not on selected routes)")
        for stop_id in extra
        if stop_id not in stop_ids
    ]
    return sorted(options, key=lambda o: o["label"].casefold())


def _settings_schema(static: StaticGtfs, defaults: dict[str, Any]) -> vol.Schema:
    routes = list(defaults.get(CONF_ROUTES, []))
    return vol.Schema(
        {
            vol.Required(CONF_ROUTES, default=routes): SelectSelector(
                SelectSelectorConfig(
                    options=_route_options(static, routes),
                    multiple=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_MAX_DEPARTURES, default=defaults.get(CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES)
            ): int,
            vol.Optional(
                CONF_REFRESH_INTERVAL, default=defaults.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL)
            ): int,
            vol.Optional(
                CONF_EXPOSE_TO_ASSISTANTS,
                default=defaults.get(CONF_EXPOSE_TO_ASSISTANTS, DEFAULT_EXPOSE_TO_ASSISTANTS),
            ): bool,
            vol.Optional(
                CONF_STATIC_GTFS_REFRESH_HOURS,
                default=defaults.get(CONF_STATIC_GTFS_REFRESH_HOURS, DEFAULT_STATIC_GTFS_REFRESH_HOURS),
            ): int,
            vol.Optional(
                CONF_ALERT_GRACE_MINUTES,
                default=defaults.get(CONF_ALERT_GRACE_MINUTES, DEFAULT_ALERT_GRACE_MINUTES),
            ): int,
        }
    )


def _stops_schema(static: StaticGtfs, routes: list[str], current: list[str]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_STOPS, default=current): SelectSelector(
                SelectSelectorConfig(
                    options=_stop_options(static, routes, current),
                    multiple=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


async def async_load_static(hass) -> StaticGtfs:
    """Load the timetable (from the on-disk cache when fresh) for building pickers."""
    data = await StaticGtfsCache(hass).async_get(timedelta(hours=DEFAULT_STATIC_GTFS_REFRESH_HOURS))
    return await hass.async_add_executor_job(parse_static_gtfs, data)


class AdelaideMetroConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._static: StaticGtfs | None = None
        self._settings: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return AdelaideMetroOptionsFlowHandler()

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        if self._static is None:
            try:
                self._static = await async_load_static(self.hass)
            except (TimeoutError, ClientError, OSError, zipfile.BadZipFile, KeyError, ValueError):
                return self.async_abort(reason="cannot_connect")

        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                self._settings = user_input
                return await self.async_step_stops()

        return self.async_show_form(
            step_id="user",
            data_schema=_settings_schema(self._static, user_input or {}),
            errors=errors,
        )

    async def async_step_stops(self, user_input=None) -> ConfigFlowResult:
        routes = list(self._settings[CONF_ROUTES])
        if user_input is not None:
            data = _entry_data(self._settings, list(user_input.get(CONF_STOPS, [])))
            await self.async_set_unique_id("|".join(sorted(data[CONF_ROUTES])))
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Adelaide Metro Realtime", data=data)

        return self.async_show_form(
            step_id="stops",
            data_schema=_stops_schema(self._static, routes, []),
            description_placeholders={"limit": str(MAX_AUTO_DISCOVERED_STOPS)},
        )


class AdelaideMetroOptionsFlowHandler(config_entries.OptionsFlowWithReload):
    def __init__(self) -> None:
        self._static: StaticGtfs | None = None
        self._settings: dict[str, Any] = {}

    def _current(self) -> dict[str, Any]:
        current = {**self.config_entry.data, **self.config_entry.options}
        # Routes: prefer CONF_ROUTES, fall back to CONF_ROUTE_FILTERS for existing installs
        current[CONF_ROUTES] = current.get(CONF_ROUTES) or current.get("route_filters", [])
        current.setdefault(CONF_STOPS, [])
        return current

    def _static_from_coordinator(self) -> StaticGtfs | None:
        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        if not coordinator or not coordinator.route_index:
            return None
        return StaticGtfs(
            stops=coordinator.stop_index,
            routes=coordinator.route_index,
            trips={},
            direction_headsigns=coordinator.direction_headsigns,
            stop_directions=coordinator.stop_directions,
            stop_directions_raw=coordinator.stop_directions_raw,
            route_stops=coordinator.route_stops,
        )

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._static is None:
            # Reuse the loaded entry's timetable rather than parsing the bundle again
            self._static = self._static_from_coordinator()
            if self._static is None:
                try:
                    self._static = await async_load_static(self.hass)
                except (TimeoutError, ClientError, OSError, zipfile.BadZipFile, KeyError, ValueError):
                    return self.async_abort(reason="cannot_connect")

        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                self._settings = user_input
                return await self.async_step_stops()

        return self.async_show_form(
            step_id="init",
            data_schema=_settings_schema(self._static, user_input or self._current()),
            errors=errors,
        )

    async def async_step_stops(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        routes = list(self._settings[CONF_ROUTES])
        if user_input is not None:
            return self.async_create_entry(
                title="", data=_entry_data(self._settings, list(user_input.get(CONF_STOPS, [])))
            )

        return self.async_show_form(
            step_id="stops",
            data_schema=_stops_schema(self._static, routes, list(self._current()[CONF_STOPS])),
            description_placeholders={"limit": str(MAX_AUTO_DISCOVERED_STOPS)},
        )

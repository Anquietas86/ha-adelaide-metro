from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback

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
)


def _split_ids(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not _split_ids(user_input.get(CONF_ROUTES, "")):
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


def _entry_data(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        CONF_ROUTES: _split_ids(user_input.get(CONF_ROUTES, "")),
        CONF_STOPS: _split_ids(user_input.get(CONF_STOPS, "")),
        CONF_MAX_DEPARTURES: user_input[CONF_MAX_DEPARTURES],
        CONF_REFRESH_INTERVAL: user_input[CONF_REFRESH_INTERVAL],
        CONF_EXPOSE_TO_ASSISTANTS: user_input.get(CONF_EXPOSE_TO_ASSISTANTS, DEFAULT_EXPOSE_TO_ASSISTANTS),
        CONF_STATIC_GTFS_REFRESH_HOURS: user_input.get(
            CONF_STATIC_GTFS_REFRESH_HOURS, DEFAULT_STATIC_GTFS_REFRESH_HOURS
        ),
        CONF_ALERT_GRACE_MINUTES: user_input.get(CONF_ALERT_GRACE_MINUTES, DEFAULT_ALERT_GRACE_MINUTES),
    }


class AdelaideMetroConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return AdelaideMetroOptionsFlowHandler()

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                data = _entry_data(user_input)
                await self.async_set_unique_id("|".join(sorted(data[CONF_ROUTES])))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Adelaide Metro Realtime", data=data)

        schema = vol.Schema(
            {
                vol.Required(CONF_ROUTES): str,
                vol.Optional(CONF_STOPS, default=""): str,
                vol.Optional(CONF_MAX_DEPARTURES, default=DEFAULT_MAX_DEPARTURES): int,
                vol.Optional(CONF_REFRESH_INTERVAL, default=DEFAULT_REFRESH_INTERVAL): int,
                vol.Optional(CONF_EXPOSE_TO_ASSISTANTS, default=DEFAULT_EXPOSE_TO_ASSISTANTS): bool,
                vol.Optional(CONF_STATIC_GTFS_REFRESH_HOURS, default=DEFAULT_STATIC_GTFS_REFRESH_HOURS): int,
                vol.Optional(CONF_ALERT_GRACE_MINUTES, default=DEFAULT_ALERT_GRACE_MINUTES): int,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class AdelaideMetroOptionsFlowHandler(config_entries.OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                return self.async_create_entry(title="", data=_entry_data(user_input))

        current = {**self.config_entry.data, **self.config_entry.options}
        # Routes: prefer CONF_ROUTES, fall back to CONF_ROUTE_FILTERS for existing installs
        current_routes = current.get(CONF_ROUTES) or current.get("route_filters", [])
        current_stops = current.get(CONF_STOPS, [])
        max_deps_default = current.get(CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES)
        refresh_default = current.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL)
        expose_default = current.get(CONF_EXPOSE_TO_ASSISTANTS, DEFAULT_EXPOSE_TO_ASSISTANTS)
        gtfs_hrs_default = current.get(CONF_STATIC_GTFS_REFRESH_HOURS, DEFAULT_STATIC_GTFS_REFRESH_HOURS)
        grace_default = current.get(CONF_ALERT_GRACE_MINUTES, DEFAULT_ALERT_GRACE_MINUTES)

        schema = vol.Schema(
            {
                vol.Required(CONF_ROUTES, default=", ".join(current_routes)): str,
                vol.Optional(CONF_STOPS, default=", ".join(current_stops)): str,
                vol.Optional(CONF_MAX_DEPARTURES, default=max_deps_default): int,
                vol.Optional(CONF_REFRESH_INTERVAL, default=refresh_default): int,
                vol.Optional(CONF_EXPOSE_TO_ASSISTANTS, default=expose_default): bool,
                vol.Optional(CONF_STATIC_GTFS_REFRESH_HOURS, default=gtfs_hrs_default): int,
                vol.Optional(CONF_ALERT_GRACE_MINUTES, default=grace_default): int,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

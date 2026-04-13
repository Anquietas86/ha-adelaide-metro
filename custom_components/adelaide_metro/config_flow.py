from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_EXPOSE_TO_ASSISTANTS,
    CONF_MAX_DEPARTURES,
    CONF_REFRESH_INTERVAL,
    CONF_ROUTE_FILTERS,
    CONF_STOPS,
    DEFAULT_EXPOSE_TO_ASSISTANTS,
    DEFAULT_MAX_DEPARTURES,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
)


class AdelaideMetroConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return AdelaideMetroOptionsFlowHandler()

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            stops = [s.strip() for s in user_input[CONF_STOPS].split(",") if s.strip()]
            routes = [r.strip() for r in user_input.get(CONF_ROUTE_FILTERS, "").split(",") if r.strip()]

            if not stops:
                errors[CONF_STOPS] = "no_stops"
            elif user_input[CONF_MAX_DEPARTURES] < 1:
                errors[CONF_MAX_DEPARTURES] = "invalid_max_departures"
            elif user_input[CONF_REFRESH_INTERVAL] < 15:
                errors[CONF_REFRESH_INTERVAL] = "invalid_refresh_interval"
            else:
                await self.async_set_unique_id("|".join(sorted(stops)))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Adelaide Metro Realtime",
                    data={
                        CONF_STOPS: stops,
                        CONF_ROUTE_FILTERS: routes,
                        CONF_MAX_DEPARTURES: user_input[CONF_MAX_DEPARTURES],
                        CONF_REFRESH_INTERVAL: user_input[CONF_REFRESH_INTERVAL],
                        CONF_EXPOSE_TO_ASSISTANTS: user_input.get(CONF_EXPOSE_TO_ASSISTANTS, DEFAULT_EXPOSE_TO_ASSISTANTS),
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_STOPS): str,
                vol.Optional(CONF_ROUTE_FILTERS, default=""): str,
                vol.Optional(CONF_MAX_DEPARTURES, default=DEFAULT_MAX_DEPARTURES): int,
                vol.Optional(CONF_REFRESH_INTERVAL, default=DEFAULT_REFRESH_INTERVAL): int,
                vol.Optional(CONF_EXPOSE_TO_ASSISTANTS, default=DEFAULT_EXPOSE_TO_ASSISTANTS): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class AdelaideMetroOptionsFlowHandler(config_entries.OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            stops = [s.strip() for s in user_input[CONF_STOPS].split(",") if s.strip()]
            routes = [r.strip() for r in user_input.get(CONF_ROUTE_FILTERS, "").split(",") if r.strip()]

            if not stops:
                errors[CONF_STOPS] = "no_stops"
            elif user_input[CONF_MAX_DEPARTURES] < 1:
                errors[CONF_MAX_DEPARTURES] = "invalid_max_departures"
            elif user_input[CONF_REFRESH_INTERVAL] < 15:
                errors[CONF_REFRESH_INTERVAL] = "invalid_refresh_interval"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_STOPS: stops,
                        CONF_ROUTE_FILTERS: routes,
                        CONF_MAX_DEPARTURES: user_input[CONF_MAX_DEPARTURES],
                        CONF_REFRESH_INTERVAL: user_input[CONF_REFRESH_INTERVAL],
                        CONF_EXPOSE_TO_ASSISTANTS: user_input.get(CONF_EXPOSE_TO_ASSISTANTS, DEFAULT_EXPOSE_TO_ASSISTANTS),
                    },
                )

        current = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(CONF_STOPS, default=", ".join(current.get(CONF_STOPS, []))): str,
                vol.Optional(CONF_ROUTE_FILTERS, default=", ".join(current.get(CONF_ROUTE_FILTERS, []))): str,
                vol.Optional(CONF_MAX_DEPARTURES, default=current.get(CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES)): int,
                vol.Optional(CONF_REFRESH_INTERVAL, default=current.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL)): int,
                vol.Optional(CONF_EXPOSE_TO_ASSISTANTS, default=current.get(CONF_EXPOSE_TO_ASSISTANTS, DEFAULT_EXPOSE_TO_ASSISTANTS)): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

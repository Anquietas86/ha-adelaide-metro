from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_MAX_DEPARTURES,
    CONF_REFRESH_INTERVAL,
    CONF_ROUTE_FILTERS,
    CONF_STOPS,
    DEFAULT_MAX_DEPARTURES,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
)


class AdelaideMetroConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

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
                    title=f"Adelaide Metro ({', '.join(stops[:2])}{'...' if len(stops) > 2 else ''})",
                    data={
                        CONF_STOPS: stops,
                        CONF_ROUTE_FILTERS: routes,
                        CONF_MAX_DEPARTURES: user_input[CONF_MAX_DEPARTURES],
                        CONF_REFRESH_INTERVAL: user_input[CONF_REFRESH_INTERVAL],
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_STOPS): str,
                vol.Optional(CONF_ROUTE_FILTERS, default=""): str,
                vol.Optional(CONF_MAX_DEPARTURES, default=DEFAULT_MAX_DEPARTURES): int,
                vol.Optional(CONF_REFRESH_INTERVAL, default=DEFAULT_REFRESH_INTERVAL): int,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

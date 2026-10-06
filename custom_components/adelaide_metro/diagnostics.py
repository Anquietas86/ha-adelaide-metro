"""Diagnostics download for bug reports."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}
    departures = data.get("departures", {})
    last_fetched = await hass.async_add_executor_job(lambda: coordinator.gtfs_cache.last_fetched)

    return {
        # No credentials or personal data are stored, so nothing needs redacting
        "entry": {"data": dict(entry.data), "options": dict(entry.options)},
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": repr(coordinator.last_exception) if coordinator.last_exception else None,
            "update_interval_s": coordinator.update_interval.total_seconds() if coordinator.update_interval else None,
            "routes": sorted(coordinator.routes),
            "missing_routes": sorted(coordinator.missing_routes),
            "missing_stops": sorted(coordinator.missing_stops),
            "monitored_stop_count": len(coordinator.stops),
            "discovered_stop_count": coordinator.discovered_stop_count,
        },
        "static_gtfs": {
            "last_fetched": last_fetched,
            "last_parsed": (
                coordinator._last_static_gtfs_refresh.isoformat()
                if coordinator._last_static_gtfs_refresh
                else None
            ),
            "stops": len(coordinator.stop_index),
            "routes": len(coordinator.route_index),
            "trips": len(coordinator.trip_index),
            "services": len(coordinator.calendar.weekly),
            "timetabled_stops": len(coordinator.schedules),
        },
        "realtime": {
            "alerts": len(data.get("alerts", [])),
            "relevant_alerts": len(data.get("relevant_alerts", [])),
            "vehicles": len(data.get("vehicles", [])),
            "relevant_vehicles": len(data.get("relevant_vehicles", [])),
            "cancelled_trips_by_route": data.get("cancelled_trips_by_route", {}),
            "stops_with_departures": sum(1 for deps in departures.values() if deps),
            "realtime_departures": sum(1 for deps in departures.values() for d in deps if d.get("realtime")),
            "scheduled_departures": sum(1 for deps in departures.values() for d in deps if not d.get("realtime")),
            "sample_departures": {stop_id: deps[:2] for stop_id, deps in list(departures.items())[:3]},
        },
    }

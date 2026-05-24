DOMAIN = "adelaide_metro"
PLATFORMS = ["sensor"]

CONF_STOPS = "stops"
CONF_ROUTE_FILTERS = "route_filters"
CONF_MAX_DEPARTURES = "max_departures"
CONF_REFRESH_INTERVAL = "refresh_interval"
CONF_EXPOSE_TO_ASSISTANTS = "expose_to_assistants"

CONF_STATIC_GTFS_REFRESH_HOURS = "static_gtfs_refresh_hours"

DEFAULT_REFRESH_INTERVAL = 60
DEFAULT_MAX_DEPARTURES = 5
DEFAULT_EXPOSE_TO_ASSISTANTS = True
DEFAULT_STATIC_GTFS_REFRESH_HOURS = 24

REALTIME_BASE_URL = "https://gtfs.adelaidemetro.com.au/v1/realtime"
STATIC_GTFS_URL = "https://gtfs.adelaidemetro.com.au/v1/static/latest/google_transit.zip"
TRIP_UPDATES_URL = f"{REALTIME_BASE_URL}/trip_updates"
SERVICE_ALERTS_URL = f"{REALTIME_BASE_URL}/service_alerts"
VEHICLE_POSITIONS_URL = f"{REALTIME_BASE_URL}/vehicle_positions"

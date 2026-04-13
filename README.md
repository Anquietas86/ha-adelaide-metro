# Adelaide Metro

A HACS-compatible Home Assistant custom integration for Adelaide Metro realtime departures.

## Planned features

- Stop-based realtime departure sensors
- Static GTFS stop enrichment
- Route enrichment
- Service alerts
- Vehicle positions

## Current scope

Initial scaffold for:
- config flow
- static GTFS stop loading
- realtime GTFS trip updates
- next departure sensor per configured stop

## Data sources

- Realtime GTFS-RT: `https://gtfs.adelaidemetro.com.au/v1/realtime/trip_updates`
- Static GTFS: `https://gtfs.adelaidemetro.com.au/v1/static/latest/google_transit.zip`

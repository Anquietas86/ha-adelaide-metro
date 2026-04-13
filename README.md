# Adelaide Metro Realtime

![Adelaide Metro logo](assets/logo.jpg)

A HACS-compatible Home Assistant custom integration for Adelaide Metro realtime public transport data.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Release](https://img.shields.io/github/v/release/Anquietas86/ha-adelaide-metro)](https://github.com/Anquietas86/ha-adelaide-metro/releases)

## Features

### Realtime departures
- Stop-based realtime departure monitoring using Adelaide Metro GTFS Realtime Trip Updates
- Per-stop sensors:
  - **Next departure** — minutes until the next service, suitable for dashboards and automations
  - **Upcoming departures** — count of upcoming services in the next period
- Direction-aware naming using trip headsigns
  - Example: `Seaford Meadows Railway Station (City-bound)`
  - Example: `Seaford Meadows Railway Station (Seaford-bound)`

### Static GTFS enrichment
- Pulls the latest Adelaide Metro static GTFS bundle on startup
- Enriches entities with stop names, stop codes, coordinates, route names and trip headsigns from:
  - `stops.txt`
  - `routes.txt`
  - `trips.txt`

### Service alerts
- Polls Adelaide Metro GTFS Realtime Service Alerts
- Provides:
  - a **Service Alerts summary sensor** showing total active alert count
  - **separate alert entities** for alerts relevant to configured stops/routes only
- Alert entities are exposed to Home Assistant Assist by default, since Assist does not reliably read attributes
- Relevant alert matching:
  - matches configured stop IDs
  - matches explicit route filters if set
  - falls back to routes actively seen in departure data for configured stops

### Assistant exposure
- New entities are automatically exposed to Assist and Google Assistant by default
- Can be toggled in the options flow

## Installation

### HACS (recommended)
1. In HACS, go to **Integrations → Custom repositories**
2. Add `https://github.com/Anquietas86/ha-adelaide-metro` as an **Integration**
3. Install **Adelaide Metro Realtime**
4. Restart Home Assistant
5. Go to **Settings → Devices & Services → Add Integration**
6. Search for **Adelaide Metro Realtime**

### Manual
Copy `custom_components/adelaide_metro/` to your Home Assistant `config/custom_components/` directory and restart.

## Configuration

During setup you will be asked for:

| Field | Description | Default |
|-------|-------------|---------|
| Stop IDs | Comma separated list of stop IDs to monitor | required |
| Route filters | Comma separated route IDs for alert filtering (optional) | none |
| Maximum departures | Max upcoming services to track per stop | 5 |
| Refresh interval | Seconds between realtime data refreshes | 60 |
| Expose to assistants | Auto-expose entities to Assist and Google Assistant | on |

All settings can be edited after setup via **Settings → Devices & Services → Adelaide Metro Realtime → Configure**.

## Finding stop IDs

Stop IDs are numeric identifiers from the Adelaide Metro static GTFS feed.

Some useful examples:

| Stop | Stop ID | Notes |
|------|---------|-------|
| Seaford Meadows Railway Station (City-bound) | 101588 | |
| Seaford Meadows Railway Station (Seaford-bound) | 101587 | |

For stations with multiple platforms/directions, add the individual stop IDs per platform — **not** the parent station ID.

To find stop IDs:
- Download the latest static GTFS from `https://gtfs.adelaidemetro.com.au/v1/static/latest/google_transit.zip`
- Open `stops.txt` and search for your station

## Finding route IDs

Route IDs appear in the trip updates realtime feed. Some examples:

| Route | Route ID |
|-------|----------|
| Seaford line | SEAFRD |
| Glenelg tram | GLNELG |

Adding route filters improves service alert matching — without them, alerts are matched against routes seen in departure data for your configured stops.

## Entity types

### Per configured stop
- `Next departure` — minutes until next service
- `Upcoming departures` — count of next services

### Per integration (Service Alerts device)
- `Service alerts` — total active alert count in the network feed
- One entity per relevant active alert

## Data sources

| Feed | URL |
|------|-----|
| Trip updates | `https://gtfs.adelaidemetro.com.au/v1/realtime/trip_updates` |
| Service alerts | `https://gtfs.adelaidemetro.com.au/v1/realtime/service_alerts` |
| Vehicle positions | `https://gtfs.adelaidemetro.com.au/v1/realtime/vehicle_positions` |
| Static GTFS | `https://gtfs.adelaidemetro.com.au/v1/static/latest/google_transit.zip` |
| Proto definition | `https://gtfs.adelaidemetro.com.au/v1/realtime/adelaidemetro_gtfsr.proto` |

## Adelaide Metro custom proto extension

Adelaide Metro uses a custom protobuf extension on `VehicleDescriptor` (extension id `1999`, namespace `transit_realtime.tfnsw_vehicle_descriptor`) with fields:
- `air_conditioned` (default true)
- `wheelchair_accessible` (int32, 0 or 1)

These are not yet surfaced as entity attributes but are planned.

## Known limitations
- Stop IDs must be entered manually — a searchable stop picker is planned
- Static GTFS is only fetched once per HA restart
- Vehicle positions are not yet exposed as entities
- Adelaide Metro custom vehicle extension (wheelchair, aircon) not yet parsed

## Planned improvements
- Searchable stop picker in config flow
- Vehicle positions as entities or map data
- Parse and expose wheelchair accessibility and air conditioning from custom proto extension
- Improved alert lifecycle (auto-cleanup of cleared alerts)
- Periodic static GTFS refresh without restart

## Current status
v0.1.0 — functional for stop departures and relevant service alerts. Early-stage but usable.

## Contributing
Pull requests welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) if present.

## License
Apache 2.0

# Adelaide Metro

![Adelaide Metro logo](assets/logo.jpg)

A HACS-compatible Home Assistant custom integration for Adelaide Metro realtime public transport data.

## Current features

### Realtime departures
- Stop-based realtime departure monitoring using Adelaide Metro GTFS Realtime Trip Updates
- Per-stop sensors for:
  - **Next departure**
  - **Upcoming departures**
- Direction-aware naming for stations where possible, using trip headsigns
  - Example: `Seaford Meadows Railway Station (City-bound)`
  - Example: `Seaford Meadows Railway Station (Seaford-bound)`

### Static GTFS enrichment
- Pulls the latest Adelaide Metro static GTFS bundle
- Enriches entities with data from:
  - `stops.txt`
  - `routes.txt`
  - `trips.txt`
- Uses static GTFS for:
  - stop names
  - stop codes
  - coordinates
  - route names
  - trip headsigns

### Service alerts
- Polls Adelaide Metro GTFS Realtime Service Alerts
- Provides:
  - a **summary service alerts sensor**
  - **separate alert entities** for alerts relevant to the configured stops/routes
- Designed to work better with Home Assistant Assist, since Assist does not reliably use large attribute blobs

## Current entity types

### Per configured stop
- `Next departure`
- `Upcoming departures`

### Per integration
- `Service alerts` summary sensor
- one entity per relevant active alert

## Data sources

### Realtime GTFS Realtime
- Trip updates: `https://gtfs.adelaidemetro.com.au/v1/realtime/trip_updates`
- Vehicle positions: `https://gtfs.adelaidemetro.com.au/v1/realtime/vehicle_positions`
- Service alerts: `https://gtfs.adelaidemetro.com.au/v1/realtime/service_alerts`
- Adelaide Metro GTFS realtime proto: `https://gtfs.adelaidemetro.com.au/v1/realtime/adelaidemetro_gtfsr.proto`

### Static GTFS
- `https://gtfs.adelaidemetro.com.au/v1/static/latest/google_transit.zip`

## Installation

### HACS
1. Add this repository to HACS as a **custom repository** of type **Integration**.
2. Install **Adelaide Metro**.
3. Restart Home Assistant.
4. Go to **Settings -> Devices & Services**.
5. Add the **Adelaide Metro** integration.
6. Enter one or more stop IDs.
7. Optionally set route filters, max departures, and refresh interval.

## Configuration notes
- The current config flow expects **stop IDs**, not parent station IDs.
- For stations with multiple platforms/directions, use the actual stop/platform IDs.
- Example for Seaford Meadows Railway Station:
  - `101587`
  - `101588`

## Example use cases
- Show the next train departure from a chosen station on a dashboard
- Surface relevant disruption alerts for configured stops
- Expose transport status to Home Assistant Assist
- Build commute automations around departure timing and alerts

## Nice-to-have / future improvements
- Config flow stop picker with searchable multi-select instead of raw stop IDs
- Better route and stop selection UX
- Vehicle positions as entities or map-friendly data
- Parse Adelaide Metro custom vehicle extension data
  - wheelchair accessibility
  - air conditioning
- More polished alert presentation
- Additional Home Assistant testing and release polish

## Current status
This integration is already usable for basic stop departures and relevant service alerts, but it is still early-stage and needs more polish, testing, and feature refinement before being considered a mature release.

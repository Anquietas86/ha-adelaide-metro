"""Static timetable helpers: service calendars and scheduled departures."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo

# A realtime trip with no start_date is assumed to be the run within this window
UNDATED_EXCLUDE_WINDOW = timedelta(hours=12)

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def parse_gtfs_time(value: str) -> int | None:
    """Parse a GTFS HH:MM:SS time (hours may exceed 24) into seconds."""
    try:
        h, m, s = value.strip().split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except (AttributeError, ValueError):
        return None


def parse_gtfs_date(value: str) -> date | None:
    try:
        value = value.strip()
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except (AttributeError, ValueError):
        return None


@dataclass
class ServiceCalendar:
    """calendar.txt + calendar_dates.txt, answering "which services run on this date?"."""

    # service_id -> (weekday flags Mon..Sun, start_date, end_date)
    weekly: dict[str, tuple[tuple[bool, ...], date, date]] = field(default_factory=dict)
    # date -> {service_id: exception_type} (1 = added, 2 = removed)
    exceptions: dict[date, dict[str, int]] = field(default_factory=dict)
    _cache: dict[date, frozenset[str]] = field(default_factory=dict, repr=False)

    def active_services(self, day: date) -> frozenset[str]:
        if day in self._cache:
            return self._cache[day]
        active = {
            service_id
            for service_id, (days, start, end) in self.weekly.items()
            if start <= day <= end and days[day.weekday()]
        }
        for service_id, exception_type in self.exceptions.get(day, {}).items():
            if exception_type == 1:
                active.add(service_id)
            elif exception_type == 2:
                active.discard(service_id)
        result = frozenset(active)
        if len(self._cache) > 14:
            self._cache.clear()
        self._cache[day] = result
        return result


def service_day_start(day: date, tz: tzinfo) -> datetime:
    """GTFS times are measured from "noon minus 12h", which handles DST days correctly."""
    return datetime.combine(day, time(12), tzinfo=tz) - timedelta(hours=12)


def scheduled_departures(
    stop_schedule: list[tuple[int, str]],
    trip_services: dict[str, str],
    calendar: ServiceCalendar,
    now: datetime,
    tz: tzinfo,
    limit: int,
    exclude: dict[str, date | None] | None = None,
) -> list[tuple[int, str]]:
    """Return up to ``limit`` upcoming (unix_ts, trip_id) departures from the static timetable.

    ``stop_schedule`` is a list of (seconds since service-day start, trip_id)
    sorted by seconds. Yesterday's service day is included because GTFS
    times can run past 24:00.

    ``exclude`` maps trip_ids the realtime feed already covers to their
    service date. With no date, only runs in the next ``UNDATED_EXCLUDE_WINDOW``
    are skipped, so tomorrow's run of the same trip still shows.
    """
    exclude = exclude or {}
    if not stop_schedule or limit <= 0:
        return []
    now_ts = now.timestamp()
    today = now.astimezone(tz).date()
    results: list[tuple[int, str]] = []
    for day in (today - timedelta(days=1), today, today + timedelta(days=1)):
        active = calendar.active_services(day)
        if not active:
            continue
        base = service_day_start(day, tz).timestamp()
        start = bisect_left(stop_schedule, (int(now_ts - base), ""))
        found = 0
        # Each day's slice is ascending, so its first `limit` matches are all we need
        for secs, trip_id in stop_schedule[start:]:
            if trip_services.get(trip_id) not in active:
                continue
            ts = int(base + secs)
            if trip_id in exclude:
                service_date = exclude[trip_id]
                if service_date == day or (
                    service_date is None and ts - now_ts < UNDATED_EXCLUDE_WINDOW.total_seconds()
                ):
                    continue
            results.append((ts, trip_id))
            found += 1
            if found >= limit:
                break
    results.sort()
    return results[:limit]

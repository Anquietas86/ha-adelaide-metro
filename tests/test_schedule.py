"""Timetable calendar and scheduled-departure tests."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.adelaide_metro.schedule import (
    ServiceCalendar,
    parse_gtfs_time,
    scheduled_departures,
    service_day_start,
)

TZ = ZoneInfo("Australia/Adelaide")
WEEKDAYS_ONLY = (True, True, True, True, True, False, False)


def _calendar() -> ServiceCalendar:
    cal = ServiceCalendar()
    cal.weekly["WK"] = (WEEKDAYS_ONLY, date(2026, 1, 1), date(2026, 12, 31))
    cal.weekly["SAT"] = ((False,) * 5 + (True, False), date(2026, 1, 1), date(2026, 12, 31))
    return cal


def test_parse_time_past_midnight() -> None:
    assert parse_gtfs_time("25:10:00") == 25 * 3600 + 600
    assert parse_gtfs_time("bad") is None


def test_weekly_and_exceptions() -> None:
    cal = _calendar()
    monday, saturday = date(2026, 10, 5), date(2026, 10, 10)
    assert cal.active_services(monday) == {"WK"}
    assert cal.active_services(saturday) == {"SAT"}
    # Public holiday: weekday service replaced by Saturday timetable
    holiday = date(2026, 10, 6)
    cal.exceptions[holiday] = {"WK": 2, "SAT": 1}
    cal._cache.clear()
    assert cal.active_services(holiday) == {"SAT"}


def test_scheduled_departures_orders_and_filters() -> None:
    cal = _calendar()
    schedule = sorted([(8 * 3600, "A"), (9 * 3600, "B"), (10 * 3600, "C"), (9 * 3600, "S")])
    services = {"A": "WK", "B": "WK", "C": "WK", "S": "SAT"}
    now = datetime(2026, 10, 5, 8, 30, tzinfo=TZ)  # Monday
    # B is covered by the live feed (no start_date), so only today's run is hidden
    result = scheduled_departures(schedule, services, cal, now, TZ, 5, exclude={"B": None})
    assert [trip for _, trip in result] == ["C", "A", "B", "C"]
    assert result[0][0] == int(datetime(2026, 10, 5, 10, 0, tzinfo=TZ).timestamp())
    assert result[1][0] == int(datetime(2026, 10, 6, 8, 0, tzinfo=TZ).timestamp())


def test_dated_exclusion_only_hides_that_day() -> None:
    cal = _calendar()
    schedule = [(9 * 3600, "B")]
    now = datetime(2026, 10, 5, 8, 30, tzinfo=TZ)
    result = scheduled_departures(schedule, {"B": "WK"}, cal, now, TZ, 2, exclude={"B": date(2026, 10, 6)})
    # Tuesday (excluded) is skipped; the lookahead only spans yesterday to tomorrow
    assert [datetime.fromtimestamp(ts, TZ).date() for ts, _ in result] == [date(2026, 10, 5)]


def test_after_midnight_trip_belongs_to_previous_service_day() -> None:
    cal = _calendar()
    # Friday's 25:00 trip runs at 01:00 on Saturday, when no WK service is active
    schedule = [(25 * 3600, "LATE")]
    now = datetime(2026, 10, 10, 0, 30, tzinfo=TZ)
    result = scheduled_departures(schedule, {"LATE": "WK"}, cal, now, TZ, 3)
    assert result == [(int(datetime(2026, 10, 10, 1, 0, tzinfo=TZ).timestamp()), "LATE")]


def test_service_day_start_on_dst_change() -> None:
    # Adelaide moves clocks forward on 2026-10-04; times count from noon minus 12h
    start = service_day_start(date(2026, 10, 4), TZ)
    noon = datetime(2026, 10, 4, 12, 0, tzinfo=TZ)
    assert noon - start == timedelta(hours=12)

"""Explicit timezone instructions take precedence over public calendar defaults."""

import pytest

from recommit.components.calendar_binding import (
    DEFAULT_TZ,
    _calendar_timezone,
    _fill_event_time_fields,
)


@pytest.mark.parametrize("zone", ["Asia/Shanghai", "Europe/Paris", "UTC"])
def test_explicit_zone_overrides_calendar_default(zone):
    args = {}
    _fill_event_time_fields(
        args,
        task=f"September 25, 2026 from 9:00 am - 10:00 am in {zone}",
        consumed={},
        default_timezone="Asia/Tokyo",
    )
    assert args["start"] == {"dateTime": "2026-09-25T09:00:00", "timeZone": zone}
    assert args["end"] == {"dateTime": "2026-09-25T10:00:00", "timeZone": zone}


@pytest.mark.parametrize("target", ["team-calendar", "Research"])
def test_public_calendar_timezone_by_id_or_name(target):
    seed = {"calendars": [{"id": "team-calendar", "summary": "Research", "time_zone": "UTC"}]}
    args = {"calendarId": target}
    zone = _calendar_timezone(args, seed)
    _fill_event_time_fields(
        args, task="September 25, 2026 from 9:00 am - 10:00 am", consumed={}, default_timezone=zone
    )
    assert args["start"]["timeZone"] == args["end"]["timeZone"] == "UTC"


def test_primary_calendar_uses_public_self_identity():
    seed = {
        "calendar_users": [{"id": "person", "email": "person@example.org", "self": True}],
        "calendars": [
            {"id": "other", "owner_id": "someone", "time_zone": "UTC"},
            {"id": "mine", "owner_id": "person", "time_zone": "Asia/Tokyo"},
        ],
    }
    assert _calendar_timezone({"calendarId": "primary"}, seed) == "Asia/Tokyo"
    assert _calendar_timezone({"calendarId": "unknown"}, seed) == DEFAULT_TZ


def test_public_user_timezone_precedes_target_calendar_timezone():
    seed = {
        "calendar_users": [{"id": "person", "self": True}],
        "calendar_settings": [
            {"user_id": "other", "setting_id": "timezone", "value": "Europe/Paris"},
            {"user_id": "person", "setting_id": "timezone", "value": "Asia/Tokyo"},
        ],
        "calendars": [{"id": "shared", "time_zone": "UTC"}],
    }
    assert _calendar_timezone({"calendarId": "shared"}, seed) == "Asia/Tokyo"
    args = {}
    _fill_event_time_fields(
        args,
        task="September 25, 2026 from 9:00 am - 10:00 am in Europe/Paris",
        consumed={},
        default_timezone=_calendar_timezone({"calendarId": "shared"}, seed),
    )
    assert args["start"]["timeZone"] == "Europe/Paris"


def test_multiple_explicit_zones_are_not_arbitrarily_assigned():
    args = {}
    _fill_event_time_fields(
        args,
        task="September 25, 2026 from 9:00 am - 10:00 am; convert Asia/Tokyo to Europe/Paris",
        consumed={},
    )
    assert not args


def test_single_explicit_zone_applies_to_multiple_iso_events():
    consumed = {}
    task = "2026-09-25T09:00:00 2026-09-25T10:00:00 2026-09-26T09:00:00 2026-09-26T10:00:00 UTC"
    for day in (25, 26):
        args = {}
        _fill_event_time_fields(args, task=task, consumed=consumed)
        assert args["start"] == {"dateTime": f"2026-09-{day}T09:00:00", "timeZone": "UTC"}
        assert args["end"]["timeZone"] == "UTC"


def test_no_public_timezone_keeps_existing_environment_fallback():
    args = {}
    _fill_event_time_fields(args, task="September 25, 2026 from 9:00 am - 10:00 am", consumed={})
    assert args["start"]["timeZone"] == args["end"]["timeZone"] == DEFAULT_TZ

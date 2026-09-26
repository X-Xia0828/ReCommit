"""Calendar writes and reads must bind the same public resource identity."""

import copy
import json

from recommit.components.calendar_binding import (
    _normalize_calendar_identifiers,
    bind_calendar_plan,
)
from recommit.components.calendar_simulator import execute_call
from recommit.components.llm_plan_parse import bind_llm_plan


def test_subscription_and_update_target_one_calendar_after_model_merge():
    seed = {"calendars": [{"id": "cal_shared", "summary": "Shared Events", "time_zone": "UTC"}]}
    skeleton = [
        {"tool": "calendarList.insert", "arguments": {"id": "[M]"}},
        {"tool": "calendarList.update", "arguments": {"calendarId": "[M]", "hidden": "[M]"}},
    ]
    task = "Subscribe to Shared Events, use color ID 6, and hide it from my list."
    bound = bind_calendar_plan(skeleton, task=task, seed=seed, ids_only=True)
    assert bound[0]["arguments"]["id"] == "cal_shared"
    raw = json.dumps(
        {
            "fills": [
                {"i": 0, "arguments": {"id": "cal_shared", "colorId": "6"}},
                {"i": 1, "arguments": {"calendarId": "cal_shared", "hidden": True}},
            ]
        }
    )
    calls, parsed = bind_llm_plan(raw, bound, binder="typed")
    assert parsed
    state = copy.deepcopy(seed)
    assert all(execute_call(state, call)["ok"] for call in calls)
    assert len(state["calendars"]) == 1
    assert len(state["calendar_list_entries"]) == 1
    assert state["calendar_list_entries"][0]["calendar_id"] == "cal_shared"
    assert state["calendar_list_entries"][0]["color_id"] == "6"
    assert state["calendar_list_entries"][0]["hidden"] is True
    assert skeleton[0]["arguments"]["id"] == "[M]"


def test_duplicate_names_remain_editable_and_model_id_survives():
    seed = {"calendars": [{"id": "one", "summary": "Shared"}, {"id": "two", "summary": "Shared"}]}
    plan = [{"tool": "calendarList.insert", "arguments": {"id": "Shared"}}]
    bound = _normalize_calendar_identifiers(plan, seed)
    assert bound[0]["arguments"]["id"] == "[M]"
    calls, parsed = bind_llm_plan('{"fills":[{"i":0,"arguments":{"id":"two"}}]}', bound)
    assert parsed and calls[0]["arguments"]["id"] == "two"


def test_existing_ids_external_ids_and_references_are_preserved():
    seed = {"calendars": [{"id": "one", "summary": "Original"}, {"id": "two", "summary": "one"}]}
    for value in ("one", "external@example.org", "$last_calendar", "primary"):
        plan = [{"tool": "calendarList.insert", "arguments": {"id": value}}]
        assert _normalize_calendar_identifiers(plan, seed)[0]["arguments"]["id"] == value


def test_read_move_and_event_targets_use_correct_catalog():
    seed = {
        "calendars": [{"id": "source", "summary": "Work"}, {"id": "target", "summary": "Home"}],
        "calendar_events": [
            {"id": "e1", "calendar_id": "source", "summary": "Lunch"},
            {"id": "e2", "calendar_id": "target", "summary": "Lunch"},
        ],
    }
    plan = [
        {"tool": "calendars.get", "arguments": {"calendarId": "Work"}},
        {
            "tool": "events.move",
            "arguments": {"calendarId": "Work", "eventId": "Lunch", "destination": "Home"},
        },
    ]
    calls = _normalize_calendar_identifiers(plan, seed)
    assert calls[0]["arguments"]["calendarId"] == "source"
    assert calls[1]["arguments"] == {
        "calendarId": "source",
        "eventId": "e1",
        "destination": "target",
    }

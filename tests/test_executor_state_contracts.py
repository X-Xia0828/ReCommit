"""Service execution checks on synthetic states."""

import copy

import pytest

from recommit.components import box_simulator as box
from recommit.components import calendar_simulator as calendar
from recommit.components import linear_simulator as linear
from recommit.components import slack_simulator as slack
from recommit.execution import step_executor, executor_fingerprint


def test_shared_entry_points_and_fingerprint():
    for service, module in (
        ("box", box),
        ("calendar", calendar),
        ("linear", linear),
        ("slack", slack),
    ):
        assert step_executor(service) is module.execute_call
    hashes = executor_fingerprint()
    assert len(hashes) == 6
    assert all(len(value) == 64 for value in hashes.values())


def call(module, state, tool, **arguments):
    return module.execute_call(state, {"tool": tool, "arguments": arguments})


def linear_state():
    return {
        "issues": [
            {
                "id": "i1",
                "title": "Old",
                "description": "Old",
                "priority": 2,
                "priorityLabel": "High",
                "teamId": "t1",
            }
        ],
        "teams": [{"id": "t1", "name": "Team", "key": "T"}],
        "workflow_states": [
            {"id": "s1", "name": "Backlog", "teamId": "t1"},
            {"id": "s2", "name": "Done", "teamId": "t1"},
        ],
    }


def test_linear_zero_and_empty_description():
    state = linear_state()
    assert call(linear, state, "issueUpdate", id="i1", priority=0, description="")["ok"]
    assert state["issues"][0]["priority"] == 0
    assert state["issues"][0]["priorityLabel"] == "No priority"
    assert state["issues"][0]["description"] == ""


def test_linear_create_state():
    state = linear_state()
    assert call(linear, state, "issueCreate", teamId="t1", title="New", stateId="s2")["ok"]
    assert state["issues"][-1]["stateId"] == "s2"


def test_invalid_state_does_not_use_last_lookup():
    state = linear_state()
    state["_scratch"] = {"last_workflow_state_id": "s2"}
    before = copy.deepcopy(state)
    assert not call(linear, state, "issueCreate", teamId="t1", title="New", stateId="missing")["ok"]
    assert state == before


@pytest.mark.parametrize("entity", ["files", "folders"])
@pytest.mark.parametrize("tags", [[], ["new"]])
def test_box_replaces_tags_and_clears_description(entity, tags):
    state = {f"box_{entity}": [{"id": "a", "name": "Item", "tags": ["old"], "description": "Old"}]}
    assert call(box, state, f"PUT /{entity}/{{id}}", id="a", tags=tags, description="")["ok"]
    assert state[f"box_{entity}"][0]["tags"] == tags
    assert state[f"box_{entity}"][0]["description"] == ""


def test_box_invalid_tags_atomic():
    state = {"box_files": [{"id": "a", "name": "Old"}]}
    before = copy.deepcopy(state)
    assert not call(box, state, "PUT /files/{id}", id="a", name="New", tags="tag")["ok"]
    assert state == before


def test_box_upload_version_and_empty_content():
    state = {"box_files": [{"id": "f", "name": "old.txt", "version_number": "2", "etag": "e"}]}
    assert call(
        box, state, "POST /files/{id}/content", file_id="f", file="", name="new.md", if_match="e"
    )["ok"]
    row = state["box_files"][0]
    assert (row["size"], row["content"], row["version_number"], row["extension"]) == (
        0,
        "",
        "3",
        "md",
    )
    before = copy.deepcopy(state)
    assert not call(box, state, "POST /files/{id}/content", id="f", file="bad", if_match="e")["ok"]
    assert state == before


def test_box_upload_requires_content():
    state = {"box_files": [{"id": "f", "name": "old.txt"}]}
    before = copy.deepcopy(state)
    assert not call(box, state, "POST /files/{id}/content", id="f", name="new.txt")["ok"]
    assert state == before


def calendar_state():
    return {
        "calendars": [{"id": "c", "summary": "Calendar"}],
        "calendar_events": [
            {
                "id": "e",
                "calendar_id": "c",
                "summary": "Old",
                "description": "Old",
                "recurrence": [],
                "transparency": "opaque",
            }
        ],
        "calendar_event_attendees": [
            {"event_id": "e", "email": "old@example.org", "response_status": "needsAction"},
            {"event_id": "other", "email": "keep@example.org"},
        ],
    }


@pytest.mark.parametrize("tool", ["events.patch", "events.update"])
def test_calendar_replace_attendees_and_event_fields(tool):
    state = calendar_state()
    args = {
        "calendarId": "c",
        "eventId": "e",
        "description": "",
        "transparency": "transparent",
        "recurrence": ["RRULE:FREQ=DAILY;COUNT=2"],
        "guestsCanModify": False,
        "attendees": [{"email": "new@example.org", "responseStatus": "accepted"}],
    }
    assert call(calendar, state, tool, **args)["ok"]
    event = state["calendar_events"][0]
    assert (
        event["description"],
        event["transparency"],
        event["recurrence"],
        event["guests_can_modify"],
    ) == ("", "transparent", args["recurrence"], False)
    rows = state["calendar_event_attendees"]
    assert len(rows) == 2
    assert rows[0]["event_id"] == "other"
    assert rows[1]["email"] == "new@example.org" and rows[1]["response_status"] == "accepted"
    assert call(calendar, state, tool, calendarId="c", eventId="e", attendees=[], recurrence=[])[
        "ok"
    ]
    assert len(state["calendar_event_attendees"]) == 1
    assert state["calendar_events"][0]["recurrence"] == []


@pytest.mark.parametrize("tool", ["events.insert", "events.import"])
def test_calendar_create_fields(tool):
    state = calendar_state()
    assert call(
        calendar,
        state,
        tool,
        calendarId="c",
        summary="New",
        transparency="transparent",
        recurrence=["RRULE:FREQ=WEEKLY"],
        attendees=[{"email": "a@example.org", "responseStatus": "declined"}],
    )["ok"]
    assert state["calendar_events"][-1]["transparency"] == "transparent"
    assert state["calendar_events"][-1]["recurrence"] == ["RRULE:FREQ=WEEKLY"]
    assert state["calendar_event_attendees"][-1]["response_status"] == "declined"


@pytest.mark.parametrize("attendees", ["a@example.org", ["a@example.org"], [{}]])
def test_invalid_attendees_atomic(attendees):
    state = calendar_state()
    before = copy.deepcopy(state)
    assert not call(
        calendar,
        state,
        "events.patch",
        calendarId="c",
        eventId="e",
        summary="New",
        attendees=attendees,
    )["ok"]
    assert state == before


def slack_state():
    return {
        "channels": [
            {"channel_id": "C1", "channel_name": "one"},
            {"channel_id": "C2", "channel_name": "two"},
        ],
        "messages": [{"message_id": "m", "channel_id": "C1", "message_text": "Old"}],
    }


@pytest.mark.parametrize("tool", ["chat.update", "chat.delete"])
@pytest.mark.parametrize("channel", ["missing", "C2"])
def test_slack_channel_scopes_message(tool, channel):
    state = slack_state()
    before = copy.deepcopy(state)
    assert not call(slack, state, tool, channel=channel, ts="m", text="New")["ok"]
    assert state == before


def test_slack_blocks_render_and_clear():
    state = slack_state()
    blocks = [
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [
                        {"type": "text", "text": "Hello", "style": {"bold": True}},
                        {"type": "user", "user_id": "U1"},
                    ],
                }
            ],
        }
    ]
    assert call(slack, state, "chat.update", channel="C1", ts="m", blocks=blocks)["ok"]
    assert state["messages"][0]["message_text"] == "*Hello*\n<@U1>"
    assert call(slack, state, "chat.update", channel="C1", ts="m", blocks=[])["ok"]
    assert state["messages"][0]["message_text"] == ""


def test_slack_section_blocks_only_post():
    state = slack_state()
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "New"}}]
    assert call(slack, state, "chat.postMessage", channel="C1", blocks=blocks)["ok"]
    assert state["messages"][-1]["message_text"] == "New"


@pytest.mark.parametrize("target", ["missing", "file_1", "Duplicate"])
def test_box_invalid_or_ambiguous_target_never_falls_back(target):
    state = {
        "box_files": [
            {"id": "file_10", "name": "Duplicate"},
            {"id": "file_11", "name": "Duplicate"},
        ],
        "_scratch": {"last_file_id": "file_10"},
    }
    before = copy.deepcopy(state)
    assert not call(box, state, "PUT /files/{id}", id=target, name="Changed")["ok"]
    assert state == before


def test_box_explicit_reference_is_supported():
    state = {
        "box_files": [{"id": "file_10", "name": "Old"}],
        "_scratch": {"last_file_id": "file_10"},
    }
    assert call(box, state, "PUT /files/{id}", id="$last_file", name="New")["ok"]
    assert state["box_files"][0]["name"] == "New"


@pytest.mark.parametrize("tool", ["events.patch", "events.delete", "events.move"])
def test_calendar_invalid_calendar_never_uses_previous(tool):
    state = calendar_state()
    state["_scratch"] = {"last_calendar_id": "c", "last_event_id": "e"}
    before = copy.deepcopy(state)
    assert not call(
        calendar, state, tool, calendarId="missing", eventId="e", destination="c", summary="New"
    )["ok"]
    assert state == before


def test_calendar_invalid_event_never_uses_previous():
    state = calendar_state()
    state["_scratch"] = {"last_event_id": "e"}
    before = copy.deepcopy(state)
    assert not call(
        calendar, state, "events.patch", calendarId="c", eventId="missing", summary="New"
    )["ok"]
    assert state == before


def test_calendar_nested_arguments_and_explicit_reference():
    state = calendar_state()
    state["_scratch"] = {"last_event_id": "e"}
    assert call(
        calendar,
        state,
        "events.patch",
        parameters={
            "path": {"calendarId": "c", "eventId": "$last_event"},
            "body": {"description": ""},
        },
    )["ok"]
    assert state["calendar_events"][0]["description"] == ""


@pytest.mark.parametrize("args", [{"stateId": "bad"}, {"assigneeId": "bad"}, {"priority": 9}])
def test_linear_invalid_update_atomic(args):
    state = linear_state()
    before = copy.deepcopy(state)
    assert not call(linear, state, "issueUpdate", id="i1", title="Changed", **args)["ok"]
    assert state == before


def test_slack_user_resolution_is_exact():
    state = {
        "users": [
            {"user_id": "U123", "real_name": "Alex One"},
            {"user_id": "U124", "real_name": "Alex Two"},
        ]
    }
    assert slack.resolve_user(state, "U12") is None
    assert slack.resolve_user(state, "Alex") is None
    assert slack.resolve_user(state, "U123") == "U123"


def test_box_public_path_parameter_precedes_generic_alias():
    state = {"box_files": [{"id": "f1", "name": "old.txt"}, {"id": "f2", "name": "other.txt"}]}
    assert call(box, state, "PUT /files/{id}", file_id="f1", id="f2", name="new.txt")["ok"]
    assert [row["name"] for row in state["box_files"]] == ["new.txt", "other.txt"]


def test_box_invalid_public_path_does_not_use_alias():
    state = {"box_files": [{"id": "f1", "name": "old.txt"}]}
    before = copy.deepcopy(state)
    assert not call(box, state, "PUT /files/{id}", file_id="bad", id="f1", name="new.txt")["ok"]
    assert state == before


@pytest.mark.parametrize(
    "tool,args",
    [
        ("GET /collections/{id}", {"collection_id": "c"}),
        ("GET /collections/{id}/items", {"collection_id": "c"}),
        ("GET /files/{id}/comments", {"file_id": "f"}),
        ("GET /files/{id}/tasks", {"file_id": "f"}),
        ("GET /hubs/{id}", {"hub_id": "h"}),
        ("GET /hub_items", {"hub_id": "h"}),
    ],
)
def test_read_dispatch_checks_target_without_expanding_observations(tool, args):
    state = {
        "box_collections": [{"id": "c", "name": "Collection"}],
        "box_files": [{"id": "f", "name": "File"}],
        "box_hubs": [{"id": "h", "title": "Hub"}],
    }
    result = call(box, state, tool, **args)
    assert result["ok"] and result["matched_id"]
    assert set(result) == {"tool", "ok", "reason", "matched_id"}
    assert not call(box, state, tool, **{key: "missing" for key in args})["ok"]


def test_linear_single_issue_dispatch():
    state = linear_state()
    assert call(linear, state, "issue", id="i1")["matched_id"] == "i1"
    assert not call(linear, state, "issue", id="missing")["ok"]

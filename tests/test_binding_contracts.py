"""Binding and execution invariants on synthetic public states."""

import copy
import json

import pytest

from recommit.components.box_binding import bind_box_plan
from recommit.components.calendar_binding import bind_calendar_plan
from recommit.components.canonical_binding import run_typed_residual
from recommit.components.linear_binding import bind_linear_plan
from recommit.components.linear_simulator import execute_call as linear_execute
from recommit.components.slack_simulator import execute_call as slack_execute
from recommit.components.typed_binder_core import EntityCatalog, seed_name_hits


def call(tool, **arguments):
    return {"tool": tool, "arguments": arguments}


def calendar_seed():
    return {
        "calendars": [{"id": "work", "summary": "Work"}, {"id": "personal", "summary": "Personal"}],
        "calendar_events": [
            {"id": "event_work", "summary": "Review", "calendar_id": "work"},
            {"id": "event_personal", "summary": "Review", "calendar_id": "personal"},
        ],
    }


@pytest.mark.parametrize("target", ["primary", "work", "external@example.org", "$last_calendar"])
def test_calendar_creation_does_not_replace_explicit_target(target):
    plan = [
        call("calendars.insert", summary="New"),
        call("events.insert", calendarId=target, summary="Appointment"),
    ]
    result = bind_calendar_plan(plan, task="Create New and add Appointment.", seed=calendar_seed())
    assert result[1]["arguments"]["calendarId"] == target


@pytest.mark.parametrize(
    "tool", ["events.get", "events.patch", "events.update", "events.delete", "events.move"]
)
def test_event_name_is_resolved_only_within_target_calendar(tool):
    result = bind_calendar_plan(
        [call(tool, calendarId="personal", eventId="[M]")],
        task="Move Review on Personal.",
        seed=calendar_seed(),
        ids_only=True,
    )
    assert result[0]["arguments"]["eventId"] == "event_personal"


def test_ambiguous_event_name_stays_editable():
    seed = calendar_seed()
    seed["calendar_events"].append(
        {"id": "event_other", "summary": "Review", "calendar_id": "personal"}
    )
    result = bind_calendar_plan(
        [call("events.patch", calendarId="personal", eventId="[M]")],
        task="Update Review on Personal.",
        seed=seed,
        ids_only=True,
    )
    assert result[0]["arguments"]["eventId"] == "[M]"


@pytest.mark.parametrize(
    "task", ["Do not hide Work; change its color to 3.", "Work is hidden; change its color to 3."]
)
def test_visibility_is_not_inferred_from_a_keyword(task):
    result = bind_calendar_plan(
        [call("calendarList.patch", calendarId="work", colorId="3")],
        task=task,
        seed=calendar_seed(),
    )
    assert "hidden" not in result[0]["arguments"]
    assert "selected" not in result[0]["arguments"]


def test_explicit_visibility_value_survives_binding():
    result = bind_calendar_plan(
        [call("calendarList.patch", calendarId="work", hidden=True)],
        task="Hide Work.",
        seed=calendar_seed(),
        ids_only=True,
    )
    assert result[0]["arguments"]["hidden"] is True


def test_catalog_matches_whole_names_and_rejects_ambiguous_names():
    catalog = EntityCatalog("teams", ("name",))
    seed = {"teams": [{"id": "a", "name": "API"}]}
    assert seed_name_hits("capital expenses", seed, catalog) == []
    assert seed_name_hits("Use the API team.", seed, catalog) == ["API"]
    seed["teams"].append({"id": "b", "name": "API"})
    assert seed_name_hits("Use the API team.", seed, catalog) == []


@pytest.mark.parametrize(
    "entity,name",
    [("folders", "Archive"), ("files", "Invoice"), ("comments", "A comment"), ("hubs", "Research")],
)
def test_box_path_parameter_uses_its_resource_type(entity, name):
    seed = {
        "box_folders": [{"id": "d", "name": "Archive"}],
        "box_files": [{"id": "f", "name": "Invoice"}],
        "box_comments": [{"id": "c", "message": "A comment"}],
        "box_hubs": [{"id": "h", "title": "Research"}],
    }
    result = bind_box_plan(
        [call(f"DELETE /{entity}/{{id}}", id="[M]")],
        task="Delete Archive, Invoice, A comment, and Research.",
        seed=seed,
        ids_only=True,
    )
    assert result[0]["arguments"]["id"] == name


@pytest.mark.parametrize("key", ["assigneeId", "assignee"])
def test_explicit_unassignment_survives_binding_and_execution(key):
    seed = {
        "issues": [
            {"id": "issue-a", "identifier": "TST-1", "title": "Task", "assigneeId": "user-a"}
        ],
        "users": [{"id": "user-a", "name": "Alice"}],
    }
    result = bind_linear_plan(
        [call("issueUpdate", issue="TST-1", **{key: None})],
        task="Unassign TST-1; notify Alice.",
        seed=seed,
    )
    assert result[0]["arguments"]["assigneeId"] is None
    assert linear_execute(seed, result[0])["ok"]
    assert seed["issues"][0]["assigneeId"] is None


@pytest.mark.parametrize("target", ["NONEXISTENT-999", "TST", "[M]", "", None])
def test_invalid_issue_target_cannot_mutate_previous_issue(target):
    seed = {
        "issues": [{"id": "issue-a", "identifier": "TST-1", "title": "Original"}],
        "_scratch": {"last_issue_id": "issue-a"},
    }
    before = copy.deepcopy(seed)
    assert not linear_execute(seed, call("issueUpdate", issue=target, title="Changed"))["ok"]
    assert seed == before


@pytest.mark.parametrize("target", ["TST-1", "issue-a", "$last_issue", "$prev_issue"])
def test_valid_issue_identity_and_explicit_dynamic_references_work(target):
    seed = {
        "issues": [{"id": "issue-a", "identifier": "TST-1", "title": "Original"}],
        "_scratch": {"last_issue_id": "issue-a"},
    }
    assert linear_execute(seed, call("issueUpdate", issue=target, title="Changed"))["ok"]
    assert seed["issues"][0]["title"] == "Changed"


def test_missing_slack_channel_does_not_create_a_message():
    seed = {"channels": [], "messages": []}
    assert not slack_execute(seed, call("chat.postMessage", channel="C_MISSING", text="Hello"))[
        "ok"
    ]
    assert seed["messages"] == []


@pytest.mark.parametrize("target", ["C_EXISTS", "general", "$last_channel"])
def test_existing_slack_channel_and_dynamic_reference_work(target):
    seed = {
        "channels": [{"channel_id": "C_EXISTS", "channel_name": "general"}],
        "messages": [],
        "_scratch": {"last_channel_id": "C_EXISTS"},
    }
    assert slack_execute(seed, call("chat.postMessage", channel=target, text="Hello"))["ok"]
    assert seed["messages"][0]["channel_id"] == "C_EXISTS"


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("issueUpdate", {"issue": "TST-1"}),
        ("events.patch", {"calendarId": "work", "eventId": "event_work"}),
        ("PUT /files/{id}", {"id": "file-a"}),
    ],
)
def test_update_requires_how_even_without_masks(tool, arguments):
    class Generator:
        calls = 0

        def generate(self, *args, **kwargs):
            self.calls += 1
            return json.dumps({"fills": [{"i": 0, "arguments": {"description": "Updated"}}]})

    generator = Generator()
    result = run_typed_residual(
        service="linear",
        expanded=[call(tool, **arguments)],
        failed_raw="",
        failed_tools=[],
        task="Change the description to Updated.",
        state_summary="",
        obligations=[],
        public_contracts={},
        generator=generator,
        sample_seed=1234,
        execution_feedback=None,
        bind_ids=lambda p: p,
        bind_full=lambda p: p,
    )
    assert generator.calls == 1
    assert result[0][0]["arguments"]["description"] == "Updated"
    assert result[3]["reason"] == "open_update_payload_requires_how"

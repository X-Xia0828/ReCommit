"""Behavioral checks for composing messages and scoping cancellation requests."""

import copy

import pytest

from recommit.components.slack_binding import bind_plan
from recommit.components.calendar_binding import bind_calendar_plan


USERS = {
    "users": [{"user_id": "u-a", "username": "Alice"}, {"user_id": "u-b", "username": "Bob"}],
    "channels": [{"channel_id": "c-a", "channel_name": "updates"}],
}


def slack(task, *, text="[M]", seed=None, ids_only=False):
    plan = [{"tool": "chat.postMessage", "arguments": {"channel": "c-a", "text": text}}]
    return bind_plan(plan, task, USERS if seed is None else seed, ids_only=ids_only)[0]


@pytest.mark.parametrize(
    "names,expected",
    [
        ("Alice", "<@u-a>"),
        ("Bob", "<@u-b>"),
        ("alice", "<@u-a>"),
        ("@Alice", "<@u-a>"),
        ("Alice and Bob", "<@u-a> <@u-b>"),
        ("Bob, Alice", "<@u-b> <@u-a>"),
        ("Alice and Alice", "<@u-a>"),
    ],
)
def test_message_mentions_follow_public_names(names, expected):
    out = slack(f"Post to #updates mentioning {names} with text 'Please review'.")
    assert out["arguments"]["text"] == expected + " Please review"
    events = out["_meta"]["provenance_events"]
    assert any(e["provenance"] == "seed_state" for e in events)
    assert any(e["provenance"] == "task_span" for e in events)


@pytest.mark.parametrize(
    "task",
    [
        "Post to #updates mentioning Unknown with text 'Hello'",
        "Post to #updates mentioning Alice and Unknown with text 'Hello'",
        "Post to #updates without mentioning Alice with text 'Hello'",
        "Post text 'Hello' and do not mention Alice.",
        "Post text 'Hello' and don't mention Alice.",
        "Post text 'Hello' and mention Alice in bold.",
        "Post text 'Hello' and mention Alice using Block Kit rich_text.",
        "Post to #updates mentioning Alice with text 'Hello' and append a link.",
    ],
)
def test_incomplete_message_bindings_remain_for_how(task):
    assert slack(task)["arguments"]["text"] == "[M]"


def test_ambiguous_name_is_not_bound_to_multiple_people():
    seed = {
        "users": [
            {"user_id": "u-a", "display_name": "Alex"},
            {"user_id": "u-b", "display_name": "Alex"},
        ]
    }
    assert (
        slack("Post to #updates mentioning Alex with text 'Hello'", seed=seed)["arguments"]["text"]
        == "[M]"
    )


def test_literal_mention_word_is_not_an_instruction():
    assert (
        slack("Post 'Do not mention the release date'.")["arguments"]["text"]
        == "Do not mention the release date"
    )


def test_concrete_content_ids_only_and_rebinding_are_preserved():
    task = "Post to #updates mentioning Alice with text 'Hello'"
    assert slack(task, text="Existing message")["arguments"]["text"] == "Existing message"
    assert slack(task, ids_only=True)["arguments"]["text"] == "[M]"
    out = slack(task)
    assert bind_plan([out], task, USERS)[0]["arguments"] == out["arguments"]
    assert (
        slack("Post to #updates mentioning Alice with text '<@u-a> Hello'")["arguments"]["text"]
        == "<@u-a> Hello"
    )


SEED = {
    "calendar_events": [
        {"id": "event-a", "summary": "Planning"},
        {"id": "event-b", "summary": "Review"},
    ]
}


def calendar(task, **updates):
    args = {"calendarId": "primary", "eventId": "event-a", "summary": "New title"}
    args.update(updates)
    plan = [{"tool": "events.patch", "arguments": args}]
    before = copy.deepcopy(plan)
    out = bind_calendar_plan(plan, task=task, seed=SEED)
    assert plan == before
    return out[0]


@pytest.mark.parametrize(
    "task",
    [
        "Do not cancel event-a.",
        "Don't cancel event-a.",
        "Never cancel event-a.",
        "Update event-a without cancelling it.",
        "Do not delete event-a.",
        "Change event-a title. Do not cancel the event.",
        "Update event-a and delete the other calendar.",
        "Cancel event-b.",
        "Please remove event-b.",
        "If the meeting is postponed, cancel event-a.",
        "Set the title to 'cancel event-a'.",
        "Cancel event-a and keep event-b.",
    ],
)
def test_negated_other_target_and_compound_requests_do_not_invent_cancellation(task):
    assert "status" not in calendar(task)["arguments"]


@pytest.mark.parametrize(
    "task",
    [
        "Cancel event-a.",
        "Please cancel event event-a.",
        "Delete the event event-a.",
        "Remove event-a!",
        "Cancel 'Planning'.",
    ],
)
def test_simple_affirmative_cancellation_is_preserved(task):
    out = calendar(task)
    assert out["arguments"]["status"] == "cancelled"
    assert any(e["key"] == "status" for e in out["_meta"]["provenance_events"])


@pytest.mark.parametrize("status", ["confirmed", "tentative", "cancelled"])
def test_explicit_status_is_not_overwritten(status):
    assert calendar("Cancel event-a.", status=status)["arguments"]["status"] == status


def test_duplicate_event_titles_do_not_establish_a_cancellation_target():
    seed = {
        "calendar_events": [{"id": name, "summary": "Planning"} for name in ("event-a", "event-b")]
    }
    plan = [{"tool": "events.patch", "arguments": {"eventId": "event-a", "summary": "New title"}}]
    out = bind_calendar_plan(plan, task="Cancel 'Planning'.", seed=seed)
    assert "status" not in out[0]["arguments"]


def test_mention_composition_survives_the_content_gate_and_failed_text():
    from recommit.components.canonical_binding import run_typed_residual

    class UnneededGenerator:
        def generate(self, *args, **kwargs):
            raise AssertionError("Explicit body and unambiguous public mention should be bound")

    task = "Post to #updates mentioning Alice with text 'Please review'."
    result, parsed, _, _, _ = run_typed_residual(
        service="slack",
        expanded=[{"tool": "chat.postMessage", "arguments": {"channel": "c-a", "text": "[M]"}}],
        failed_raw='{"calls":[{"tool":"chat.postMessage","arguments":{"channel":"c-a","text":"Wrong old text"}}]}',
        failed_tools=["chat.postMessage"],
        task=task,
        state_summary="",
        obligations=[],
        public_contracts={},
        generator=UnneededGenerator(),
        sample_seed=1234,
        execution_feedback=None,
        bind_ids=lambda p: bind_plan(p, task, USERS, ids_only=True),
        bind_full=lambda p: bind_plan(p, task, USERS),
    )
    assert parsed
    assert result[0]["arguments"]["text"] == "<@u-a> Please review"

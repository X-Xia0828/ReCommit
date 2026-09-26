"""Failed proposals are suggestions, not trusted argument bindings."""

import copy
import json

import pytest

from recommit.components.canonical_binding import run_typed_residual
from recommit.components.slack_state import realization_summary, state_summary


def realize(expanded, failed, output):
    class Generator:
        prompts = []

        def generate(self, system, prompt, *, seed):
            self.prompts.append(prompt)
            return json.dumps({"calls": output})

    generator = Generator()
    result = run_typed_residual(
        service="slack",
        expanded=expanded,
        failed_raw=json.dumps({"calls": failed}),
        failed_tools=[c["tool"] for c in failed],
        task="Complete the requested repair.",
        state_summary="{}",
        obligations=[],
        public_contracts={},
        generator=generator,
        sample_seed=1234,
        execution_feedback=None,
        bind_ids=copy.deepcopy,
        bind_full=copy.deepcopy,
    )
    return result, generator.prompts


@pytest.mark.parametrize("body", ["Wrong but concrete", "[Original message 1]", "Hello"])
def test_failed_text_cannot_lock_or_skip_how(body):
    skeleton = [{"tool": "chat.postMessage", "arguments": {"channel": "c1", "text": "[M]"}}]
    failed = [{"tool": "chat.postMessage", "arguments": {"channel": "wrong", "text": body}}]
    output = [{"tool": "chat.postMessage", "arguments": {"channel": "other", "text": "Fixed"}}]
    (calls, parsed, _, _, _), prompts = realize(skeleton, failed, output)
    assert len(prompts) == 1
    assert parsed and calls[0]["arguments"] == {"channel": "c1", "text": "Fixed"}
    assert skeleton[0]["arguments"]["text"] == "[M]"


def test_failed_id_and_blocks_are_editable_and_optional_thread_is_preserved():
    skeleton = [{"tool": "chat.postMessage", "arguments": {"channel": "[M]", "text": "[M]"}}]
    failed = [
        {
            "tool": "chat.postMessage",
            "arguments": {
                "channel": "wrong",
                "text": "old",
                "blocks": [{"type": "section"}],
                "thread_ts": "wrong-parent",
            },
        }
    ]
    args = {
        "channel": "c2",
        "text": "New body",
        "thread_ts": "m2",
        "blocks": [{"type": "rich_text", "elements": []}],
    }
    (calls, parsed, _, _, _), prompts = realize(
        skeleton, failed, [{"tool": "chat.postMessage", "arguments": args}]
    )
    assert parsed and calls[0]["arguments"] == args
    assert len(prompts) == 1


def test_nested_failed_parameters_remain_repairable():
    skeleton = [
        {"tool": "events.patch", "arguments": {"start": {"dateTime": "[M]", "timeZone": "UTC"}}}
    ]
    failed = [
        {"tool": "events.patch", "arguments": {"start": {"dateTime": "wrong", "timeZone": "bad"}}}
    ]
    output = [
        {"tool": "events.patch", "arguments": {"start": {"dateTime": "right", "timeZone": "bad"}}}
    ]
    (calls, parsed, _, _, _), _ = realize(skeleton, failed, output)
    assert parsed and calls[0]["arguments"]["start"] == {"dateTime": "right", "timeZone": "UTC"}


@pytest.mark.parametrize("channel", ["launch", "blue-room"])
def test_public_context_preserves_roles_and_named_channel_records(channel):
    seed = {
        "users": [{"user_id": "u1", "username": "Pat"}],
        "user_teams": [{"user_id": "u1", "team_id": "t1", "role": "admin"}],
        "channels": [{"channel_id": "c2", "channel_name": channel}],
        "messages": [
            {"message_id": f"noise-{i}", "channel_id": "c1", "message_text": "x" * 300}
            for i in range(90)
        ]
        + [{"message_id": "parent", "channel_id": "c2", "message_text": "Shipping questions"}],
    }
    before = state_summary(seed)
    summary = realization_summary(seed, f"Reply to Shipping questions in #{channel}", 1000)
    data = json.loads(summary)
    assert len(summary) <= 1000
    assert data["messages"][0]["id"] == "parent"
    assert data["user_teams"][0]["role"] == "admin"
    assert data["omitted_messages"] > 0
    assert state_summary(seed) == before


def test_missing_role_is_not_invented():
    data = json.loads(realization_summary({"users": [{"user_id": "u1"}]}, "List admins"))
    assert data["user_teams"] == []

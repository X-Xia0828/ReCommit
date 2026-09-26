"""Small behavioral checks; no checkpoints or benchmark data are required."""

import copy
import itertools
import math
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from recommit import Episode, ServiceContext, recover, schedule
from recommit.components.call_normalization import normalize_tool_arguments
from recommit.components.llm_plan_parse import merge_arguments
from recommit.components.support_search import inclusion_scores, kbest_inclusion_sets
from recommit.how import project
from recommit.services import simulator


def test_support_search_matches_exhaustive_enumeration():
    distributions = [
        {"a": 0.6, "b": 0.2, "c": 0.1, ".": 0.1},
        {"a": 0.1, "b": 0.25, "c": 0.5, ".": 0.15},
    ]
    scores = inclusion_scores(distributions)
    brute = []
    for flags in itertools.product((False, True), repeat=3):
        logp = sum(math.log(scores[t] if keep else 1 - scores[t]) for t, keep in zip("abc", flags))
        brute.append((logp, tuple(t for t, keep in zip("abc", flags) if keep)))
    expected = [s for _, s in sorted(brute, reverse=True)]
    actual = [r.obligations for r in kbest_inclusion_sets(distributions, 8)]
    assert actual == expected
    assert actual[:3] == [r.obligations for r in kbest_inclusion_sets(distributions, 3)]


def test_schedule_uses_seven_supports_for_thirteen_trials():
    assert schedule(3) == [("canonical", 0), ("expressive", 0), ("canonical", 1)]
    assert schedule(13)[-1] == ("canonical", 6)
    with pytest.raises(ValueError):
        schedule(0)


def test_equal_score_support_order_is_stable_across_processes():
    code = (
        "import json; from recommit.components.support_search import kbest_inclusion_sets; "
        "print(json.dumps([c.obligations for c in "
        "kbest_inclusion_sets([{'a': 0.5, 'b': 0.5}], 4)]))"
    )
    outputs = []
    for hash_seed in (1, 2, 1234):
        env = dict(os.environ, PYTHONHASHSEED=str(hash_seed))
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        outputs.append(json.loads(subprocess.check_output([sys.executable, "-c", code], env=env)))
    assert outputs == [[[], ["a"], ["a", "b"], ["b"]]] * 3


def test_calendar_write_filter_covers_watch_endpoints():
    tools = ["events.list", "events.watch", "calendarList.watch", "acl.watch", "channels.stop"]
    ctx = ServiceContext("calendar", {}, "", dict.fromkeys(tools, {}))
    calls = [{"tool": t, "arguments": {}} for t in tools]
    kept, valid = project(calls, ["events.delete"], ctx)
    assert valid and [c["tool"] for c in kept] == ["events.list"]
    assert not project([{"tool": "unknown"}], [], ctx)[1]


def test_scalar_comment_body_survives_normalization():
    assert normalize_tool_arguments({"input": {"body": "Hello"}})["body"] == "Hello"


def test_nested_fill_preserves_concrete_sibling_fields():
    skeleton = {"start": {"dateTime": "2026-01-01T09:00:00", "timeZone": "[M]"}}
    fill = {"start": {"dateTime": "2030-01-01T00:00:00", "timeZone": "UTC"}}
    result = merge_arguments(skeleton, fill)
    assert result == {"start": {"dateTime": "2026-01-01T09:00:00", "timeZone": "UTC"}}
    assert skeleton["start"]["timeZone"] == "[M]"
    assert merge_arguments(skeleton, {"start": "UTC"}) == skeleton


def test_public_episode_drops_evaluation_fields():
    row = dict(
        failure_id="x", test_id="y", question="q", answer="private", required_tools=["secret"]
    )
    assert "answer" not in vars(Episode.from_dict(row))
    assert "required_tools" not in vars(Episode.from_dict(row))


def test_no_hidden_success_stopping_or_feedback(monkeypatch):
    import recommit.engine as engine

    call = {"tool": "issueCreate", "arguments": {"title": "test"}}
    seeds = []

    def realize(episode, context, support, generator, seed):
        seeds.append(seed)
        return [copy.deepcopy(call)], True, "", {}

    monkeypatch.setattr(engine, "canonical", realize)
    monkeypatch.setattr(engine, "expressive", realize)
    executions = []

    def execute(plan):
        executions.append(plan)
        return {
            "execution": [{"ok": True}],
            "execution_all_ok": True,
            "assertion_passed": True,
            "answer": "not method visible",
        }

    result = recover(
        Episode("x", "y", "q"),
        ServiceContext("linear", {}, "", {}),
        [["issueCreate"]] * 7,
        None,
        execute,
        what_seconds=2,
    )
    assert len(result["trials"]) == len(executions) == 13
    assert seeds == [1234 + i // 2 for i in range(13)]
    assert all("assertion_passed" not in t["public_observation"] for t in result["trials"])
    assert all(t["prefix_seconds"] >= 2 for t in result["trials"])


def test_pruning_replays_kept_plan_only(monkeypatch):
    import recommit.engine as engine

    calls = [{"tool": "good", "arguments": {}}, {"tool": "bad", "arguments": {}}]
    monkeypatch.setattr(engine, "canonical", lambda *a: (copy.deepcopy(calls), True, "", {}))
    observed = []

    def execute(plan):
        observed.append(copy.deepcopy(plan))
        steps = [{"ok": c["tool"] == "good"} for c in plan]
        return {"execution": steps, "execution_all_ok": all(s["ok"] for s in steps)}

    result = recover(
        Episode("x", "y", "q"), ServiceContext("linear", {}, "", {}), [[]], None, execute, budget=1
    )
    assert observed == [calls, calls[:1]]
    assert result["trials"][0]["calls"] == calls[:1]


def test_simulator_resets_state():
    state = {"teams": [{"id": "team-a", "name": "Engineering"}], "issues": []}
    ctx = ServiceContext("linear", state, "", {})
    execute = simulator(ctx)
    plan = [{"tool": "issueCreate", "arguments": {"teamId": "team-a", "title": "Test"}}]
    assert execute(plan) == execute(plan)
    assert state["issues"] == []


def test_invalid_parse_does_not_execute(monkeypatch):
    import recommit.engine as engine

    monkeypatch.setattr(engine, "canonical", lambda *a: ([], False, "invalid", {}))

    def execute(_):
        raise AssertionError("An invalid plan must not be executed")

    out = recover(
        Episode("x", "y", "q"), ServiceContext("linear", {}, "", {}), [[]], None, execute, budget=1
    )
    assert out["trials"][0]["public_observation"] == {
        "execution": [],
        "execution_all_ok": False,
        "state_diff_counts": {},
    }


@pytest.mark.parametrize("recipient", ["Alice", "Beatrice", "Omer"])
def test_slack_recipient_tracks_request_not_report_template(recipient):
    from recommit.components.slack_binding import bind_plan

    seed = {
        "users": [
            {"user_id": "u-" + name, "username": name} for name in ("Omer", "Alice", "Beatrice")
        ]
    }
    plan = [{"tool": "conversations.open", "arguments": {"users": "[M]"}}]
    out = bind_plan(plan, f"Send the field report to {recipient}.", seed, ids_only=True)
    assert out[0]["arguments"]["users"] == "u-" + recipient
    assert out[0]["_meta"]["provenance_events"]


def test_slack_keeps_concrete_recipient_and_leaves_unknowns_masked():
    from recommit.components.slack_binding import bind_plan

    seed = {"users": [{"user_id": "u-one", "username": "Someone"}]}
    concrete = [{"tool": "conversations.open", "arguments": {"users": "u-existing"}}]
    out = bind_plan(concrete, "Send a field report to Someone.", seed)
    assert out[0]["arguments"]["users"] == "u-existing"
    masked = [{"tool": "chat.postMessage", "arguments": {"channel": "[M]", "text": "[M]"}}]
    out = bind_plan(masked, "Prepare a field report.", seed)
    assert out[0]["arguments"] == masked[0]["arguments"]


def test_slack_does_not_invent_sample_block_payloads():
    from recommit.components.slack_binding import bind_plan

    plan = [{"tool": "chat.postMessage", "arguments": {"channel": "c-one", "text": "[M]"}}]
    out = bind_plan(plan, "Post a Block Kit table.", {})
    assert out[0]["arguments"] == plan[0]["arguments"]


def test_slack_renamed_public_channel_and_literal_message():
    from recommit.components.slack_binding import bind_plan

    plan = [{"tool": "chat.postMessage", "arguments": {"channel": "[M]", "text": "[M]"}}]
    for name in ("research", "support-desk"):
        seed = {"channels": [{"channel_id": "c-target", "channel_name": name}]}
        out = bind_plan(plan, f"Post 'Meeting moved' to #{name}.", seed)
        assert out[0]["arguments"] == {"channel": "c-target", "text": "Meeting moved"}


def test_slack_count_template_is_left_for_how():
    from recommit.components.slack_binding import bind_plan

    plan = [{"tool": "chat.postMessage", "arguments": {"channel": "c-target", "text": "[M]"}}]
    out = bind_plan(plan, "Post 'Found [N] replies'.", {})
    assert out[0]["arguments"]["text"] == "[M]"

"""Model argument fills cannot cross tool identities."""

import json

from recommit.components.llm_plan_parse import bind_skeleton_from_llm


def test_partial_named_plan_binds_only_unique_matching_tools():
    skeleton = [
        {"tool": "lookup", "arguments": {"query": "[M]"}},
        {"tool": "create", "arguments": {"name": "[M]", "team": "verified"}},
        {"tool": "update", "arguments": {"name": "[M]"}},
    ]
    raw = json.dumps({"calls": [{"tool": "create", "arguments": {"name": "New", "team": "wrong"}}]})
    calls, ok = bind_skeleton_from_llm(raw, skeleton)
    assert ok
    assert [c["tool"] for c in calls] == ["lookup", "create", "update"]
    assert calls[0]["arguments"] == {"query": "[M]"}
    assert calls[1]["arguments"] == {"name": "New", "team": "verified"}
    assert calls[2]["arguments"] == {"name": "[M]"}


def test_reordered_calls_follow_identity_without_reordering_skeleton():
    skeleton = [{"tool": t, "arguments": {"value": "[M]"}} for t in ("read", "write")]
    raw = json.dumps(
        {
            "calls": [
                {"tool": "write", "arguments": {"value": "W"}},
                {"tool": "read", "arguments": {"value": "R"}},
            ]
        }
    )
    calls, ok = bind_skeleton_from_llm(raw, skeleton)
    assert ok and [c["arguments"]["value"] for c in calls] == ["R", "W"]


def test_ambiguous_repeated_tools_are_not_guessed():
    skeleton = [{"tool": "write", "arguments": {"value": "[M]"}} for _ in range(2)]
    raw = json.dumps({"calls": [{"tool": "write", "arguments": {"value": "X"}}]})
    assert bind_skeleton_from_llm(raw, skeleton) == ([], False)


def test_matching_repeated_sequence_remains_positional():
    skeleton = [{"tool": "write", "arguments": {"value": "[M]"}} for _ in range(2)]
    raw = json.dumps({"calls": [{"tool": "write", "arguments": {"value": v}} for v in ("A", "B")]})
    calls, ok = bind_skeleton_from_llm(raw, skeleton)
    assert ok and [c["arguments"]["value"] for c in calls] == ["A", "B"]


def test_unknown_tool_cannot_inject_fields_into_skeleton():
    skeleton = [{"tool": "read", "arguments": {"query": "[M]"}}]
    raw = json.dumps({"calls": [{"tool": "delete", "arguments": {"query": "all"}}]})
    assert bind_skeleton_from_llm(raw, skeleton) == ([], False)


def test_explicit_indexed_fills_keep_original_contract():
    skeleton = [{"tool": t, "arguments": {"value": "[M]"}} for t in ("read", "write")]
    raw = json.dumps({"fills": [{"i": 1, "arguments": {"value": "W"}}]})
    calls, ok = bind_skeleton_from_llm(raw, skeleton)
    assert ok and [c["arguments"]["value"] for c in calls] == ["[M]", "W"]

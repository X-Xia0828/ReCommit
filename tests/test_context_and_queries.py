"""Context-budget parity and separation of read queries from write identities."""

import copy

import pytest

from recommit.components.linear_simulator import execute_call
from recommit.components.llm_plan_filler_prompt import build_llm_fill_prompt


@pytest.mark.parametrize(
    "service,limit", [("box", 18000), ("linear", 18000), ("calendar", 18000), ("slack", 12000)]
)
def test_how_public_context_budget(service, limit):
    summary = "a" * 11990 + "VISIBLE_MIDDLE" + "b" * 6000 + "OMITTED_TAIL"
    _, prompt = build_llm_fill_prompt(
        service=service,
        task="Inspect records",
        state_summary=summary,
        failed_tools=[],
        failed_raw="",
        obligations=[],
        skeleton_plan=[],
        public_contracts={},
        execution_feedback=None,
        binder="typed",
    )
    actual = prompt.split("Visible state summary:\n", 1)[1].split(
        "\n\nFailed agent tool sequence:", 1
    )[0]
    assert actual == summary[:limit]
    assert ("VISIBLE_MIDDLE" in actual) == (service != "slack")


def seed():
    return {
        "issues": [
            {
                "id": "a",
                "identifier": "QA-1",
                "title": "Investigate login errors",
                "teamId": "team-a",
            },
            {
                "id": "b",
                "identifier": "QA-2",
                "title": "Update onboarding guide",
                "teamId": "team-b",
            },
        ],
        "comments": [],
    }


def call(tool, **arguments):
    return {"tool": tool, "arguments": arguments}


@pytest.mark.parametrize(
    "args",
    [
        {"query": "login"},
        {"query": "LOGIN"},
        {"filter": {"title": {"contains": "login"}}},
        {"filter": {"title": {"containsIgnoreCase": "LOGIN"}, "teamId": {"eq": "team-a"}}},
        {"identifier": "QA-1"},
    ],
)
def test_read_queries_resolve_public_evidence(args):
    state = seed()
    assert execute_call(state, call("issues", **args))["matched_id"] == "a"
    assert state["issues"] == seed()["issues"]
    assert execute_call(state, call("issueUpdate", issue="$last_issue", title="Updated"))["ok"]
    assert state["issues"][0]["title"] == "Updated"


@pytest.mark.parametrize("target", ["login", "QA", "QA-999", "$issueCreate.id", "[M]", None])
def test_read_match_does_not_authorize_fuzzy_or_invalid_writes(target):
    state = seed()
    execute_call(state, call("issues", query="login"))
    before = copy.deepcopy(state)
    assert not execute_call(state, call("issueUpdate", issue=target, title="Wrong"))["ok"]
    assert state == before


@pytest.mark.parametrize(
    "args",
    [
        {"query": "missing"},
        {"filter": {"unsupported": {"eq": "x"}}},
        {"filter": {"title": {"unsupported": "login"}}},
        {"filter": {"title": {"contains": "login"}, "teamId": {"eq": "team-b"}}},
        {"id": "missing", "query": "login"},
        {},
    ],
)
def test_failed_query_does_not_return_or_retain_unrelated_issue(args):
    state = seed()
    state["_scratch"] = {"last_issue_id": "b"}
    assert execute_call(state, call("issues", **args))["matched_id"] is None
    assert "last_issue_id" not in state["_scratch"]


def test_ambiguous_query_does_not_choose_first_row():
    state = seed()
    state["issues"].append({"id": "c", "title": "Another login problem"})
    assert execute_call(state, call("issues", query="login"))["matched_id"] is None


def test_comment_lookup_can_search_issue_title_without_allowing_fuzzy_comment_write():
    state = seed()
    state["comments"] = [{"id": "c", "issueId": "a", "body": "Investigating"}]
    assert execute_call(state, call("comments", query="login"))["matched_id"] == "c"
    assert not execute_call(state, call("commentCreate", issue="login", body="New"))["ok"]

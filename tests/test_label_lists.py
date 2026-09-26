import copy

import pytest

from recommit.components.linear_simulator import execute_call


def seed():
    return {
        "issues": [{"id": "i1", "title": "Issue", "labelIds": ["l1"]}],
        "issue_labels": [{"id": "l1", "name": "One"}, {"id": "l2", "name": "Two"}],
        "issue_label_issue_association": [{"issue_id": "i1", "issue_label_id": "l1"}],
    }


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"labelIds": ["l2"]}, ["l2"]),
        ({"labelIds": []}, []),
        ({"addedLabelIds": ["l2"]}, ["l1", "l2"]),
        ({"removedLabelIds": ["l1"]}, []),
        ({"addedLabelIds": ["l2"], "removedLabelIds": ["l1"]}, ["l2"]),
        ({"labelIds": ["l1"], "addedLabelIds": ["l2"], "removedLabelIds": ["l1"]}, ["l1"]),
    ],
)
def test_label_mutations(args, expected):
    state = seed()
    assert execute_call(state, {"tool": "issueUpdate", "arguments": {"id": "i1", "input": args}})[
        "ok"
    ]
    assert state["issues"][0]["labelIds"] == expected
    assert state["issue_label_issue_association"] == [
        {"issue_id": "i1", "issue_label_id": x} for x in expected
    ]


@pytest.mark.parametrize("value", [None, "l2", ["missing"], ["l2", "l2"], [7]])
def test_invalid_labels_do_not_partially_write(value):
    state = seed()
    before = copy.deepcopy(state)
    result = execute_call(
        state,
        {"tool": "issueUpdate", "arguments": {"id": "i1", "title": "Changed", "labelIds": value}},
    )
    assert not result["ok"]
    assert state == before


def test_create_label_list():
    state = seed()
    assert execute_call(
        state, {"tool": "issueCreate", "arguments": {"title": "New", "labelIds": ["l2"]}}
    )["ok"]
    issue = state["issues"][-1]
    assert issue["labelIds"] == ["l2"]
    assert {"issue_id": issue["id"], "issue_label_id": "l2"} in state[
        "issue_label_issue_association"
    ]

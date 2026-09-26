"""Linear expansion."""

from __future__ import annotations
from collections import OrderedDict
from typing import Any, Mapping, Sequence
from recommit.components.slack_graph import PrerequisiteGraph
from recommit.components.linear_graph import MUTATING_TOOLS

READ_DEFAULTS: dict[str, dict[str, Any]] = {
    "teams": {"name": "[M]"},
    "users": {"name": "[M]"},
    "issues": {"query": "[M]"},
    "issue": {"id": "[M]"},
    "workflowStates": {"name": "[M]"},
    "issueLabels": {"name": "[M]"},
    "comments": {"query": "[M]"},
}
WRITE_DEFAULTS: dict[str, dict[str, Any]] = {
    "teamCreate": {"name": "[M]"},
    "teamMembershipCreate": {"teamId": "[M]", "userIds": ["[M]"]},
    "workflowStateCreate": {"teamId": "[M]", "name": "[M]"},
    "workflowStateArchive": {"id": "[M]"},
    "issueCreate": {"team": "[M]", "title": "[M]", "priority": "[M]"},
    "issueUpdate": {"issue": "[M]"},
    "issueLabelCreate": {"name": "[M]", "teamId": "[M]"},
    "issueLabelUpdate": {"label": "[M]", "name": "[M]"},
    "issueLabelDelete": {"label": "[M]"},
    "commentCreate": {"issue": "[M]", "body": "[M]"},
    "commentUpdate": {"comment": "[M]", "body": "[M]"},
    "commentDelete": {"comment": "[M]"},
    "issueRelationCreate": {"issue": "[M]", "relatedIssue": "[M]", "type": "relates"},
}
SUPPLIER_PRIORITY: dict[str, list[str]] = {
    "team_id": ["teams", "teamCreate"],
    "user_id": ["users"],
    "issue_id": ["issues", "issue", "issueCreate"],
    "label_id": ["issueLabels", "issueLabelCreate"],
    "comment_id": ["comments", "commentCreate"],
    "workflow_state_id": ["workflowStates", "workflowStateCreate"],
}


def _choose_supplier(
    graph: PrerequisiteGraph, id_type: str, effect: str, *, include_mutating: bool = False
) -> str | None:
    candidates = list(
        graph.prerequisites_of(effect, include_mutating=include_mutating).get(id_type, ())
    )
    if not candidates:
        candidates = list(graph.suppliers_of(id_type, include_mutating=include_mutating) - {effect})
    if not candidates:
        return None
    priority = SUPPLIER_PRIORITY.get(id_type, [])
    read_candidates = [tool for tool in candidates if tool in graph.read_tools()]
    for tool in priority:
        if tool in read_candidates:
            return tool
    return sorted(read_candidates)[0] if read_candidates else None


def expand_linear_obligations(
    obligations: Sequence[Mapping[str, Any]], graph: PrerequisiteGraph
) -> list[dict[str, Any]]:
    """Turn Linear obligations into ordered calls with prerequisite reads."""
    reads_needed: OrderedDict[str, None] = OrderedDict()
    write_calls: list[dict[str, Any]] = []
    for item in obligations:
        effect = str(item.get("effect") or "")
        if effect not in MUTATING_TOOLS:
            continue
        base = dict(WRITE_DEFAULTS.get(effect, {}))
        slots = item.get("slots") if isinstance(item.get("slots"), dict) else {}
        for key, value in slots.items():
            if value not in (None, ""):
                base[key] = value
        write_calls.append({"tool": effect, "arguments": base})
        demand_types = set(graph.prerequisites_of(effect, include_mutating=False))
        if effect in {"issueCreate", "issueUpdate"}:
            demand_types.add("user_id")
        for id_type in sorted(demand_types):
            supplier = _choose_supplier(graph, id_type, effect)
            if supplier:
                reads_needed.setdefault(supplier, None)
    plan: list[dict[str, Any]] = []
    for tool in reads_needed:
        plan.append({"tool": tool, "arguments": dict(READ_DEFAULTS.get(tool, {}))})
    plan.extend(write_calls)
    return plan

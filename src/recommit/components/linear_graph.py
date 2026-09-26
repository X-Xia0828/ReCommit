"""Linear graph."""

from __future__ import annotations
import json
from pathlib import Path
from typing import Mapping
from recommit.components.slack_graph import ContractError, PrerequisiteGraph, ToolContract

TEAM_ID = "team_id"
USER_ID = "user_id"
ISSUE_ID = "issue_id"
LABEL_ID = "label_id"
COMMENT_ID = "comment_id"
WORKFLOW_STATE_ID = "workflow_state_id"
IDENTIFIER_PARAMS: Mapping[str, str] = {
    "teamId": TEAM_ID,
    "team": TEAM_ID,
    "assigneeId": USER_ID,
    "userId": USER_ID,
    "userIds": USER_ID,
    "issueId": ISSUE_ID,
    "relatedIssueId": ISSUE_ID,
    "labelId": LABEL_ID,
    "labelIds": LABEL_ID,
    "addedLabelIds": LABEL_ID,
    "stateId": WORKFLOW_STATE_ID,
    "commentId": COMMENT_ID,
}
TOOL_ID_PARAM_TYPE: Mapping[str, str] = {
    "issueUpdate": ISSUE_ID,
    "commentUpdate": COMMENT_ID,
    "commentDelete": COMMENT_ID,
    "issueLabelUpdate": LABEL_ID,
    "issueLabelDelete": LABEL_ID,
    "workflowStateArchive": WORKFLOW_STATE_ID,
}
EXTRA_REQUIRED_IDENTIFIERS: Mapping[str, frozenset[str]] = {
    "commentCreate": frozenset({ISSUE_ID}),
    "issueRelationCreate": frozenset({ISSUE_ID}),
    "teamMembershipCreate": frozenset({TEAM_ID, USER_ID}),
    "workflowStateCreate": frozenset({TEAM_ID}),
    "issueLabelCreate": frozenset(),
}
DECLARED_SUPPLIES: Mapping[str, frozenset[str]] = {
    "teams": frozenset({TEAM_ID}),
    "users": frozenset({USER_ID}),
    "issues": frozenset({ISSUE_ID}),
    "issue": frozenset({ISSUE_ID}),
    "workflowStates": frozenset({WORKFLOW_STATE_ID}),
    "issueLabels": frozenset({LABEL_ID}),
    "comments": frozenset({COMMENT_ID}),
    "teamCreate": frozenset({TEAM_ID}),
    "issueCreate": frozenset({ISSUE_ID}),
    "issueLabelCreate": frozenset({LABEL_ID}),
    "commentCreate": frozenset({COMMENT_ID}),
    "workflowStateCreate": frozenset({WORKFLOW_STATE_ID}),
}
MUTATING_TOOLS: frozenset[str] = frozenset(
    {
        "teamCreate",
        "teamMembershipCreate",
        "workflowStateCreate",
        "workflowStateArchive",
        "issueCreate",
        "issueUpdate",
        "issueLabelCreate",
        "issueLabelUpdate",
        "issueLabelDelete",
        "commentCreate",
        "commentUpdate",
        "commentDelete",
        "issueRelationCreate",
    }
)


def parse_linear_docs(docs_path: Path) -> dict[str, tuple[frozenset[str], frozenset[str]]]:
    """Read required and optional argument / field names from Linear docs."""
    payload = json.loads(docs_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not payload:
        raise ContractError(f"no tool entries in {docs_path}")
    out: dict[str, tuple[frozenset[str], frozenset[str]]] = {}
    for tool_name, entry in payload.items():
        if not isinstance(entry, dict):
            continue
        required: set[str] = set()
        optional: set[str] = set()
        arguments = entry.get("arguments") or {}
        if not isinstance(arguments, dict):
            out[str(tool_name)] = (frozenset(), frozenset())
            continue
        for param_name, spec in arguments.items():
            if not isinstance(spec, dict):
                optional.add(param_name)
                continue
            fields = spec.get("fields")
            if isinstance(fields, dict):
                for field_name, field_spec in fields.items():
                    if isinstance(field_spec, dict) and field_spec.get("required"):
                        required.add(field_name)
                    else:
                        optional.add(field_name)
                continue
            if param_name == "input":
                continue
            if spec.get("required"):
                required.add(param_name)
            else:
                optional.add(param_name)
        out[str(tool_name)] = (frozenset(required), frozenset(optional - required))
    return out


class LinearToolContract(ToolContract):
    """ToolContract with Linear's ambiguous top-level `id` typing."""

    def required_identifier_types(self) -> frozenset[str]:
        types: set[str] = set()
        for param in self.required_params:
            if param == "id" and self.name in TOOL_ID_PARAM_TYPE:
                types.add(TOOL_ID_PARAM_TYPE[self.name])
            elif param in IDENTIFIER_PARAMS:
                types.add(IDENTIFIER_PARAMS[param])
        types |= set(EXTRA_REQUIRED_IDENTIFIERS.get(self.name, frozenset()))
        return frozenset(types)

    def consumable_identifier_types(self) -> frozenset[str]:
        types: set[str] = set()
        for param in self.required_params | self.optional_params:
            if param == "id" and self.name in TOOL_ID_PARAM_TYPE:
                types.add(TOOL_ID_PARAM_TYPE[self.name])
            elif param in IDENTIFIER_PARAMS:
                types.add(IDENTIFIER_PARAMS[param])
        types |= set(EXTRA_REQUIRED_IDENTIFIERS.get(self.name, frozenset()))
        return frozenset(types)

    @property
    def is_mutating(self) -> bool:
        return self.name in MUTATING_TOOLS


def build_linear_graph(*, docs_path: Path, service: str = "linear") -> PrerequisiteGraph:
    params = parse_linear_docs(docs_path)
    unknown_mutating = sorted(MUTATING_TOOLS - set(params))
    if unknown_mutating:
        for name in unknown_mutating:
            params[name] = (frozenset({"id"}), frozenset())
    tools: dict[str, ToolContract] = {}
    for tool_name, (required, optional) in params.items():
        tools[tool_name] = LinearToolContract(
            name=tool_name,
            required_params=required,
            optional_params=optional,
            supplies=DECLARED_SUPPLIES.get(tool_name, frozenset()),
        )
    return PrerequisiteGraph(service=service, tools=tools)

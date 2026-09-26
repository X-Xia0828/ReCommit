"""Linear simulator."""

from __future__ import annotations
import copy
import json
import re
from typing import Any

AGENT_USER_ID = "2790a7ee-fde0-4537-9588-e233aa5a68d1"


def normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def text_contains_any(text: str, values: list[Any]) -> bool:
    lower = normalize(text)
    return any((normalize(value) and normalize(value) in lower for value in values))


def next_id(prefix: str, rows: list[dict[str, Any]]) -> str:
    return f"{prefix}_OFFLINE_{len(rows) + 1:04d}"


def arg_text(args: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = args.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def find_one(rows: list[dict[str, Any]], value: Any, fields: list[str]) -> dict[str, Any] | None:
    """Resolve an exact identity or unique public name; never a substring of an ID."""
    text = normalize(value)
    if not text or text.startswith("$"):
        return None
    for group in (
        [field for field in fields if field == "id"],
        [field for field in fields if field != "id"],
    ):
        matches = [row for row in rows if any(text == normalize(row.get(field)) for field in group)]
        if matches:
            return matches[0] if len(matches) == 1 else None
    return None


def resolve_team(state: dict[str, Any], value: Any) -> dict[str, Any] | None:
    row = find_one(state.get("teams", []), value, ["id", "name", "key", "displayName"])
    if row:
        return row
    if value != "$last_team":
        return None
    scratch = state.get("_scratch", {})
    team_id = scratch.get("last_team_id") if isinstance(scratch, dict) else None
    return find_one(state.get("teams", []), team_id, ["id"]) if team_id else None


def resolve_user(state: dict[str, Any], value: Any) -> dict[str, Any] | None:
    row = find_one(state.get("users", []), value, ["id", "name", "displayName", "email"])
    if row:
        return row
    if value != "$last_user":
        return None
    scratch = state.get("_scratch", {})
    user_id = scratch.get("last_user_id") if isinstance(scratch, dict) else None
    return find_one(state.get("users", []), user_id, ["id"]) if user_id else None


def resolve_issue(state: dict[str, Any], value: Any) -> dict[str, Any] | None:
    scratch = state.get("_scratch", {})
    if value in ("$last_issue", "$prev_issue") and isinstance(scratch, dict):
        created = list(scratch.get("created_issue_ids") or [])
        value = (
            created[-2]
            if value == "$prev_issue" and len(created) >= 2
            else created[-1]
            if created
            else scratch.get("last_issue_id")
        )
    text = normalize(value)
    if not text or text.startswith("$"):
        return None
    rows = state.get("issues", [])
    # IDs take precedence over display names. A miss is never an implicit
    # reference to an unrelated earlier lookup or creation.
    for fields in (("id", "identifier"), ("title", "number")):
        matches = [row for row in rows if any(text == normalize(row.get(f)) for f in fields)]
        if matches:
            return matches[0] if len(matches) == 1 else None
    return None


def query_issue(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve a unique read result without weakening mutation identity checks.

    Text queries search titles; structured filters constrain public fields.
    Ambiguous, unsupported, and empty queries never pick an arbitrary issue.
    """
    rows = state.get("issues", [])
    explicit = next(
        (args[k] for k in ("id", "identifier", "issue", "issue_id", "issueId") if k in args),
        None,
    )
    if any(k in args for k in ("id", "identifier", "issue", "issue_id", "issueId")):
        issue = resolve_issue(state, explicit)
        rows = [issue] if issue else []
    filters = args.get("filter")
    if isinstance(filters, dict):
        # Only supported predicates are evaluated; unknown filters must not
        # silently disappear and turn into an unconstrained lookup.
        for field, condition in filters.items():
            if field not in {
                "id",
                "identifier",
                "title",
                "number",
                "teamId",
                "assigneeId",
                "stateId",
            }:
                return None
            predicates = condition if isinstance(condition, dict) else {"eq": condition}
            if not predicates or not set(predicates) <= {"eq", "contains", "containsIgnoreCase"}:
                return None
            for operator, value in predicates.items():
                if not isinstance(value, (str, int)) or value == "":
                    return None
                if operator == "eq":
                    rows = [r for r in rows if str(r.get(field, "")) == str(value)]
                elif operator == "contains":
                    rows = [r for r in rows if str(value) in str(r.get(field, ""))]
                else:
                    rows = [r for r in rows if normalize(value) in normalize(r.get(field))]
    elif filters is not None and not isinstance(filters, str):
        return None
    query = arg_text(args, "query", "name", "title", "value") or (
        filters if isinstance(filters, str) else ""
    )
    if query:
        text = normalize(query)
        if text.startswith("$") or text == "[m]":
            return None
        exact = [
            row
            for row in rows
            if any(text == normalize(row.get(k)) for k in ("id", "identifier", "title", "number"))
        ]
        rows = exact or [row for row in rows if text in normalize(row.get("title"))]
    elif explicit is None and not filters:
        return None
    return rows[0] if len(rows) == 1 else None


def resolve_workflow_state(
    state: dict[str, Any], value: Any, team_id: str | None = None
) -> dict[str, Any] | None:
    text = normalize(value)
    candidates = state.get("workflow_states", [])
    if team_id:
        candidates = [row for row in candidates if row.get("teamId") == team_id]
    row = find_one(candidates, text, ["id", "name", "type"])
    if row:
        return row
    if value not in (None, "", "$last_workflow_state"):
        return None
    scratch = state.get("_scratch", {})
    state_id = scratch.get("last_workflow_state_id") if isinstance(scratch, dict) else None
    return find_one(candidates, state_id, ["id"]) if state_id else None


def resolve_label(
    state: dict[str, Any], value: Any, team_id: str | None = None
) -> dict[str, Any] | None:
    candidates = state.get("issue_labels", [])
    if team_id:
        candidates = [row for row in candidates if row.get("teamId") in (None, team_id)]
    row = find_one(candidates, value, ["id", "name"])
    if row:
        return row
    if value != "$last_label":
        return None
    scratch = state.get("_scratch", {})
    label_id = scratch.get("last_label_id") if isinstance(scratch, dict) else None
    return find_one(state.get("issue_labels", []), label_id, ["id"]) if label_id else None


def priority_fields(value: Any) -> tuple[float | None, str | None]:
    text = normalize(value)
    mapping = {
        "urgent": (1.0, "Urgent"),
        "high": (2.0, "High"),
        "medium": (3.0, "Medium"),
        "low": (4.0, "Low"),
        "none": (0.0, "No priority"),
        "no priority": (0.0, "No priority"),
    }
    if text in mapping:
        return mapping[text]
    try:
        number = float(value)
        label = {0.0: "No priority", 1.0: "Urgent", 2.0: "High", 3.0: "Medium", 4.0: "Low"}.get(
            number
        )
        return (number, label)
    except Exception:
        return (None, None)


def remember(state: dict[str, Any], **values: str | None) -> None:
    scratch = state.setdefault("_scratch", {})
    for key, value in values.items():
        if value:
            scratch[key] = value
            if key == "last_issue_id":
                created = scratch.setdefault("created_issue_ids", [])
                if value not in created:
                    created.append(value)


def resolve_issue_ref(state: dict[str, Any], value: Any) -> dict[str, Any] | None:
    text = str(value or "")
    scratch = state.get("_scratch", {}) if isinstance(state.get("_scratch"), dict) else {}
    created = list(scratch.get("created_issue_ids") or [])
    if text == "$last_issue":
        return resolve_issue(state, created[-1] if created else scratch.get("last_issue_id"))
    if text == "$prev_issue":
        if len(created) >= 2:
            return resolve_issue(state, created[-2])
        return resolve_issue(state, created[-1] if created else scratch.get("last_issue_id"))
    return resolve_issue(state, value)


def execute_lookup(state: dict[str, Any], tool: str, args: dict[str, Any]) -> dict[str, Any]:
    query = arg_text(args, "query", "filter", "name", "title", "identifier", "id", "value")
    if tool == "teams":
        team = resolve_team(state, query or args.get("team") or args.get("teamId"))
        remember(state, last_team_id=team.get("id") if team else None)
        return {
            "tool": tool,
            "ok": True,
            "reason": "",
            "matched_id": team.get("id") if team else None,
        }
    if tool == "users":
        user = resolve_user(state, query or args.get("user") or args.get("assignee"))
        remember(state, last_user_id=user.get("id") if user else None)
        return {
            "tool": tool,
            "ok": True,
            "reason": "",
            "matched_id": user.get("id") if user else None,
        }
    if tool == "issues":
        issue = query_issue(state, args)
        if issue:
            remember(state, last_issue_id=issue.get("id"))
        else:
            state.setdefault("_scratch", {}).pop("last_issue_id", None)
        return {
            "tool": tool,
            "ok": True,
            "reason": "",
            "matched_id": issue.get("id") if issue else None,
        }
    if tool == "workflowStates":
        team = resolve_team(state, args.get("team") or args.get("teamId"))
        state_row = resolve_workflow_state(
            state,
            query or args.get("state") or args.get("status"),
            team.get("id") if team else None,
        )
        remember(state, last_workflow_state_id=state_row.get("id") if state_row else None)
        return {
            "tool": tool,
            "ok": True,
            "reason": "",
            "matched_id": state_row.get("id") if state_row else None,
        }
    if tool == "issueLabels":
        label = resolve_label(state, query or args.get("label") or args.get("label_name"))
        remember(state, last_label_id=label.get("id") if label else None)
        return {
            "tool": tool,
            "ok": True,
            "reason": "",
            "matched_id": label.get("id") if label else None,
        }
    if tool == "comments":
        comments = state.get("comments", [])
        comment = find_one(comments, query, ["id", "body"])
        if comment is None:
            issue = query_issue(state, args)
            if issue:
                for row in comments:
                    if row.get("issueId") == issue.get("id"):
                        comment = row
                        break
        if comment:
            remember(state, last_comment_id=comment.get("id"), last_issue_id=comment.get("issueId"))
        return {
            "tool": tool,
            "ok": True,
            "reason": "",
            "matched_id": comment.get("id") if comment else None,
        }
    return {"tool": tool, "ok": True, "reason": ""}


def _execute_call_without_label_lists(
    state: dict[str, Any], call: dict[str, Any]
) -> dict[str, Any]:
    from recommit.components.call_normalization import normalize_tool_arguments

    tool = str(
        call.get("tool") or call.get("name") or call.get("endpoint") or call.get("api") or ""
    )
    args = normalize_tool_arguments(
        call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
    )
    if tool == "issue":
        issue = resolve_issue(state, args.get("id"))
        if issue:
            remember(state, last_issue_id=issue["id"])
        return {
            "tool": tool,
            "ok": issue is not None,
            "reason": "" if issue else "unresolved_issue",
            "matched_id": issue.get("id") if issue else None,
        }
    if tool in {"teams", "users", "issues", "workflowStates", "issueLabels", "comments"}:
        return execute_lookup(state, tool, args)
    result = {"tool": tool, "ok": True, "reason": ""}
    if tool in {"issueCreate", "issueUpdate"}:
        if args.get("stateId") is not None:
            issue = (
                resolve_issue(state, args.get("id") or args.get("issueId") or args.get("issue_id"))
                if tool == "issueUpdate"
                else None
            )
            team_id = issue.get("teamId") if issue else args.get("teamId")
            rows = [
                row
                for row in state.get("workflow_states", [])
                if row.get("id") == args["stateId"]
                and (not team_id or row.get("teamId") == team_id)
            ]
            if len(rows) != 1:
                result.update(ok=False, reason="unresolved_workflow_state")
                return result
        for parameter, table in (("teamId", "teams"), ("assigneeId", "users")):
            if args.get(parameter) not in (None, "") and not any(
                row.get("id") == args[parameter] for row in state.get(table, [])
            ):
                result.update(ok=False, reason="invalid_" + parameter)
                return result
        if "priority" in args and args["priority"] is not None:
            priority, label = priority_fields(args["priority"])
            if priority is None or label is None:
                result.update(ok=False, reason="invalid_priority")
                return result
    if tool == "teamCreate":
        name = arg_text(args, "name", "team", "displayName")
        if not name:
            result.update(ok=False, reason="missing_team_name")
            return result
        team = {
            "id": next_id("team", state.setdefault("teams", [])),
            "name": name,
            "key": re.sub("[^A-Za-z0-9]", "", name).upper()[:4] or "TEAM",
            "displayName": name,
            "organizationId": state.get("organizations", [{}])[0].get("id"),
            "private": False,
        }
        state["teams"].append(team)
        state.setdefault("workflow_states", []).append(
            {
                "id": next_id("state", state["workflow_states"]),
                "teamId": team["id"],
                "name": "Backlog",
                "type": "backlog",
            }
        )
        remember(state, last_team_id=team["id"])
        return result
    if tool == "workflowStateCreate":
        team = resolve_team(state, args.get("team") or args.get("teamId"))
        name = arg_text(args, "name", "state", "status")
        if not team or not name:
            result.update(ok=False, reason="missing_team_or_state_name")
            return result
        row = {
            "id": next_id("state", state.setdefault("workflow_states", [])),
            "teamId": team["id"],
            "name": name,
            "type": normalize(name),
        }
        state["workflow_states"].append(row)
        remember(state, last_workflow_state_id=row["id"])
        return result
    if tool == "issueLabelCreate":
        team = resolve_team(state, args.get("team") or args.get("teamId"))
        name = arg_text(args, "name", "label", "label_name")
        if not name:
            result.update(ok=False, reason="missing_label_name")
            return result
        row = {
            "id": next_id("label", state.setdefault("issue_labels", [])),
            "organizationId": state.get("organizations", [{}])[0].get("id"),
            "teamId": team.get("id") if team else None,
            "name": name,
        }
        state["issue_labels"].append(row)
        remember(state, last_label_id=row["id"])
        return result
    if tool == "issueLabelUpdate":
        label = resolve_label(
            state,
            args.get("label") or args.get("labelId") or args.get("id") or args.get("label_name"),
        )
        issue = resolve_issue(
            state, args.get("issue") or args.get("issueId") or args.get("issue_id")
        )
        new_name = arg_text(args, "name", "new_name", "label_name")
        if label and new_name:
            label["name"] = new_name
        if issue and label:
            assoc = {"issue_id": issue["id"], "issue_label_id": label["id"]}
            if assoc not in state.setdefault("issue_label_issue_association", []):
                state["issue_label_issue_association"].append(assoc)
        return result
    if tool == "issueLabelDelete":
        label = resolve_label(
            state,
            args.get("label") or args.get("labelId") or args.get("id") or args.get("label_name"),
        )
        issue = resolve_issue(
            state, args.get("issue") or args.get("issueId") or args.get("issue_id")
        )
        if issue and label:
            state["issue_label_issue_association"] = [
                row
                for row in state.setdefault("issue_label_issue_association", [])
                if not (
                    row.get("issue_id") == issue["id"] and row.get("issue_label_id") == label["id"]
                )
            ]
        elif label:
            label["retiredAt"] = "2026-07-10T00:00:00"
        return result
    if tool == "issueCreate":
        team = resolve_team(state, args.get("team") or args.get("teamId"))
        assignee = resolve_user(
            state, args.get("assignee") or args.get("assigneeId") or args.get("user")
        )
        title = arg_text(args, "title", "name")
        if not title:
            result.update(ok=False, reason="missing_issue_title")
            return result
        state_value = args.get("stateId") or args.get("state") or args.get("status")
        workflow_state = resolve_workflow_state(
            state, state_value or "Backlog", team.get("id") if team else None
        )
        if state_value and workflow_state is None:
            result.update(ok=False, reason="unresolved_workflow_state")
            return result
        issue = {
            "id": next_id("issue", state.setdefault("issues", [])),
            "identifier": f"{(team.get('key', 'ISS') if team else 'ISS')}-{len(state['issues']) + 1}",
            "title": title,
            "description": arg_text(args, "description", "body"),
            "teamId": team.get("id") if team else None,
            "stateId": workflow_state.get("id") if workflow_state else None,
            "assigneeId": assignee.get("id") if assignee else None,
            "creatorId": AGENT_USER_ID,
            "priority": 0.0,
            "priorityLabel": "No priority",
            "labelIds": [],
        }
        (priority, priority_label) = priority_fields(
            args.get("priority", args.get("priorityLabel"))
        )
        if priority is not None:
            issue["priority"] = priority
            issue["priorityLabel"] = priority_label
        label_value = args.get("label") or args.get("labelId") or args.get("label_name")
        if label_value:
            label = resolve_label(state, label_value, team.get("id") if team else None)
            if label:
                issue["labelIds"] = [label["id"]]
                assoc = {"issue_id": issue["id"], "issue_label_id": label["id"]}
                if assoc not in state.setdefault("issue_label_issue_association", []):
                    state["issue_label_issue_association"].append(assoc)
        state["issues"].append(issue)
        remember(state, last_issue_id=issue["id"])
        return result
    if tool == "commentCreate":
        issue = resolve_issue(
            state, args.get("issue") or args.get("issueId") or args.get("issue_id")
        )
        body = arg_text(args, "body", "comment", "text")
        if not issue or not body:
            result.update(ok=False, reason="missing_issue_or_body")
            return result
        comment = {
            "id": next_id("comment", state.setdefault("comments", [])),
            "issueId": issue["id"],
            "userId": AGENT_USER_ID,
            "body": body,
            "bodyData": "",
        }
        state["comments"].append(comment)
        remember(state, last_comment_id=comment["id"])
        return result
    if tool == "issueUpdate":
        issue = resolve_issue(
            state,
            args.get("issue_id") or args.get("issueId") or args.get("issue") or args.get("id"),
        )
        if not issue:
            result.update(ok=False, reason="unresolved_issue")
            return result
        if args.get("title") or args.get("name"):
            issue["title"] = arg_text(args, "title", "name")
        if "description" in args or "body" in args:
            desc = args.get("description", args.get("body"))
            issue["description"] = (
                desc
                if "include" not in normalize(args.get("operation"))
                else f"{issue.get('description', '')} {desc}".strip()
            )
        state_value = (
            args.get("stateId") or args.get("state") or args.get("status") or args.get("statusId")
        )
        if state_value:
            state_row = resolve_workflow_state(state, state_value, issue.get("teamId"))
            if state_row:
                issue["stateId"] = state_row["id"]
        user_value = (
            args.get("assigneeId")
            if "assigneeId" in args
            else args.get("assignee", args.get("user"))
        )
        if "assigneeId" in args or "assignee" in args or "user" in args:
            if user_value in (None, ""):
                issue["assigneeId"] = None
            else:
                user = resolve_user(state, user_value)
                if user:
                    issue["assigneeId"] = user["id"]
        (priority, priority_label) = priority_fields(
            args.get("priority", args.get("priorityLabel"))
        )
        if priority is not None:
            issue["priority"] = priority
            issue["priorityLabel"] = priority_label
        label_value = args.get("label") or args.get("labelId") or args.get("label_name")
        if label_value:
            label = resolve_label(state, label_value, issue.get("teamId"))
            if label:
                assoc = {"issue_id": issue["id"], "issue_label_id": label["id"]}
                if assoc not in state.setdefault("issue_label_issue_association", []):
                    state["issue_label_issue_association"].append(assoc)
        return result
    if tool == "issueRelationCreate":
        issue = resolve_issue_ref(
            state, args.get("issue") or args.get("issueId") or args.get("issue_id")
        )
        related = resolve_issue_ref(
            state,
            args.get("relatedIssue") or args.get("relatedIssueId") or args.get("related_issue_id"),
        )
        if not issue or not related:
            result.update(ok=False, reason="unresolved_relation_issue")
            return result
        state.setdefault("issue_relations", []).append(
            {
                "id": next_id("rel", state["issue_relations"]),
                "issueId": issue["id"],
                "relatedIssueId": related["id"],
                "type": arg_text(args, "type", "relation") or "relates",
                "issueTitle": issue.get("title"),
                "relatedIssueTitle": related.get("title"),
            }
        )
        return result
    if tool == "commentUpdate":
        comment = find_one(
            state.get("comments", []),
            args.get("comment") or args.get("commentId") or args.get("id"),
            ["id", "body"],
        )
        if comment is None:
            issue = resolve_issue(
                state, args.get("comment") or args.get("issue") or args.get("issueId")
            )
            if issue:
                for row in state.get("comments", []):
                    if row.get("issueId") == issue.get("id") and (not row.get("archivedAt")):
                        comment = row
                        break
        if comment is None:
            scratch = state.get("_scratch", {})
            comment = (
                find_one(state.get("comments", []), scratch.get("last_comment_id"), ["id"])
                if isinstance(scratch, dict)
                else None
            )
        body = arg_text(args, "body", "comment", "text")
        if not comment or not body:
            result.update(ok=False, reason="missing_comment_or_body")
            return result
        comment["body"] = body
        return result
    if tool == "commentDelete":
        comment = find_one(
            state.get("comments", []),
            args.get("comment") or args.get("commentId") or args.get("id"),
            ["id", "body"],
        )
        if comment is None:
            issue = resolve_issue(
                state, args.get("comment") or args.get("issue") or args.get("issueId")
            )
            if issue:
                for row in state.get("comments", []):
                    if row.get("issueId") == issue.get("id"):
                        comment = row
                        break
        if not comment:
            scratch = state.get("_scratch", {})
            comment = (
                find_one(state.get("comments", []), scratch.get("last_comment_id"), ["id"])
                if isinstance(scratch, dict)
                else None
            )
        if not comment:
            result.update(ok=False, reason="unresolved_comment")
            return result
        comment["archivedAt"] = "2026-07-10T00:00:00"
        return result
    if tool == "workflowStateArchive":
        state_row = resolve_workflow_state(
            state, args.get("id") or args.get("state") or args.get("name") or args.get("stateId")
        )
        if not state_row:
            result.update(ok=False, reason="unresolved_workflow_state")
            return result
        state_row["archivedAt"] = "2026-07-10T00:00:00"
        return result
    if tool == "teamMembershipCreate":
        team = resolve_team(state, args.get("team") or args.get("teamId"))
        user_values = args.get("userIds") or args.get("userId") or args.get("user")
        if not isinstance(user_values, list):
            user_values = [user_values] if user_values not in (None, "") else []
        if not team or not user_values:
            result.update(ok=False, reason="missing_team_or_user")
            return result
        for value in user_values:
            user = resolve_user(state, value)
            if not user:
                continue
            row = {
                "id": next_id("tm", state.setdefault("team_memberships", [])),
                "teamId": team["id"],
                "userId": user["id"],
            }
            if row not in state["team_memberships"] and (
                not any(
                    (
                        item.get("teamId") == team["id"] and item.get("userId") == user["id"]
                        for item in state["team_memberships"]
                    )
                )
            ):
                state["team_memberships"].append(row)
        return result
    result.update(ok=False, reason="unsupported_tool")
    return result


def execute_call(state: dict[str, Any], call: dict[str, Any]) -> dict[str, Any]:
    """Apply label-list mutations with the public API's replacement precedence.

    Explicit label IDs never fall back to the last label or a display-name match.
    """
    from recommit.components.call_normalization import normalize_tool_arguments

    tool = str(
        call.get("tool") or call.get("name") or call.get("endpoint") or call.get("api") or ""
    )
    args = normalize_tool_arguments(call.get("arguments"))
    fields = ("labelIds",) if "labelIds" in args else ("addedLabelIds", "removedLabelIds")
    if tool not in {"issueCreate", "issueUpdate"} or not any(k in args for k in fields):
        return _execute_call_without_label_lists(state, call)
    if tool == "issueCreate" and "labelIds" not in args:
        return {"tool": tool, "ok": False, "reason": "unsupported_create_label_delta"}
    issue = None
    if tool == "issueUpdate":
        issue = resolve_issue(
            state,
            args.get("issue_id") or args.get("issueId") or args.get("issue") or args.get("id"),
        )
        if issue is None:
            return {"tool": tool, "ok": False, "reason": "unresolved_issue"}
    catalog = {str(row.get("id")) for row in state.get("issue_labels", [])}
    for field in fields:
        if field not in args:
            continue
        values = args[field]
        if not isinstance(values, list) or any(not isinstance(x, str) or not x for x in values):
            return {"tool": tool, "ok": False, "reason": "invalid_label_list"}
        if field != "removedLabelIds" and (
            len(values) != len(set(values)) or not set(values) <= catalog
        ):
            return {"tool": tool, "ok": False, "reason": "invalid_or_unknown_label_id"}
    if "labelIds" in args:
        label_ids = list(args["labelIds"])
    else:
        # The seed keeps the relationship separately; include both representations.
        current = list(issue.get("labelIds") or [])
        current += [
            x["issue_label_id"]
            for x in state.get("issue_label_issue_association", [])
            if x.get("issue_id") == issue["id"]
        ]
        current += args.get("addedLabelIds", [])
        removed = set(args.get("removedLabelIds", []))
        label_ids = [x for x in dict.fromkeys(current) if x not in removed]
    forwarded = {
        k: v
        for k, v in args.items()
        if k
        not in {"labelIds", "addedLabelIds", "removedLabelIds", "label", "labelId", "label_name"}
    }
    result = _execute_call_without_label_lists(state, {"tool": tool, "arguments": forwarded})
    if not result.get("ok"):
        return result
    if tool == "issueCreate":
        issue = state["issues"][-1]
    issue["labelIds"] = label_ids
    associations = state.setdefault("issue_label_issue_association", [])
    associations[:] = [x for x in associations if x.get("issue_id") != issue["id"]]
    associations.extend(
        {"issue_id": issue["id"], "issue_label_id": label_id} for label_id in label_ids
    )
    return result


def row_key(row: dict[str, Any]) -> tuple:
    return tuple(
        sorted(((k, json.dumps(v, sort_keys=True, ensure_ascii=False)) for (k, v) in row.items()))
    )


def entity_pk(entity: str, row: dict[str, Any]) -> tuple:
    keys_by_entity = {
        "organizations": ("id",),
        "users": ("id",),
        "teams": ("id",),
        "workflow_states": ("id",),
        "issue_labels": ("id",),
        "issues": ("id",),
        "comments": ("id",),
        "team_memberships": ("id",),
        "issue_relations": ("id",),
        "issue_label_issue_association": ("issue_id", "issue_label_id"),
    }
    keys = keys_by_entity.get(entity, ())
    if keys and all((key in row for key in keys)):
        return tuple((row.get(key) for key in keys))
    return row_key(row)


def diff_state(
    before: dict[str, list[dict[str, Any]]], after: dict[str, list[dict[str, Any]]]
) -> dict[str, list[dict[str, Any]]]:
    diff = {"added": [], "removed": [], "changed": []}
    for entity, after_rows in after.items():
        if entity.startswith("_") or not isinstance(after_rows, list):
            continue
        before_rows = before.get(entity, [])
        if not isinstance(before_rows, list):
            before_rows = []
        before_keys = {entity_pk(entity, row): row for row in before_rows}
        after_keys = {entity_pk(entity, row): row for row in after_rows}
        for key, row in after_keys.items():
            if key not in before_keys:
                record = copy.deepcopy(row)
                record["__table__"] = entity
                diff["added"].append(record)
        for key, row in before_keys.items():
            if key not in after_keys:
                record = copy.deepcopy(row)
                record["__table__"] = entity
                diff["removed"].append(record)
        for key, before_row in before_keys.items():
            after_row = after_keys.get(key)
            if after_row and before_row != after_row:
                record = copy.deepcopy(after_row)
                record["__table__"] = entity
                record["__before__"] = before_row
                diff["changed"].append(record)
    return diff

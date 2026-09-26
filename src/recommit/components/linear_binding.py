"""Linear binding."""

from __future__ import annotations
import re
from typing import Any
from recommit.components.typed_binder_core import (
    MASK,
    BinderSchema,
    EntityCatalog,
    bind_arguments,
    build_binding_state,
    is_masked,
    strip_masks,
)

LINEAR_SCHEMA = BinderSchema(
    catalogs={
        "teams": EntityCatalog("teams", ("name", "displayName", "key")),
        "users": EntityCatalog("users", ("name", "displayName", "email")),
        "issues": EntityCatalog("issues", ("title",), alt_id_fields=("identifier",)),
        "issue_labels": EntityCatalog("issue_labels", ("name",)),
        "workflow_states": EntityCatalog("workflow_states", ("name",)),
        "comments": EntityCatalog("comments", ("body",), alt_id_fields=("id",)),
    },
    arg_to_catalog={
        "team": "teams",
        "teamId": "teams",
        "assignee": "users",
        "assigneeId": "users",
        "user": "users",
        "userIds": "users",
        "issue": "issues",
        "issueId": "issues",
        "relatedIssue": "issues",
        "relatedIssueId": "issues",
        "label": "issue_labels",
        "labelId": "issue_labels",
        "state": "workflow_states",
        "stateId": "workflow_states",
        "comment": "comments",
        "commentId": "comments",
        "id": "issues",
    },
    free_text_args=frozenset({"title", "name", "body", "description", "text", "query"}),
    id_patterns=(
        "\\b[A-Z]{2,}-\\d+\\b",
        "\\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\\b",
    ),
    enums={"priority": {"urgent": "urgent", "high": "high", "medium": "medium", "low": "low"}},
)


def _priority_from_task(task: str) -> str | None:
    lower = task.lower()
    for word, value in LINEAR_SCHEMA.enums["priority"].items():
        if re.search(f"\\b{word}\\b", lower):
            return value
    return None


def bind_linear_plan(
    plan: list[dict[str, Any]], *, task: str, seed: dict[str, Any], ids_only: bool = False
) -> list[dict[str, Any]]:
    """Bind a Linear expanded plan via the shared typed binder + thin overrides."""
    from recommit.components.argument_provenance import attach_ledger_meta, track_provenance

    with track_provenance() as ledger:
        bound = _bind_linear_plan_impl(plan, task=task, seed=seed, ids_only=ids_only)
        return attach_ledger_meta(bound, ledger)


def _bind_linear_plan_impl(
    plan: list[dict[str, Any]], *, task: str, seed: dict[str, Any], ids_only: bool = False
) -> list[dict[str, Any]]:
    state = build_binding_state(task, seed, LINEAR_SCHEMA)
    priority = _priority_from_task(task)
    created_issue_placeholders = 0
    keep = {"title", "name", "body", "team", "issue", "relatedIssue"} | set(
        LINEAR_SCHEMA.free_text_args
    )
    bound: list[dict[str, Any]] = []
    for call in plan:
        tool = str(call.get("tool") or "")
        args = dict(call.get("arguments") or {})
        if tool in {"teams", "users", "issues", "issueLabels", "workflowStates", "comments"}:
            catalog_key = {
                "teams": "teams",
                "users": "users",
                "issues": "issues",
                "issueLabels": "issue_labels",
                "workflowStates": "workflow_states",
                "comments": "comments",
            }[tool]
            if tool == "issues" and state.structured_ids:
                args["query"] = state.take_id() or state.peek_catalog("issues")
            elif tool == "comments" and state.structured_ids:
                args["query"] = state.structured_ids[0]
            else:
                hit = state.peek_catalog(catalog_key)
                if hit and is_masked(args.get("name") if "name" in args else args.get("query")):
                    if "name" in args or tool in {
                        "teams",
                        "users",
                        "issueLabels",
                        "workflowStates",
                    }:
                        args["name"] = hit
                    else:
                        args["query"] = hit
            bound.append({"tool": tool, "arguments": strip_masks(args, keep=keep)})
            continue
        args = bind_arguments(tool, args, state=state, schema=LINEAR_SCHEMA, ids_only=ids_only)
        from recommit.components.argument_provenance import bind_seed_state, bind_task_span

        if tool == "issueCreate":
            if priority and is_masked(args.get("priority")):
                args["priority"] = priority
            if not ids_only and is_masked(args.get("description")) and state.peek_quote():
                args["description"] = bind_task_span(
                    state.take_quote(), tool=tool, key="description"
                )
            created_issue_placeholders += 1
            args.setdefault("_created_index", created_issue_placeholders)
        elif tool == "issueUpdate":
            if args.get("assignee") is None and "assignee" in args:
                args["assigneeId"] = None
            elif (
                "assigneeId" not in args
                and args.get("assignee", "[M]") is not None
                and is_masked(args.get("assignee"))
            ):
                hit = state.peek_catalog("users")
                if hit:
                    args["assignee"] = bind_seed_state(hit, tool=tool, key="assignee")
            if priority and is_masked(args.get("priority")):
                args["priority"] = priority
            if not ids_only and is_masked(args.get("description")) and state.peek_quote():
                args["description"] = bind_task_span(
                    state.take_quote(), tool=tool, key="description"
                )
            if not ids_only and is_masked(args.get("title")) and state.peek_quote():
                args["title"] = bind_task_span(state.take_quote(), tool=tool, key="title")
            if is_masked(args.get("label")):
                hit = state.peek_catalog("issue_labels")
                if hit:
                    args["label"] = bind_seed_state(hit, tool=tool, key="label")
                elif not ids_only and state.peek_quote():
                    args["label"] = bind_task_span(state.take_quote(), tool=tool, key="label")
            if is_masked(args.get("state")):
                hit = state.peek_catalog("workflow_states")
                if hit:
                    args["state"] = bind_seed_state(hit, tool=tool, key="state")
        elif tool == "issueRelationCreate":
            if is_masked(args.get("issue")):
                args["issue"] = "$prev_issue"
            if is_masked(args.get("relatedIssue")):
                args["relatedIssue"] = "$last_issue"
        elif tool == "workflowStateArchive":
            if is_masked(args.get("id")):
                hit = state.peek_catalog("workflow_states")
                if hit:
                    args["id"] = hit
        elif tool == "teamMembershipCreate":
            if is_masked(args.get("userIds")) or args.get("userIds") == [MASK]:
                uuids = [
                    value
                    for value in state.structured_ids
                    if re.fullmatch(
                        "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                        value,
                        flags=re.I,
                    )
                ]
                if uuids:
                    args["userIds"] = [uuids[0]]
                else:
                    hit = state.peek_catalog("users")
                    if hit:
                        args["userIds"] = [hit]
        bound.append({"tool": tool, "arguments": strip_masks(args, keep=keep)})
    return bound

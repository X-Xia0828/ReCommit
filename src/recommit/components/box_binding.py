"""Box binding."""

from __future__ import annotations
from typing import Any
from recommit.components.typed_binder_core import (
    BinderSchema,
    EntityCatalog,
    bind_arguments,
    build_binding_state,
    is_masked,
    strip_masks,
)

BOX_SCHEMA = BinderSchema(
    catalogs={
        "box_folders": EntityCatalog("box_folders", ("name", "description")),
        "box_files": EntityCatalog("box_files", ("name",)),
        "box_comments": EntityCatalog("box_comments", ("message",)),
        "box_hubs": EntityCatalog("box_hubs", ("title", "description")),
        "box_users": EntityCatalog("box_users", ("name", "login")),
        "box_tasks": EntityCatalog("box_tasks", ("message",)),
    },
    arg_to_catalog={
        "file_id": "box_files",
        "folder_id": "box_folders",
        "parent_id": "box_folders",
        "parent": "box_folders",
        "item_id": "box_files",
        "hub_id": "box_hubs",
        "comment_id": "box_comments",
        "task_id": "box_tasks",
    },
    free_text_args=frozenset({"name", "title", "description", "message", "query", "content"}),
    id_patterns=("\\b\\d{8,12}\\b",),
    tool_arg_catalogs={
        f"{verb} /{entity}/{{id}}{suffix}": {"id": f"box_{entity}"}
        for entity in ("folders", "files", "comments", "hubs", "tasks")
        for verb in ("GET", "PUT", "POST", "DELETE")
        for suffix in ("", "/items", "/content", "/manage_items")
    },
)
READ_TOOLS = frozenset(
    {
        "GET /search",
        "GET /folders/{id}",
        "GET /folders/{id}/items",
        "GET /files/{id}",
        "GET /hubs",
        "GET /collections",
        "GET /users/me",
        "GET /files/{id}/content",
    }
)


def _resolve_parent_id(state, args: dict[str, Any], *, tool: str = "") -> str | None:
    from recommit.components.argument_provenance import bind_schema_default, bind_seed_state

    if not is_masked(args.get("parent_id")):
        return str(args.get("parent_id"))
    hit = state.peek_catalog("box_folders")
    if hit:
        return bind_seed_state(hit, tool=tool, key="parent_id", note="catalog:box_folders")
    return bind_schema_default("0", tool=tool, key="parent_id", note="root_folder_default")


def _coerce_item_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        return {"id": raw}
    return {}


def bind_box_plan(
    plan: list[dict[str, Any]],
    *,
    task: str,
    seed: dict[str, Any],
    consume_read_catalog: bool = False,
    ids_only: bool = False,
) -> list[dict[str, Any]]:
    """Bind a Box expanded plan via the shared typed binder + thin overrides.

    ``consume_read_catalog`` advances the entity cursor on each lookup instead of
    peeking, so an interleaved plan resolves successive files/folders rather than
    re-matching the first candidate for every write.

    ``ids_only=True`` binds catalog identifiers but leaves free-text slots masked
    for the HOW model to fill.
    """
    from recommit.components.argument_provenance import (
        attach_ledger_meta,
        bind_seed_state,
        bind_task_span,
        track_provenance,
    )

    with track_provenance() as ledger:
        bound = _bind_box_plan_impl(
            plan,
            task=task,
            seed=seed,
            consume_read_catalog=consume_read_catalog,
            ids_only=ids_only,
            bind_seed_state=bind_seed_state,
            bind_task_span=bind_task_span,
        )
        return attach_ledger_meta(bound, ledger)


def _bind_box_plan_impl(
    plan: list[dict[str, Any]],
    *,
    task: str,
    seed: dict[str, Any],
    consume_read_catalog: bool,
    ids_only: bool,
    bind_seed_state,
    bind_task_span,
) -> list[dict[str, Any]]:
    state = build_binding_state(task, seed, BOX_SCHEMA)
    bound: list[dict[str, Any]] = []
    keep_masks = {"id", "parent_id", "query"} | set(BOX_SCHEMA.free_text_args)

    def read_hit(*keys: str) -> str | None:
        for key in keys:
            hit = state.take_read_catalog(key) if consume_read_catalog else state.peek_catalog(key)
            if hit:
                return hit
        return None

    for call in plan:
        tool = str(call.get("tool") or "")
        args = dict(call.get("arguments") or {})
        if tool in READ_TOOLS:
            if tool == "GET /search" and is_masked(args.get("query")) and (not ids_only):
                hit = read_hit("box_folders", "box_files")
                if hit:
                    args["query"] = bind_seed_state(hit, tool=tool, key="query")
            elif tool in {"GET /folders/{id}", "GET /folders/{id}/items"} and is_masked(
                args.get("id")
            ):
                hit = read_hit("box_folders")
                if hit:
                    args["id"] = bind_seed_state(hit, tool=tool, key="id")
            elif tool == "GET /files/{id}" and is_masked(args.get("id")):
                hit = read_hit("box_files")
                if hit:
                    args["id"] = bind_seed_state(hit, tool=tool, key="id")
            elif tool == "GET /users/me":
                hit = state.peek_catalog("box_users")
                if hit and is_masked(args.get("name")) and (not ids_only):
                    args["name"] = bind_seed_state(hit, tool=tool, key="name")
            bound.append({"tool": tool, "arguments": strip_masks(args, keep=keep_masks)})
            continue
        args = bind_arguments(tool, args, state=state, schema=BOX_SCHEMA, ids_only=ids_only)
        if tool == "POST /folders":
            if not ids_only and is_masked(args.get("name")) and state.peek_quote():
                args["name"] = bind_task_span(state.take_quote(), tool=tool, key="name")
            if is_masked(args.get("parent_id")):
                args["parent_id"] = _resolve_parent_id(state, args, tool=tool)
        elif tool in {"PUT /folders/{id}", "DELETE /folders/{id}"}:
            if is_masked(args.get("id")):
                hit = state.peek_catalog("box_folders")
                if hit:
                    args["id"] = bind_seed_state(hit, tool=tool, key="id")
            if tool == "PUT /folders/{id}" and (not ids_only):
                if is_masked(args.get("name")) and state.peek_quote():
                    args["name"] = bind_task_span(state.take_quote(), tool=tool, key="name")
                if is_masked(args.get("description")) and state.peek_quote():
                    args["description"] = bind_task_span(
                        state.take_quote(), tool=tool, key="description"
                    )
        elif tool == "POST /files/content":
            if not ids_only and is_masked(args.get("name")) and state.peek_quote():
                args["name"] = bind_task_span(state.take_quote(), tool=tool, key="name")
            if is_masked(args.get("parent_id")):
                args["parent_id"] = _resolve_parent_id(state, args, tool=tool)
        elif tool in {"PUT /files/{id}", "DELETE /files/{id}"}:
            if is_masked(args.get("id")):
                hit = state.peek_catalog("box_files")
                if hit:
                    args["id"] = bind_seed_state(hit, tool=tool, key="id")
            if (
                not ids_only
                and tool == "PUT /files/{id}"
                and is_masked(args.get("name"))
                and state.peek_quote()
            ):
                args["name"] = bind_task_span(state.take_quote(), tool=tool, key="name")
        elif tool == "POST /comments":
            item = _coerce_item_dict(args.get("item"))
            if is_masked(item.get("id")):
                hit = state.peek_catalog("box_files")
                if hit:
                    item["id"] = bind_seed_state(hit, tool=tool, key="item.id")
            item.setdefault("type", "file")
            if not ids_only and is_masked(args.get("message")) and state.peek_quote():
                args["message"] = bind_task_span(state.take_quote(), tool=tool, key="message")
            args["item"] = item
        elif tool in {"PUT /comments/{id}", "DELETE /comments/{id}"}:
            if is_masked(args.get("id")):
                hit = state.peek_catalog("box_comments")
                if hit:
                    args["id"] = bind_seed_state(hit, tool=tool, key="id")
            if (
                not ids_only
                and tool == "PUT /comments/{id}"
                and is_masked(args.get("message"))
                and state.peek_quote()
            ):
                args["message"] = bind_task_span(state.take_quote(), tool=tool, key="message")
        elif tool == "POST /hubs":
            if not ids_only and is_masked(args.get("title")) and state.peek_quote():
                args["title"] = bind_task_span(state.take_quote(), tool=tool, key="title")
            if not ids_only and is_masked(args.get("description")) and state.peek_quote():
                args["description"] = bind_task_span(
                    state.take_quote(), tool=tool, key="description"
                )
        elif tool == "PUT /hubs/{id}":
            if is_masked(args.get("id")):
                hit = state.peek_catalog("box_hubs")
                if hit:
                    args["id"] = bind_seed_state(hit, tool=tool, key="id")
            if not ids_only and is_masked(args.get("title")) and state.peek_quote():
                args["title"] = bind_task_span(state.take_quote(), tool=tool, key="title")
        elif tool == "POST /hubs/{id}/manage_items":
            if is_masked(args.get("id")):
                hit = state.peek_catalog("box_hubs")
                if hit:
                    args["id"] = bind_seed_state(hit, tool=tool, key="id")
            operations = args.get("operations")
            if isinstance(operations, list):
                for op in operations:
                    if not isinstance(op, dict):
                        continue
                    item = op.get("item")
                    if isinstance(item, dict) and is_masked(item.get("id")):
                        hit = state.peek_catalog("box_folders") or state.peek_catalog("box_files")
                        if hit:
                            item["id"] = bind_seed_state(hit, tool=tool, key="item.id")
        elif tool == "POST /tasks":
            item = _coerce_item_dict(args.get("item"))
            if is_masked(item.get("id")):
                hit = state.peek_catalog("box_files")
                if hit:
                    item["id"] = bind_seed_state(hit, tool=tool, key="item.id")
            item.setdefault("type", "file")
            args["item"] = item
            if not ids_only and is_masked(args.get("message")) and state.peek_quote():
                args["message"] = bind_task_span(state.take_quote(), tool=tool, key="message")
        bound.append({"tool": tool, "arguments": strip_masks(args, keep=keep_masks)})
    return bound

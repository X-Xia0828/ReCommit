"""Box simulator."""

from __future__ import annotations
import copy
import hashlib
import json
from typing import Any

AGENT_USER_ID = "27512847635"
ROOT_FOLDER_ID = "0"
TABLE_KEYS = ("box_files", "box_folders", "box_comments", "box_hubs", "box_hub_items", "box_tasks")


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


def nested_get(args: dict[str, Any], dotted: str) -> Any:
    if dotted in args:
        return args[dotted]
    current: Any = args
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


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


def remember(state: dict[str, Any], **values: str | None) -> None:
    scratch = state.setdefault("_scratch", {})
    for key, value in values.items():
        if value:
            scratch[key] = value
            if key == "last_folder_id":
                created = scratch.setdefault("created_folder_ids", [])
                if value not in created:
                    created.append(value)
            if key == "last_file_id":
                created = scratch.setdefault("created_file_ids", [])
                if value not in created:
                    created.append(value)
            if key == "last_hub_id":
                created = scratch.setdefault("created_hub_ids", [])
                if value not in created:
                    created.append(value)


def active_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("item_status", "active") == "active"]


def resolve_folder(state: dict[str, Any], value: Any) -> dict[str, Any] | None:
    text = str(value or "")
    scratch = state.get("_scratch", {}) if isinstance(state.get("_scratch"), dict) else {}
    created = list(scratch.get("created_folder_ids") or [])
    if text in {"$last_folder", "$last_folder_id"}:
        return find_one(
            state.get("box_folders", []),
            created[-1] if created else scratch.get("last_folder_id"),
            ["id", "name"],
        )
    if text in {"root", "all files", ""}:
        return find_one(state.get("box_folders", []), ROOT_FOLDER_ID, ["id", "name"])
    row = find_one(active_rows(state.get("box_folders", [])), value, ["id", "name"])
    if row:
        return row
    return None


def resolve_file(state: dict[str, Any], value: Any) -> dict[str, Any] | None:
    text = str(value or "")
    scratch = state.get("_scratch", {}) if isinstance(state.get("_scratch"), dict) else {}
    created = list(scratch.get("created_file_ids") or [])
    if text == "$last_file":
        return find_one(
            state.get("box_files", []),
            created[-1] if created else scratch.get("last_file_id"),
            ["id", "name"],
        )
    row = find_one(active_rows(state.get("box_files", [])), value, ["id", "name"])
    if row:
        return row
    return None


def resolve_comment(state: dict[str, Any], value: Any) -> dict[str, Any] | None:
    row = find_one(state.get("box_comments", []), value, ["id", "message"])
    if row:
        return row
    if value == "$last_comment":
        return find_one(
            state.get("box_comments", []), state.get("_scratch", {}).get("last_comment_id"), ["id"]
        )
    return None


def resolve_hub(state: dict[str, Any], value: Any) -> dict[str, Any] | None:
    text = str(value or "")
    scratch = state.get("_scratch", {}) if isinstance(state.get("_scratch"), dict) else {}
    created = list(scratch.get("created_hub_ids") or [])
    if text == "$last_hub":
        return find_one(
            state.get("box_hubs", []),
            created[-1] if created else scratch.get("last_hub_id"),
            ["id", "title"],
        )
    row = find_one(state.get("box_hubs", []), value, ["id", "title"])
    if row:
        return row
    return None


def resolve_parent_id(state: dict[str, Any], args: dict[str, Any]) -> str:
    parent_id = nested_get(args, "parent.id") or args.get("parent_id")
    if parent_id not in (None, ""):
        folder = resolve_folder(state, parent_id)
        return folder["id"] if folder else str(parent_id)
    folder = resolve_folder(state, args.get("parent"))
    if folder:
        return folder["id"]
    return ROOT_FOLDER_ID


def search_items(state: dict[str, Any], query: str) -> list[tuple[str, dict[str, Any]]]:
    text = normalize(query)
    if not text:
        return []
    hits: list[tuple[str, dict[str, Any]]] = []
    for row in active_rows(state.get("box_folders", [])):
        if text_contains_any(row.get("name"), [text]):
            hits.append(("folder", row))
    for row in active_rows(state.get("box_files", [])):
        if text_contains_any(row.get("name"), [text]):
            hits.append(("file", row))
    return hits


def merge_tags(existing: Any, tags: Any) -> list[str]:
    current: list[str] = []
    if isinstance(existing, list):
        current = [str(item) for item in existing]
    elif isinstance(existing, str) and existing:
        current = [existing]
    incoming: list[str] = []
    if isinstance(tags, list):
        incoming = [str(item) for item in tags]
    elif isinstance(tags, str) and tags:
        incoming = [tags]
    merged = list(current)
    for tag in incoming:
        if tag not in merged:
            merged.append(tag)
    return merged


def execute_call(state: dict[str, Any], call: dict[str, Any]) -> dict[str, Any]:
    from recommit.components.call_normalization import normalize_tool_arguments

    tool = str(
        call.get("tool") or call.get("name") or call.get("endpoint") or call.get("api") or ""
    )
    args = normalize_tool_arguments(
        call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
    )
    # Named public path parameters take precedence over the generic local alias.
    for resource, parameter in (
        ("files", "file_id"),
        ("folders", "folder_id"),
        ("hubs", "hub_id"),
        ("comments", "comment_id"),
        ("tasks", "task_id"),
        ("collections", "collection_id"),
    ):
        if f"/{resource}/{{id}}" in tool and parameter in args:
            args["id"] = args[parameter]
            break
    result: dict[str, Any] = {"tool": tool, "ok": True, "reason": ""}
    if tool in {"POST /folders", "POST /files/content", "PUT /folders/{id}", "PUT /files/{id}"}:
        parent = nested_get(args, "parent.id") or args.get("parent_id")
        if parent not in (None, ""):
            folder = resolve_folder(state, parent)
            if folder is None:
                result.update(ok=False, reason="unresolved_parent")
                return result
            args["parent_id"] = folder["id"]
            if isinstance(args.get("parent"), dict):
                args["parent"] = dict(args["parent"], id=folder["id"])
    if tool in {"PUT /files/{id}", "PUT /folders/{id}"} and "tags" in args:
        if not isinstance(args["tags"], list) or any(
            not isinstance(tag, str) for tag in args["tags"]
        ):
            result.update(ok=False, reason="invalid_tags")
            return result
    if tool == "GET /search":
        query = arg_text(args, "query", "q", "name")
        hits = search_items(state, query)
        scratch = state.setdefault("_scratch", {})
        scratch["search_hits"] = hits
        if hits:
            (kind, row) = hits[0]
            if kind == "folder":
                remember(state, last_folder_id=row.get("id"))
            else:
                remember(state, last_file_id=row.get("id"))
            result["matched_id"] = row.get("id")
        return result
    if tool == "GET /users/me":
        user = find_one(state.get("box_users", []), AGENT_USER_ID, ["id"])
        if user:
            scratch = state.setdefault("_scratch", {})
            scratch["current_user_name"] = user.get("name")
            result["matched_id"] = user.get("id")
        return result
    if tool in {"GET /collections/{id}", "GET /collections/{id}/items"}:
        collection = find_one(state.get("box_collections", []), args.get("id"), ["id", "name"])
        result.update(
            ok=collection is not None,
            reason="" if collection else "unresolved_collection",
            matched_id=collection.get("id") if collection else None,
        )
        return result
    if tool in {"GET /hubs/{id}", "GET /hub_items"}:
        hub = resolve_hub(state, args.get("hub_id") or args.get("id"))
        if hub:
            remember(state, last_hub_id=hub["id"])
        result.update(
            ok=hub is not None,
            reason="" if hub else "unresolved_hub",
            matched_id=hub.get("id") if hub else None,
        )
        return result
    if tool in {"GET /files/{id}/comments", "GET /files/{id}/tasks"}:
        file_row = resolve_file(state, args.get("id"))
        if file_row:
            remember(state, last_file_id=file_row["id"])
        result.update(
            ok=file_row is not None,
            reason="" if file_row else "unresolved_file",
            matched_id=file_row.get("id") if file_row else None,
        )
        return result
    if tool == "GET /folders/{id}":
        folder = resolve_folder(state, args.get("id") or args.get("folder_id"))
        remember(state, last_folder_id=folder.get("id") if folder else None)
        result["matched_id"] = folder.get("id") if folder else None
        return result
    if tool == "GET /folders/{id}/items":
        folder = resolve_folder(state, args.get("id") or args.get("folder_id"))
        remember(state, last_folder_id=folder.get("id") if folder else None)
        result["matched_id"] = folder.get("id") if folder else None
        return result
    if tool == "GET /files/{id}":
        file_row = resolve_file(state, args.get("id") or args.get("file_id"))
        remember(state, last_file_id=file_row.get("id") if file_row else None)
        result["matched_id"] = file_row.get("id") if file_row else None
        return result
    if tool == "GET /files/{id}/content":
        file_row = resolve_file(state, args.get("id") or args.get("file_id"))
        remember(state, last_file_id=file_row.get("id") if file_row else None)
        result["matched_id"] = file_row.get("id") if file_row else None
        return result
    if tool == "GET /hubs":
        hubs = state.get("box_hubs", [])
        if hubs:
            remember(state, last_hub_id=hubs[0].get("id"))
            result["matched_id"] = hubs[0].get("id")
        return result
    if tool == "GET /collections":
        collections = state.get("box_collections", [])
        result["matched_id"] = collections[0].get("id") if collections else None
        return result
    if tool == "POST /folders":
        name = arg_text(args, "name")
        if not name:
            result.update(ok=False, reason="missing_folder_name")
            return result
        parent_id = resolve_parent_id(state, args)
        folder = {
            "id": next_id("folder", state.setdefault("box_folders", [])),
            "type": "folder",
            "name": name,
            "parent_id": parent_id,
            "created_at": "2025-12-30T00:00:00+00:00",
            "modified_at": "2025-12-30T00:00:00+00:00",
            "created_by_id": AGENT_USER_ID,
            "modified_by_id": AGENT_USER_ID,
            "owned_by_id": AGENT_USER_ID,
            "description": arg_text(args, "description"),
            "item_status": "active",
            "size": 0,
            "sequence_id": "0",
            "etag": "0",
        }
        state["box_folders"].append(folder)
        remember(state, last_folder_id=folder["id"])
        result["matched_id"] = folder["id"]
        return result
    if tool == "PUT /folders/{id}":
        folder = resolve_folder(state, args.get("id") or args.get("folder_id"))
        if not folder:
            result.update(ok=False, reason="unresolved_folder")
            return result
        if args.get("name"):
            folder["name"] = args["name"]
        if args.get("description") is not None:
            folder["description"] = args.get("description")
        if args.get("tags") is not None:
            folder["tags"] = list(args["tags"])
        parent_id = nested_get(args, "parent.id") or args.get("parent_id")
        if parent_id not in (None, ""):
            folder["parent_id"] = str(parent_id)
        remember(state, last_folder_id=folder["id"])
        return result
    if tool == "DELETE /folders/{id}":
        folder = resolve_folder(state, args.get("id") or args.get("folder_id"))
        if not folder:
            result.update(ok=False, reason="unresolved_folder")
            return result
        folder["item_status"] = "trashed"
        folder["trashed_at"] = "2025-12-30T00:00:00+00:00"
        return result
    if tool == "POST /files/content":
        name = arg_text(args, "name")
        if not name:
            result.update(ok=False, reason="missing_file_name")
            return result
        parent_id = resolve_parent_id(state, args)
        file_row = {
            "id": next_id("file", state.setdefault("box_files", [])),
            "type": "file",
            "name": name,
            "parent_id": parent_id,
            "created_at": "2025-12-30T00:00:00+00:00",
            "modified_at": "2025-12-30T00:00:00+00:00",
            "created_by_id": AGENT_USER_ID,
            "modified_by_id": AGENT_USER_ID,
            "owned_by_id": AGENT_USER_ID,
            "size": len(str(args.get("content") or "")),
            "extension": name.rsplit(".", 1)[-1] if "." in name else "",
            "version_number": "1",
            "comment_count": 0,
            "item_status": "active",
            "sequence_id": "0",
            "etag": "0",
        }
        state["box_files"].append(file_row)
        remember(state, last_file_id=file_row["id"])
        result["matched_id"] = file_row["id"]
        return result
    if tool == "POST /files/{id}/content":
        file_row = resolve_file(state, args.get("id") or args.get("file_id"))
        if not file_row:
            result.update(ok=False, reason="unresolved_file")
            return result
        # JSON callers supply inline content; never open a model-provided local path.
        content = args.get("file", args.get("content"))
        if isinstance(content, dict):
            content = content.get("content")
        if not isinstance(content, (str, bytes)):
            result.update(ok=False, reason="missing_or_invalid_file_content")
            return result
        if args.get("if_match") is not None and args["if_match"] != file_row.get("etag"):
            result.update(ok=False, reason="precondition_failed")
            return result
        raw = content.encode("utf-8") if isinstance(content, str) else content
        version = int(file_row.get("version_number") or 0) + 1
        file_row.update(
            content=content,
            size=len(raw),
            sha_1=hashlib.sha1(raw).hexdigest(),
            version_number=str(version),
            modified_by_id=AGENT_USER_ID,
            etag=str(version),
        )
        if "name" in args and args["name"] is not None:
            file_row["name"] = str(args["name"])
            file_row["extension"] = (
                str(args["name"]).rsplit(".", 1)[-1] if "." in str(args["name"]) else ""
            )
        remember(state, last_file_id=file_row["id"])
        result["matched_id"] = file_row["id"]
        return result
    if tool == "PUT /files/{id}":
        file_row = resolve_file(state, args.get("id") or args.get("file_id"))
        if not file_row:
            result.update(ok=False, reason="unresolved_file")
            return result
        if args.get("name"):
            file_row["name"] = args["name"]
            if "." in args["name"]:
                file_row["extension"] = args["name"].rsplit(".", 1)[-1]
        if args.get("description") is not None:
            file_row["description"] = args.get("description")
        if args.get("tags") is not None:
            file_row["tags"] = list(args["tags"])
        parent_id = nested_get(args, "parent.id") or args.get("parent_id")
        if parent_id not in (None, ""):
            file_row["parent_id"] = str(parent_id)
        remember(state, last_file_id=file_row["id"])
        return result
    if tool == "DELETE /files/{id}":
        file_row = resolve_file(state, args.get("id") or args.get("file_id"))
        if not file_row:
            result.update(ok=False, reason="unresolved_file")
            return result
        file_row["item_status"] = "trashed"
        file_row["trashed_at"] = "2025-12-30T00:00:00+00:00"
        return result
    if tool == "POST /comments":
        item_id = nested_get(args, "item.id") or args.get("item_id")
        item_type = nested_get(args, "item.type") or args.get("item_type") or "file"
        message = arg_text(args, "message", "text", "body")
        if not item_id or not message:
            result.update(ok=False, reason="missing_comment_target_or_message")
            return result
        if item_type == "file":
            file_row = resolve_file(state, item_id)
            if not file_row:
                result.update(ok=False, reason="unresolved_file")
                return result
            item_id = file_row["id"]
        comment = {
            "id": next_id("comment", state.setdefault("box_comments", [])),
            "type": "comment",
            "item_id": item_id,
            "file_id": item_id if item_type == "file" else nested_get(args, "file_id"),
            "item_type": item_type,
            "message": message,
            "created_by_id": AGENT_USER_ID,
            "created_at": "2025-12-30T00:00:00+00:00",
            "modified_at": "2025-12-30T00:00:00+00:00",
            "is_reply_comment": item_type == "comment",
        }
        state["box_comments"].append(comment)
        remember(
            state,
            last_comment_id=comment["id"],
            last_file_id=item_id if item_type == "file" else None,
        )
        result["matched_id"] = comment["id"]
        return result
    if tool == "PUT /comments/{id}":
        comment = resolve_comment(state, args.get("id") or args.get("comment_id"))
        if not comment:
            result.update(ok=False, reason="unresolved_comment")
            return result
        message = arg_text(args, "message", "text", "body")
        if message:
            comment["message"] = message
        remember(state, last_comment_id=comment["id"])
        return result
    if tool == "DELETE /comments/{id}":
        comment = resolve_comment(state, args.get("id") or args.get("comment_id"))
        if not comment:
            result.update(ok=False, reason="unresolved_comment")
            return result
        state["box_comments"] = [
            row for row in state.get("box_comments", []) if row.get("id") != comment["id"]
        ]
        return result
    if tool == "POST /hubs":
        title = arg_text(args, "title", "name")
        if not title:
            result.update(ok=False, reason="missing_hub_title")
            return result
        hub = {
            "id": next_id("hub", state.setdefault("box_hubs", [])),
            "type": "hubs",
            "title": title,
            "description": arg_text(args, "description"),
            "created_by_id": AGENT_USER_ID,
            "updated_by_id": AGENT_USER_ID,
            "created_at": "2025-12-30T00:00:00+00:00",
            "updated_at": "2025-12-30T00:00:00+00:00",
            "is_ai_enabled": True,
            "can_non_owners_invite": True,
            "can_shared_link_be_created": True,
            "is_collaboration_restricted_to_enterprise": False,
            "view_count": 0,
        }
        state["box_hubs"].append(hub)
        remember(state, last_hub_id=hub["id"])
        result["matched_id"] = hub["id"]
        return result
    if tool == "PUT /hubs/{id}":
        hub = resolve_hub(state, args.get("id") or args.get("hub_id"))
        if not hub:
            result.update(ok=False, reason="unresolved_hub")
            return result
        if args.get("title"):
            hub["title"] = args["title"]
        if args.get("description") is not None:
            hub["description"] = args.get("description")
        remember(state, last_hub_id=hub["id"])
        return result
    if tool == "POST /hubs/{id}/manage_items":
        hub = resolve_hub(state, args.get("id") or args.get("hub_id"))
        if not hub:
            result.update(ok=False, reason="unresolved_hub")
            return result
        operations = args.get("operations")
        if not isinstance(operations, list):
            result.update(ok=False, reason="missing_manage_items_operations")
            return result
        hub_items = state.setdefault("box_hub_items", [])
        for op in operations:
            if not isinstance(op, dict):
                continue
            action = normalize(op.get("action"))
            item = op.get("item") if isinstance(op.get("item"), dict) else {}
            item_id = item.get("id")
            item_type = item.get("type") or "file"
            if not item_id:
                continue
            if action == "add":
                if any(
                    (
                        row.get("hub_id") == hub["id"] and row.get("item_id") == item_id
                        for row in hub_items
                    )
                ):
                    continue
                item_name = None
                if item_type == "file":
                    file_row = resolve_file(state, item_id)
                    item_name = file_row.get("name") if file_row else None
                elif item_type == "folder":
                    folder = resolve_folder(state, item_id)
                    item_name = folder.get("name") if folder else None
                hub_items.append(
                    {
                        "id": next_id("hubitem", hub_items),
                        "type": "hub_item",
                        "hub_id": hub["id"],
                        "item_id": item_id,
                        "item_type": item_type,
                        "item_name": item_name,
                        "position": len(hub_items) + 1,
                        "added_by_id": AGENT_USER_ID,
                    }
                )
            elif action == "remove":
                state["box_hub_items"] = [
                    row
                    for row in hub_items
                    if not (row.get("hub_id") == hub["id"] and row.get("item_id") == item_id)
                ]
                hub_items = state["box_hub_items"]
        remember(state, last_hub_id=hub["id"])
        return result
    if tool == "POST /collections":
        name = arg_text(args, "name", "title")
        if not name:
            result.update(ok=False, reason="missing_collection_name")
            return result
        collection = {
            "id": next_id("collection", state.setdefault("box_collections", [])),
            "type": "collection",
            "name": name,
            "collection_type": arg_text(args, "collection_type") or "custom",
        }
        state["box_collections"].append(collection)
        result["matched_id"] = collection["id"]
        return result
    if tool == "POST /tasks":
        file_id = nested_get(args, "item.id") or args.get("item_id") or args.get("file_id")
        message = arg_text(args, "message", "text")
        file_row = resolve_file(state, file_id)
        if not file_row:
            result.update(ok=False, reason="unresolved_file")
            return result
        task = {
            "id": next_id("task", state.setdefault("box_tasks", [])),
            "type": "task",
            "item_id": file_row["id"],
            "item_type": "file",
            "message": message or "Please review",
            "created_by_id": AGENT_USER_ID,
            "created_at": "2025-12-30T00:00:00+00:00",
            "is_completed": False,
            "action": arg_text(args, "action") or "review",
            "completion_rule": arg_text(args, "completion_rule") or "any_assignee",
        }
        state["box_tasks"].append(task)
        remember(state, last_file_id=file_row["id"])
        result["matched_id"] = task["id"]
        return result
    if tool == "PUT /tasks/{id}":
        task = find_one(state.get("box_tasks", []), args.get("id") or args.get("task_id"), ["id"])
        if not task:
            result.update(ok=False, reason="unresolved_task")
            return result
        if args.get("message"):
            task["message"] = args["message"]
        if "is_completed" in args:
            task["is_completed"] = bool(args.get("is_completed"))
        return result
    if tool == "DELETE /tasks/{id}":
        task = find_one(state.get("box_tasks", []), args.get("id") or args.get("task_id"), ["id"])
        if not task:
            result.update(ok=False, reason="unresolved_task")
            return result
        state["box_tasks"] = [
            row for row in state.get("box_tasks", []) if row.get("id") != task["id"]
        ]
        return result
    result.update(ok=False, reason="unsupported_tool")
    return result


def row_key(row: dict[str, Any]) -> tuple:
    return tuple(
        sorted(((k, json.dumps(v, sort_keys=True, ensure_ascii=False)) for (k, v) in row.items()))
    )


def entity_pk(entity: str, row: dict[str, Any]) -> tuple:
    keys_by_entity = {
        "box_files": ("id",),
        "box_folders": ("id",),
        "box_comments": ("id",),
        "box_hubs": ("id",),
        "box_hub_items": ("id",),
        "box_tasks": ("id",),
    }
    keys = keys_by_entity.get(entity, ())
    if keys and all((key in row for key in keys)):
        return tuple((row.get(key) for key in keys))
    return row_key(row)


def snapshot_tables(state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        key: copy.deepcopy(state.get(key, []))
        for key in TABLE_KEYS
        if isinstance(state.get(key, []), list)
    }


def diff_state(
    before: dict[str, list[dict[str, Any]]], after: dict[str, list[dict[str, Any]]]
) -> dict[str, list[dict[str, Any]]]:
    diff: dict[str, list[dict[str, Any]]] = {"added": [], "removed": [], "changed": []}
    for entity, after_rows in after.items():
        before_rows = before.get(entity, [])
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

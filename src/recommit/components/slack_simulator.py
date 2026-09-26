"""Slack simulator."""

from __future__ import annotations
import copy
import json
import re
from typing import Any

AGENT_USER = "U01AGENBOT9"


def normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def resolve_user(state: dict[str, list[dict[str, Any]]], value: Any) -> str | None:
    text = normalize_text(value)
    if not text or "placeholder" in text or "from_users" in text:
        return None
    rows = state.get("users", [])
    for fields in (("user_id",), ("username", "display_name", "real_name", "email")):
        matches = [
            user for user in rows if any(text == normalize_text(user.get(key)) for key in fields)
        ]
        if matches:
            return str(matches[0]["user_id"]) if len(matches) == 1 else None
    return None


def resolve_channel(state: dict[str, list[dict[str, Any]]], value: Any) -> str | None:
    if value == "$last_channel":
        scratch = state.get("_scratch", {})
        if isinstance(scratch, dict):
            value = scratch.get("last_channel_id")
    text = normalize_text(value).lstrip("#")
    if not text or "placeholder" in text:
        return None
    for channel in state.get("channels", []):
        candidates = [channel.get("channel_id"), channel.get("channel_name")]
        if any((text == normalize_text(candidate).lstrip("#") for candidate in candidates)):
            return str(channel["channel_id"])
    return None


def next_id(prefix: str, rows: list[dict[str, Any]]) -> str:
    return f"{prefix}_OFFLINE_{len(rows) + 1:04d}"


def message_text(args: dict[str, Any]) -> str:
    text = args.get("text") or args.get("message") or args.get("message_text")
    if text:
        return str(text)
    return _blocks_to_mrkdwn(args["blocks"]) if isinstance(args.get("blocks"), list) else ""


def message_blocks(args: dict[str, Any]) -> str:
    blocks = args.get("blocks")
    if blocks is None:
        return ""
    return json.dumps(blocks, ensure_ascii=False, separators=(",", ":"))


# Text rendering follows the public Agent-Diff Slack backend.
def _blocks_to_mrkdwn(blocks: list) -> str:
    """Extract text from Block Kit blocks and convert to mrkdwn format.

    Matches Slack's behavior of auto-generating text from blocks.
    Bold -> *text*, Italic -> _text_, Strike -> ~text~, Code -> `text`
    """
    if not blocks:
        return ""

    parts: list[str] = []

    for block in blocks:
        block_type = block.get("type", "")

        if block_type == "rich_text":
            for element in block.get("elements", []):
                element_type = element.get("type", "")

                if element_type == "rich_text_section":
                    for item in element.get("elements", []):
                        parts.append(_element_to_mrkdwn(item))

                elif element_type == "rich_text_list":
                    style = element.get("style", "bullet")
                    for idx, list_item in enumerate(element.get("elements", [])):
                        prefix = f"{idx + 1}. " if style == "ordered" else "• "
                        item_texts = [
                            _element_to_mrkdwn(el) for el in list_item.get("elements", [])
                        ]
                        parts.append(prefix + "".join(item_texts))

                elif element_type == "rich_text_preformatted":
                    code_parts = [
                        _element_to_mrkdwn(item, in_code=True)
                        for item in element.get("elements", [])
                    ]
                    parts.append("```" + "".join(code_parts) + "```")

                elif element_type == "rich_text_quote":
                    quote_parts = [_element_to_mrkdwn(item) for item in element.get("elements", [])]
                    parts.append(">" + "".join(quote_parts))

        elif block_type == "section":
            text_obj = block.get("text", {})
            if text_obj.get("type") == "mrkdwn":
                parts.append(text_obj.get("text", ""))
            elif text_obj.get("type") == "plain_text":
                parts.append(text_obj.get("text", ""))

    return "\n".join(parts) if parts else ""


def _element_to_mrkdwn(item: dict, in_code: bool = False) -> str:
    """Convert a single element to mrkdwn format."""
    item_type = item.get("type", "")

    if item_type == "text":
        text = item.get("text", "")
        if in_code:
            return text
        style = item.get("style", {})
        if style.get("bold"):
            text = f"*{text}*"
        if style.get("italic"):
            text = f"_{text}_"
        if style.get("strike"):
            text = f"~{text}~"
        if style.get("code"):
            text = f"`{text}`"
        return text

    elif item_type == "user":
        user_id = item.get("user_id", "")
        return f"<@{user_id}>"

    elif item_type == "channel":
        channel_id = item.get("channel_id", "")
        return f"<#{channel_id}>"

    elif item_type == "link":
        url = item.get("url", "")
        link_text = item.get("text", url)
        return f"<{url}|{link_text}>"

    elif item_type == "emoji":
        name = item.get("name", "")
        return f":{name}:"

    return ""


def execute_call(state: dict[str, list[dict[str, Any]]], call: dict[str, Any]) -> dict[str, Any]:
    from recommit.components.call_normalization import normalize_tool_arguments

    tool = str(
        call.get("tool") or call.get("name") or call.get("endpoint") or call.get("api") or ""
    )
    args = normalize_tool_arguments(
        call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
    )
    result = {"tool": tool, "ok": True, "reason": ""}
    if tool in {
        "users.list",
        "users.info",
        "users.conversations",
        "conversations.list",
        "conversations.info",
        "conversations.members",
        "conversations.history",
        "conversations.replies",
        "search.messages",
        "search.all",
    }:
        return result
    if tool == "conversations.open":
        raw_users = args.get("users") or args.get("user") or []
        if isinstance(raw_users, str):
            raw_users = [item.strip() for item in re.split("[,;]", raw_users) if item.strip()]
        user_ids = [resolve_user(state, value) for value in raw_users]
        user_ids = [value for value in user_ids if value]
        if not user_ids:
            result.update(ok=False, reason="no_resolved_users")
            return result
        channel = {
            "channel_id": next_id("D", state.setdefault("channels", [])),
            "channel_name": "dm-" + "-".join(user_ids),
            "team_id": "T01WORKSPACE",
            "is_private": True,
            "is_dm": len(user_ids) == 1,
            "is_gc": len(user_ids) > 1,
        }
        state["channels"].append(channel)
        state.setdefault("_scratch", {})["last_channel_id"] = channel["channel_id"]
        for user_id in [AGENT_USER, *user_ids]:
            state.setdefault("channel_members", []).append(
                {"channel_id": channel["channel_id"], "user_id": user_id}
            )
        result["channel_id"] = channel["channel_id"]
        return result
    if tool == "conversations.create":
        name = str(args.get("name") or args.get("channel") or args.get("channel_name") or "")
        if not name:
            result.update(ok=False, reason="missing_channel_name")
            return result
        channel = {
            "channel_id": next_id("C", state.setdefault("channels", [])),
            "channel_name": name.lstrip("#"),
            "team_id": "T01WORKSPACE",
            "is_private": bool(args.get("is_private", False)),
            "is_dm": False,
            "is_gc": False,
        }
        state["channels"].append(channel)
        state.setdefault("_scratch", {})["last_channel_id"] = channel["channel_id"]
        state.setdefault("channel_members", []).append(
            {"channel_id": channel["channel_id"], "user_id": AGENT_USER}
        )
        result["channel_id"] = channel["channel_id"]
        return result
    if tool == "conversations.invite":
        channel_id = resolve_channel(state, args.get("channel"))
        raw_users = args.get("users") or args.get("user") or []
        if isinstance(raw_users, str):
            raw_users = [item.strip() for item in re.split("[,;]", raw_users) if item.strip()]
        user_ids = [resolve_user(state, value) for value in raw_users]
        user_ids = [value for value in user_ids if value]
        if not channel_id or not user_ids:
            result.update(ok=False, reason="unresolved_channel_or_users")
            return result
        existing = {
            (row.get("channel_id"), row.get("user_id"))
            for row in state.setdefault("channel_members", [])
        }
        for user_id in user_ids:
            key = (channel_id, user_id)
            if key not in existing:
                state["channel_members"].append({"channel_id": channel_id, "user_id": user_id})
        return result
    if tool == "conversations.kick":
        channel_id = resolve_channel(state, args.get("channel"))
        user_id = resolve_user(state, args.get("user") or args.get("users"))
        before = len(state.setdefault("channel_members", []))
        state["channel_members"] = [
            row
            for row in state["channel_members"]
            if not (row.get("channel_id") == channel_id and row.get("user_id") == user_id)
        ]
        if len(state["channel_members"]) == before:
            result.update(ok=False, reason="no_matching_membership_removed")
        return result
    if tool == "conversations.archive":
        channel_id = resolve_channel(state, args.get("channel"))
        for channel in state.get("channels", []):
            if channel.get("channel_id") == channel_id:
                channel["is_archived"] = True
                return result
        result.update(ok=False, reason="unresolved_channel")
        return result
    if tool == "conversations.unarchive":
        channel_id = resolve_channel(state, args.get("channel"))
        for channel in state.get("channels", []):
            if channel.get("channel_id") == channel_id:
                channel["is_archived"] = False
                return result
        result.update(ok=False, reason="unresolved_channel")
        return result
    if tool == "conversations.rename":
        channel_id = resolve_channel(state, args.get("channel"))
        name = str(args.get("name") or args.get("channel_name") or "").lstrip("#")
        for channel in state.get("channels", []):
            if channel.get("channel_id") == channel_id and name:
                channel["channel_name"] = name
                return result
        result.update(ok=False, reason="unresolved_channel_or_name")
        return result
    if tool in {"conversations.join", "conversations.leave"}:
        channel_id = resolve_channel(state, args.get("channel"))
        if not channel_id:
            result.update(ok=False, reason="unresolved_channel")
            return result
        members = state.setdefault("channel_members", [])
        membership = {"channel_id": channel_id, "user_id": AGENT_USER}
        if tool == "conversations.join":
            if membership not in members:
                members.append(membership)
        else:
            state["channel_members"] = [
                row
                for row in members
                if not (row.get("channel_id") == channel_id and row.get("user_id") == AGENT_USER)
            ]
        return result
    if tool == "conversations.setTopic":
        channel_id = resolve_channel(state, args.get("channel"))
        topic = str(args.get("topic") or args.get("text") or "")
        for channel in state.get("channels", []):
            if channel.get("channel_id") == channel_id:
                channel["topic_text"] = topic
                return result
        result.update(ok=False, reason="unresolved_channel")
        return result
    if tool == "chat.postMessage":
        channel_id = resolve_channel(state, args.get("channel"))
        if not channel_id:
            result.update(ok=False, reason="missing_or_unresolved_channel")
            return result
        parent_id = args.get("thread_ts") or args.get("parent_id")
        state.setdefault("messages", []).append(
            {
                "message_id": next_id("M", state["messages"]),
                "channel_id": channel_id,
                "user_id": AGENT_USER,
                "message_text": message_text(args),
                "blocks": message_blocks(args),
                "parent_id": parent_id,
            }
        )
        return result
    if tool in {"chat.update", "chat.delete"}:
        channel_id = resolve_channel(state, args.get("channel"))
        if channel_id is None:
            result.update(ok=False, reason="unresolved_channel")
            return result
        message_id = str(args.get("ts") or args.get("timestamp") or args.get("message_id") or "")
        messages = state.setdefault("messages", [])
        index = next(
            (
                idx
                for (idx, message) in enumerate(messages)
                if str(message.get("message_id")) == message_id
                and (not channel_id or message.get("channel_id") == channel_id)
            ),
            None,
        )
        if index is None:
            result.update(ok=False, reason="unresolved_message")
            return result
        if tool == "chat.delete":
            messages.pop(index)
        else:
            text = message_text(args)
            if text or args.get("blocks") is not None:
                messages[index]["message_text"] = text
            if args.get("blocks") is not None:
                messages[index]["blocks"] = message_blocks(args)
        return result
    if tool == "reactions.add":
        channel_id = resolve_channel(state, args.get("channel"))
        message_id = args.get("timestamp") or args.get("message_id") or args.get("ts")
        state.setdefault("message_reactions", []).append(
            {
                "reaction_id": next_id("R", state["message_reactions"]),
                "channel_id": channel_id,
                "message_id": message_id,
                "user_id": AGENT_USER,
                "reaction_type": str(args.get("name") or args.get("reaction") or ""),
            }
        )
        return result
    if tool == "reactions.remove":
        message_id = str(args.get("timestamp") or args.get("message_id") or args.get("ts") or "")
        reaction = str(args.get("name") or args.get("reaction") or "")
        user_id = resolve_user(state, args.get("user")) or AGENT_USER
        before = len(state.setdefault("message_reactions", []))
        state["message_reactions"] = [
            row
            for row in state["message_reactions"]
            if not (
                str(row.get("message_id")) == message_id
                and (not reaction or row.get("reaction_type") == reaction)
                and (not user_id or row.get("user_id") == user_id)
            )
        ]
        if len(state["message_reactions"]) == before:
            result.update(ok=False, reason="no_matching_reaction_removed")
        return result
    result.update(ok=False, reason="unsupported_tool")
    return result


def row_key(row: dict[str, Any]) -> tuple:
    return tuple(sorted(row.items()))


def entity_pk(entity: str, row: dict[str, Any]) -> tuple:
    keys_by_entity = {
        "teams": ("team_id",),
        "users": ("user_id",),
        "channels": ("channel_id",),
        "channel_members": ("channel_id", "user_id"),
        "messages": ("message_id",),
        "message_reactions": ("reaction_id",),
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
        if not isinstance(after_rows, list):
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
    for entity, before_rows in before.items():
        if not isinstance(before_rows, list):
            continue
        after_rows = after.get(entity, [])
        if not isinstance(after_rows, list):
            after_rows = []
        after_by_id = {entity_pk(entity, row): row for row in after_rows}
        for before_row in before_rows:
            after_row = after_by_id.get(entity_pk(entity, before_row))
            if after_row and before_row != after_row:
                record = copy.deepcopy(after_row)
                record["__table__"] = entity
                record["__before__"] = before_row
                diff["changed"].append(record)
    return diff

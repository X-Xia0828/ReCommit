"""Slack state."""

from __future__ import annotations
import json
from typing import Any


def state_summary(seed: dict[str, Any]) -> str:
    users = [
        {
            "id": row.get("user_id"),
            "username": row.get("username"),
            "display": row.get("display_name"),
            "real": row.get("real_name"),
        }
        for row in seed.get("users", [])
    ]
    channels = [
        {
            "id": row.get("channel_id"),
            "name": row.get("channel_name"),
            "is_dm": row.get("is_dm"),
            "is_gc": row.get("is_gc"),
            "archived": row.get("is_archived", False),
        }
        for row in seed.get("channels", [])
    ]
    messages = [
        {
            "id": row.get("message_id"),
            "channel": row.get("channel_id"),
            "user": row.get("user_id"),
            "text": row.get("message_text"),
            "parent": row.get("parent_id"),
        }
        for row in seed.get("messages", [])[:80]
    ]
    return json.dumps(
        {"users": users, "channels": channels, "messages": messages}, ensure_ascii=False, indent=2
    )


def realization_summary(seed: dict[str, Any], task: str, max_chars: int = 12000) -> str:
    """Serialize public evidence for HOW without slicing through a JSON record.

    Explicitly named channels get their messages first. Role membership is
    public catalog data, not a result inferred from a planned users.list call.
    WHAT and expressive generation use ``state_summary``.
    """
    from recommit.components.slack_binding import channel_ids_from_task

    data = json.loads(state_summary(seed))
    data["user_teams"] = [
        {key: row[key] for key in ("user_id", "team_id", "role") if key in row}
        for row in seed.get("user_teams", [])
    ]
    data["messages"] = []
    rows = list(seed.get("messages", []))
    named = set(channel_ids_from_task(task, seed))
    rows.sort(key=lambda row: row.get("channel_id") not in named)
    data["omitted_messages"] = len(rows)

    def encode():
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    for row in rows:
        message = {
            "id": row.get("message_id"),
            "channel": row.get("channel_id"),
            "user": row.get("user_id"),
            "text": row.get("message_text"),
            "parent": row.get("parent_id"),
        }
        data["messages"].append(message)
        data["omitted_messages"] -= 1
        if len(encode()) > max_chars:
            data["messages"].pop()
            data["omitted_messages"] += 1
    summary = encode()
    if len(summary) > max_chars:
        raise ValueError("Public user/channel catalogs exceed the HOW context budget")
    return summary

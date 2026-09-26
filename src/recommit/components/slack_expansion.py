"""Slack expansion."""

from __future__ import annotations
from collections import OrderedDict
from typing import Any, Mapping, Sequence
from recommit.components.content_dependencies import (
    CONTENT_READ_TOOLS,
    content_reads_for_task,
)
from recommit.components.slack_graph import MUTATING_TOOLS, PrerequisiteGraph
from recommit.components.slack_binding import is_dm_task

READ_DEFAULTS: dict[str, dict[str, Any]] = {
    "conversations.list": {"exclude_archived": True, "limit": 100},
    "users.list": {"limit": 100},
    "conversations.history": {"channel": "[M]", "limit": 50},
    "search.messages": {"query": "[M]", "count": 20},
}
WRITE_DEFAULTS: dict[str, dict[str, Any]] = {
    "chat.postMessage": {"channel": "[M]", "text": "[M]"},
    "chat.update": {"channel": "[M]", "ts": "[M]", "text": "[M]"},
    "chat.delete": {"channel": "[M]", "ts": "[M]"},
    "reactions.add": {"channel": "[M]", "timestamp": "[M]", "name": "[M]"},
    "reactions.remove": {"channel": "[M]", "timestamp": "[M]", "name": "[M]"},
    "conversations.create": {"name": "[M]"},
    "conversations.invite": {"channel": "[M]", "users": "[M]"},
    "conversations.kick": {"channel": "[M]", "user": "[M]"},
    "conversations.join": {"channel": "[M]"},
    "conversations.leave": {"channel": "[M]"},
    "conversations.rename": {"channel": "[M]", "name": "[M]"},
    "conversations.setTopic": {"channel": "[M]", "topic": "[M]"},
    "conversations.archive": {"channel": "[M]"},
    "conversations.unarchive": {"channel": "[M]"},
}
SUPPLIER_PRIORITY: dict[str, list[str]] = {
    "channel_id": ["conversations.list", "search.messages", "conversations.create"],
    "user_id": ["users.list", "conversations.members", "search.messages"],
    "message_ts": ["conversations.history", "search.messages"],
}


def _choose_supplier(
    graph: PrerequisiteGraph, id_type: str, effect: str, *, include_mutating: bool = False
) -> str | None:
    candidates = list(
        graph.prerequisites_of(effect, include_mutating=include_mutating).get(id_type, ())
    )
    if not candidates:
        return None
    priority = SUPPLIER_PRIORITY.get(id_type, [])
    read_candidates = [tool for tool in candidates if tool in graph.read_tools()]
    for tool in priority:
        if tool in read_candidates:
            return tool
    return sorted(read_candidates)[0] if read_candidates else None


def expand_obligations(
    obligations: Sequence[Mapping[str, Any]], graph: PrerequisiteGraph, *, task: str = ""
) -> list[dict[str, Any]]:
    """Turn an obligation set into an ordered call list with prerequisite reads."""
    reads_needed: OrderedDict[str, None] = OrderedDict()
    write_calls: list[dict[str, Any]] = []
    for item in obligations:
        effect = str(item.get("effect") or "")
        if effect not in MUTATING_TOOLS:
            continue
        base = dict(WRITE_DEFAULTS.get(effect, {}))
        slots = item.get("slots") if isinstance(item.get("slots"), dict) else {}
        for key, value in slots.items():
            if key in base and value not in (None, ""):
                base[key] = value
        write_calls.append({"tool": effect, "arguments": base})
        for id_type in graph.prerequisites_of(effect, include_mutating=False):
            supplier = _choose_supplier(graph, id_type, effect)
            if supplier:
                reads_needed.setdefault(supplier, None)
        for read_tool in content_reads_for_task(task, list(obligations)):
            if read_tool in CONTENT_READ_TOOLS:
                reads_needed.setdefault(read_tool, None)
    plan: list[dict[str, Any]] = []
    for tool in reads_needed:
        plan.append({"tool": tool, "arguments": dict(READ_DEFAULTS.get(tool, {}))})
    plan.extend(write_calls)
    needs_open = False
    if task and any((call.get("tool") == "chat.postMessage" for call in write_calls)):
        if is_dm_task(task):
            needs_open = True
    if needs_open:
        open_count = 1
        existing = sum((1 for call in plan if call.get("tool") == "conversations.open"))
        for _ in range(max(0, open_count - existing)):
            plan.insert(0, {"tool": "conversations.open", "arguments": {"users": "[M]"}})
    return plan

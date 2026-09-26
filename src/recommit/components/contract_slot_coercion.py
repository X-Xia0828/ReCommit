"""Contract slot coercion."""

from __future__ import annotations
from typing import Any, Mapping

MASK = "[M]"
DEFAULT_TZ = "America/Los_Angeles"
_TEXT_KEYS: tuple[str, ...] = (
    "value",
    "description",
    "text",
    "name",
    "summary",
    "title",
    "message",
    "displayName",
    "display_name",
    "email",
    "id",
)
TEXT_KINDS = frozenset({"text", "enum", "id"})


def coerce_slot_value(value: Any, kind: str, *, mask: str = MASK) -> Any:
    """Coerce one proposed slot value to ``kind``, or demote it to ``mask``."""
    if value is None or value == mask:
        return value
    if kind in TEXT_KINDS:
        if isinstance(value, str):
            return value if value.strip() else mask
        if isinstance(value, bool):
            return mask
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, Mapping):
            for key in _TEXT_KEYS:
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate
            strings = [v for v in value.values() if isinstance(v, str) and v.strip()]
            return strings[0] if len(strings) == 1 else mask
        if isinstance(value, list):
            strings = [v for v in value if isinstance(v, str) and v.strip()]
            if strings:
                return strings[0]
            for item in value:
                if isinstance(item, Mapping):
                    nested = coerce_slot_value(item, kind, mask=mask)
                    if nested != mask:
                        return nested
            return mask
        return mask
    if kind == "datetime":
        if isinstance(value, Mapping):
            stamp = value.get("dateTime") or value.get("date_time") or value.get("datetime")
            if isinstance(stamp, str) and "T" in stamp:
                tz = value.get("timeZone") or value.get("time_zone") or DEFAULT_TZ
                return {"dateTime": stamp, "timeZone": str(tz)}
            return mask
        if isinstance(value, str) and "T" in value:
            return {"dateTime": value, "timeZone": DEFAULT_TZ}
        return mask
    if kind == "list":
        return value if isinstance(value, list) else mask
    if kind == "object":
        if isinstance(value, Mapping):
            return dict(value)
        if isinstance(value, str) and value.strip():
            return {"id": value}
        return mask
    if kind == "bool":
        return value if isinstance(value, bool) else mask
    return value


def coerce_slots(
    slots: Mapping[str, Any], slot_kinds: Mapping[str, str], *, mask: str = MASK
) -> dict[str, Any]:
    """Coerce every slot with a declared kind; leave undeclared slots untouched."""
    out: dict[str, Any] = {}
    for key, value in slots.items():
        kind = slot_kinds.get(key)
        out[key] = coerce_slot_value(value, kind, mask=mask) if kind else value
    return out


CALENDAR_SLOT_KINDS: dict[str, str] = {
    "summary": "text",
    "description": "text",
    "location": "text",
    "text": "text",
    "query": "text",
    "q": "text",
    "name": "text",
    "iCalUID": "text",
    "role": "enum",
    "status": "enum",
    "visibility": "enum",
    "transparency": "enum",
    "calendarId": "id",
    "calendar": "id",
    "eventId": "id",
    "ruleId": "id",
    "id": "id",
    "email": "id",
    "scope_value": "id",
    "destination": "id",
    "destinationCalendarId": "id",
    "timeZone": "text",
    "start": "datetime",
    "end": "datetime",
    "attendees": "list",
    "recurrence": "list",
}
BOX_SLOT_KINDS: dict[str, str] = {
    "name": "text",
    "title": "text",
    "message": "text",
    "description": "text",
    "query": "text",
    "id": "id",
    "parent_id": "id",
    "file_id": "id",
    "folder_id": "id",
    "item_id": "id",
    "hub_id": "id",
    "comment_id": "id",
    "task_id": "id",
    "item": "object",
    "parent": "object",
    "operations": "list",
    "tags": "list",
    "is_completed": "bool",
}

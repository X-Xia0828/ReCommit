"""Calendar simulator."""

from __future__ import annotations
import copy
import json
from typing import Any

AGENT_USER_ID = "user_agent"
AGENT_EMAIL = "test.user@test.com"
PRIMARY_CALENDAR_ID = "test.user@test.com"
TABLE_KEYS = (
    "calendar_users",
    "calendars",
    "calendar_list_entries",
    "calendar_events",
    "calendar_event_attendees",
    "calendar_acl_rules",
    "calendar_settings",
    "calendar_channels",
)


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
        if value in (None, ""):
            continue
        text = str(value).strip()
        if not text or text == "[M]":
            continue
        return text
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


def remember(state: dict[str, Any], **values: str | None) -> None:
    scratch = state.setdefault("_scratch", {})
    for key, value in values.items():
        if value:
            scratch[key] = value
            if key == "last_calendar_id":
                created = scratch.setdefault("created_calendar_ids", [])
                if value not in created:
                    created.append(value)
            if key == "last_event_id":
                created = scratch.setdefault("created_event_ids", [])
                if value not in created:
                    created.append(value)


def resolve_calendar(state: dict[str, Any], value: Any) -> dict[str, Any] | None:
    text = str(value or "")
    scratch = state.get("_scratch", {}) if isinstance(state.get("_scratch"), dict) else {}
    created = list(scratch.get("created_calendar_ids") or [])
    if text in {"$last_calendar", "primary", "me", ""}:
        if text == "$last_calendar" and created:
            return find_one(state.get("calendars", []), created[-1], ["id", "summary"])
        if text in {"primary", "me", ""}:
            return find_one(state.get("calendars", []), PRIMARY_CALENDAR_ID, ["id", "summary"])
    row = find_one(state.get("calendars", []), value, ["id", "summary", "description"])
    if row:
        return row
    entry = find_one(state.get("calendar_list_entries", []), value, ["id", "calendar_id"])
    if entry:
        return find_one(state.get("calendars", []), entry.get("calendar_id"), ["id", "summary"])
    if text == "$last_calendar":
        return find_one(state.get("calendars", []), scratch.get("last_calendar_id"), ["id"])
    return None


def resolve_event(
    state: dict[str, Any], value: Any, calendar_id: str | None = None
) -> dict[str, Any] | None:
    text = str(value or "")
    scratch = state.get("_scratch", {}) if isinstance(state.get("_scratch"), dict) else {}
    created = list(scratch.get("created_event_ids") or [])
    candidates = state.get("calendar_events", [])
    if calendar_id:
        candidates = [row for row in candidates if row.get("calendar_id") == calendar_id]
    if text == "$last_event":
        return find_one(
            candidates, created[-1] if created else scratch.get("last_event_id"), ["id", "summary"]
        )
    row = find_one(candidates, value, ["id", "summary", "ical_uid"])
    if row:
        return row
    return None


def resolve_acl(
    state: dict[str, Any], value: Any, calendar_id: str | None = None
) -> dict[str, Any] | None:
    candidates = state.get("calendar_acl_rules", [])
    if calendar_id:
        candidates = [row for row in candidates if row.get("calendar_id") == calendar_id]
    row = find_one(candidates, value, ["id", "scope_value"])
    if row:
        return row
    text = normalize(value)
    if text.startswith("user:"):
        return find_one(candidates, text.split(":", 1)[1], ["scope_value"])
    return None


def resolve_user_email(state: dict[str, Any], value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "@" in text:
        return text
    user = find_one(state.get("calendar_users", []), value, ["id", "email", "display_name"])
    return str(user["email"]) if user and user.get("email") else text


def event_time_fields(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    start = args.get("start")
    end = args.get("end")
    if isinstance(start, dict):
        out["start"] = start
        out["start_datetime"] = start.get("dateTime") or start.get("date")
    elif start not in (None, ""):
        out["start"] = {"dateTime": str(start)}
        out["start_datetime"] = str(start)
    if isinstance(end, dict):
        out["end"] = end
        out["end_datetime"] = end.get("dateTime") or end.get("date")
    elif end not in (None, ""):
        out["end"] = {"dateTime": str(end)}
        out["end_datetime"] = str(end)
    return out


def ensure_list_entry(
    state: dict[str, Any], calendar_id: str, *, access_role: str = "owner"
) -> None:
    entries = state.setdefault("calendar_list_entries", [])
    existing = find_one(
        [row for row in entries if row.get("user_id") == AGENT_USER_ID],
        calendar_id,
        ["calendar_id", "id"],
    )
    if existing:
        existing["deleted"] = False
        existing["hidden"] = False
        existing["selected"] = True
        return
    entries.append(
        {
            "id": next_id("cle", entries),
            "user_id": AGENT_USER_ID,
            "calendar_id": calendar_id,
            "access_role": access_role,
            "primary": False,
            "selected": True,
            "hidden": False,
            "deleted": False,
            "etag": '"etag_offline"',
            "created_at": "2018-01-01T00:00:00",
            "updated_at": "2018-01-01T00:00:00",
        }
    )


EVENT_FIELDS = {
    "summary": "summary",
    "description": "description",
    "location": "location",
    "status": "status",
    "visibility": "visibility",
    "transparency": "transparency",
    "recurrence": "recurrence",
    "colorId": "color_id",
    "reminders": "reminders",
    "guestsCanInviteOthers": "guests_can_invite_others",
    "guestsCanModify": "guests_can_modify",
    "guestsCanSeeOtherGuests": "guests_can_see_other_guests",
    "conferenceData": "conference_data",
    "attachments": "attachments",
    "extendedProperties": "extended_properties",
    "source": "source",
}


def replace_attendees(state: dict[str, Any], event_id: str, attendees: list) -> None:
    """A supplied attendee array replaces the relationship, including an empty one."""
    rows = state.setdefault("calendar_event_attendees", [])
    rows[:] = [row for row in rows if row.get("event_id") != event_id]
    for item in attendees:
        rows.append(
            {
                "event_id": event_id,
                "email": item["email"],
                "display_name": item.get("displayName"),
                "organizer": item.get("organizer", False),
                "optional": item.get("optional", False),
                "response_status": item.get("responseStatus", "needsAction"),
            }
        )


def execute_call(state: dict[str, Any], call: dict[str, Any]) -> dict[str, Any]:
    tool = str(
        call.get("tool") or call.get("name") or call.get("endpoint") or call.get("api") or ""
    )
    from recommit.components.call_normalization import normalize_tool_arguments

    args = normalize_tool_arguments(call.get("arguments"))
    result: dict[str, Any] = {"tool": tool, "ok": True, "reason": ""}
    if tool in {"events.insert", "events.import", "events.patch", "events.update"}:
        attendees = args.get("attendees")
        if attendees is not None and (
            not isinstance(attendees, list)
            or any(
                not isinstance(item, dict)
                or not isinstance(item.get("email"), str)
                or not item["email"].strip()
                for item in attendees
            )
        ):
            result.update(ok=False, reason="invalid_attendees")
            return result
        if (
            "recurrence" in args
            and args["recurrence"] is not None
            and (
                not isinstance(args["recurrence"], list)
                or any(not isinstance(item, str) for item in args["recurrence"])
            )
        ):
            result.update(ok=False, reason="invalid_recurrence")
            return result

    if tool in {"calendarList.list", "calendarList.get"}:
        query = arg_text(args, "calendarId", "id", "summary", "query", "name")
        cal = (
            resolve_calendar(state, query)
            if query
            else resolve_calendar(state, PRIMARY_CALENDAR_ID)
        )
        remember(state, last_calendar_id=cal.get("id") if cal else None)
        result["matched_id"] = cal.get("id") if cal else None
        return result
    if tool == "calendars.get":
        cal = resolve_calendar(state, args.get("calendarId") or args.get("id"))
        remember(state, last_calendar_id=cal.get("id") if cal else None)
        result["matched_id"] = cal.get("id") if cal else None
        return result
    if tool == "calendars.insert":
        summary = arg_text(args, "summary", "name", "title")
        if not summary:
            result.update(ok=False, reason="missing_calendar_summary")
            return result
        cal = {
            "id": next_id("cal", state.setdefault("calendars", [])),
            "summary": summary,
            "description": arg_text(args, "description"),
            "owner_id": AGENT_USER_ID,
            "time_zone": arg_text(args, "timeZone", "timezone") or "UTC",
            "etag": '"etag_offline"',
            "created_at": "2018-01-01T00:00:00",
            "updated_at": "2018-01-01T00:00:00",
        }
        state["calendars"].append(cal)
        ensure_list_entry(state, cal["id"])
        remember(state, last_calendar_id=cal["id"])
        result["matched_id"] = cal["id"]
        return result
    if tool in {"calendars.patch", "calendars.update"}:
        cal = resolve_calendar(state, args.get("calendarId") or args.get("id"))
        if not cal:
            result.update(ok=False, reason="unresolved_calendar")
            return result
        for src, dst in (
            ("summary", "summary"),
            ("description", "description"),
            ("timeZone", "time_zone"),
            ("timezone", "time_zone"),
        ):
            if args.get(src) not in (None, ""):
                cal[dst] = args[src]
        remember(state, last_calendar_id=cal["id"])
        return result
    if tool == "calendars.delete":
        cal = resolve_calendar(state, args.get("calendarId") or args.get("id"))
        if not cal:
            result.update(ok=False, reason="unresolved_calendar")
            return result
        cal["deleted"] = True
        for entry in state.get("calendar_list_entries", []):
            if entry.get("calendar_id") == cal["id"]:
                entry["deleted"] = True
                entry["selected"] = False
        return result
    if tool == "calendars.clear":
        cal = resolve_calendar(
            state, args.get("calendarId") or args.get("id") or PRIMARY_CALENDAR_ID
        )
        if not cal:
            result.update(ok=False, reason="unresolved_calendar")
            return result
        state["calendar_events"] = [
            row for row in state.get("calendar_events", []) if row.get("calendar_id") != cal["id"]
        ]
        return result
    if tool == "calendarList.insert":
        calendar_id = arg_text(args, "id", "calendarId")
        if not calendar_id:
            result.update(ok=False, reason="missing_calendar_id")
            return result
        if not find_one(state.get("calendars", []), calendar_id, ["id"]):
            state.setdefault("calendars", []).append(
                {
                    "id": calendar_id,
                    "summary": arg_text(args, "summary", "title") or calendar_id,
                    "description": arg_text(args, "description"),
                    "owner_id": AGENT_USER_ID,
                    "time_zone": "UTC",
                    "etag": '"etag_offline"',
                    "created_at": "2018-01-01T00:00:00",
                    "updated_at": "2018-01-01T00:00:00",
                }
            )
        ensure_list_entry(state, calendar_id, access_role=arg_text(args, "accessRole") or "reader")
        entry = find_one(
            [
                row
                for row in state.get("calendar_list_entries", [])
                if row.get("user_id") == AGENT_USER_ID
            ],
            calendar_id,
            ["calendar_id"],
        )
        if entry:
            if "colorId" in args:
                entry["color_id"] = args.get("colorId")
            if "selected" in args:
                entry["selected"] = bool(args.get("selected"))
            if "hidden" in args:
                entry["hidden"] = bool(args.get("hidden"))
            if "summary" in args:
                entry["summary_override"] = args.get("summary")
        remember(state, last_calendar_id=calendar_id)
        return result
    if tool in {"calendarList.patch", "calendarList.update"}:
        cal = resolve_calendar(state, args.get("calendarId") or args.get("id"))
        calendar_id = cal.get("id") if cal else arg_text(args, "calendarId", "id")
        if not calendar_id:
            result.update(ok=False, reason="unresolved_calendar_list_entry")
            return result
        ensure_list_entry(state, calendar_id)
        entry = find_one(
            [
                row
                for row in state.get("calendar_list_entries", [])
                if row.get("user_id") == AGENT_USER_ID
            ],
            calendar_id,
            ["calendar_id", "id"],
        )
        if not entry:
            result.update(ok=False, reason="missing_list_entry")
            return result
        if "colorId" in args:
            entry["color_id"] = args.get("colorId")
        if "selected" in args:
            entry["selected"] = bool(args.get("selected"))
        if "hidden" in args:
            entry["hidden"] = bool(args.get("hidden"))
        if "summary" in args:
            entry["summary_override"] = args.get("summary")
            if cal:
                cal["summary"] = args.get("summary")
        if "description" in args and cal:
            cal["description"] = args.get("description")
        if "timeZone" in args and cal:
            cal["time_zone"] = args.get("timeZone")
        remember(state, last_calendar_id=calendar_id)
        return result
    if tool == "calendarList.delete":
        cal = resolve_calendar(state, args.get("calendarId") or args.get("id"))
        calendar_id = cal.get("id") if cal else arg_text(args, "calendarId", "id")
        if not calendar_id:
            result.update(ok=False, reason="unresolved_calendar_list_entry")
            return result
        for entry in state.get("calendar_list_entries", []):
            if entry.get("user_id") == AGENT_USER_ID and entry.get("calendar_id") == calendar_id:
                entry["deleted"] = True
                entry["selected"] = False
        return result
    if tool in {"events.list", "events.get", "events.instances"}:
        cal = resolve_calendar(
            state, args.get("calendarId") or args.get("calendar") or PRIMARY_CALENDAR_ID
        )
        event = resolve_event(
            state,
            args.get("eventId")
            or args.get("id")
            or args.get("summary")
            or args.get("q")
            or args.get("query"),
            cal.get("id") if cal else None,
        )
        remember(
            state,
            last_calendar_id=cal.get("id") if cal else None,
            last_event_id=event.get("id") if event else None,
        )
        result["matched_id"] = event.get("id") if event else cal.get("id") if cal else None
        return result
    if tool in {"events.insert", "events.quickAdd", "events.import"}:
        cal = resolve_calendar(
            state, args.get("calendarId") or args.get("calendar") or PRIMARY_CALENDAR_ID
        )
        if not cal:
            result.update(ok=False, reason="unresolved_calendar")
            return result
        summary = arg_text(args, "summary", "text", "title")
        if tool == "events.import":
            summary = summary or arg_text(args, "iCalUID") or "imported_event"
        if tool == "events.quickAdd" and (not summary):
            summary = arg_text(args, "text")
        if not summary:
            result.update(ok=False, reason="missing_event_summary")
            return result
        event = {
            "id": next_id("event", state.setdefault("calendar_events", [])),
            "calendar_id": cal["id"],
            "ical_uid": arg_text(args, "iCalUID", "ical_uid")
            or next_id("ical", state["calendar_events"]),
            "summary": summary,
            "description": arg_text(args, "description"),
            "location": arg_text(args, "location"),
            "status": arg_text(args, "status") or "confirmed",
            "visibility": arg_text(args, "visibility") or "default",
            "transparency": "opaque",
            "event_type": "default",
            "creator_id": AGENT_USER_ID,
            "organizer_id": AGENT_USER_ID,
            "creator_email": AGENT_EMAIL,
            "organizer_email": AGENT_EMAIL,
            "sequence": 0,
            "etag": '"etag_offline"',
            "html_link": "",
            "created_at": "2018-01-01T00:00:00",
            "updated_at": "2018-01-01T00:00:00",
        }
        event.update(event_time_fields(args))
        state["calendar_events"].append(event)
        for src, dst in EVENT_FIELDS.items():
            if args.get(src) is not None:
                event[dst] = copy.deepcopy(args[src])
        if args.get("attendees") is not None:
            replace_attendees(state, event["id"], args["attendees"])
        remember(state, last_calendar_id=cal["id"], last_event_id=event["id"])
        result["matched_id"] = event["id"]
        return result
    if tool in {"events.patch", "events.update"}:
        cal = resolve_calendar(
            state, args.get("calendarId") or args.get("calendar") or PRIMARY_CALENDAR_ID
        )
        if cal is None:
            result.update(ok=False, reason="unresolved_calendar")
            return result
        event = resolve_event(
            state,
            args.get("eventId") or args.get("id") or args.get("summary"),
            cal.get("id") if cal else None,
        )
        if not event:
            result.update(ok=False, reason="unresolved_event")
            return result
        for src, dst in EVENT_FIELDS.items():
            if args.get(src) is not None:
                event[dst] = copy.deepcopy(args[src])
        event.update(event_time_fields(args))
        if args.get("attendees") is not None:
            replace_attendees(state, event["id"], args["attendees"])
        remember(state, last_calendar_id=event.get("calendar_id"), last_event_id=event["id"])
        return result
    if tool == "events.delete":
        cal = resolve_calendar(
            state, args.get("calendarId") or args.get("calendar") or PRIMARY_CALENDAR_ID
        )
        if cal is None:
            result.update(ok=False, reason="unresolved_calendar")
            return result
        event = resolve_event(
            state,
            args.get("eventId") or args.get("id") or args.get("summary"),
            cal.get("id") if cal else None,
        )
        if not event:
            result.update(ok=False, reason="unresolved_event")
            return result
        event["status"] = "cancelled"
        return result
    if tool == "events.move":
        cal = resolve_calendar(state, args.get("calendarId") or args.get("calendar"))
        dest = resolve_calendar(state, args.get("destination") or args.get("destinationCalendarId"))
        if cal is None:
            result.update(ok=False, reason="unresolved_calendar")
            return result
        event = resolve_event(
            state, args.get("eventId") or args.get("id"), cal.get("id") if cal else None
        )
        if not event or not dest:
            result.update(ok=False, reason="unresolved_event_or_destination")
            return result
        event["calendar_id"] = dest["id"]
        remember(state, last_calendar_id=dest["id"], last_event_id=event["id"])
        return result
    if tool in {"acl.list", "acl.get"}:
        cal = resolve_calendar(state, args.get("calendarId") or PRIMARY_CALENDAR_ID)
        rule = resolve_acl(
            state,
            args.get("ruleId") or args.get("id") or args.get("scope") or args.get("email"),
            cal.get("id") if cal else None,
        )
        remember(state, last_calendar_id=cal.get("id") if cal else None)
        result["matched_id"] = rule.get("id") if rule else None
        return result
    if tool == "acl.insert":
        cal = resolve_calendar(
            state, args.get("calendarId") or args.get("calendar") or PRIMARY_CALENDAR_ID
        )
        if not cal:
            result.update(ok=False, reason="unresolved_calendar")
            return result
        scope = args.get("scope") if isinstance(args.get("scope"), dict) else {}
        email = resolve_user_email(
            state,
            scope.get("value")
            or args.get("scope_value")
            or args.get("email")
            or args.get("user")
            or args.get("value"),
        )
        role = arg_text(args, "role") or "reader"
        if not email:
            result.update(ok=False, reason="missing_acl_scope")
            return result
        rule = {
            "id": next_id("acl", state.setdefault("calendar_acl_rules", [])),
            "calendar_id": cal["id"],
            "role": role,
            "scope_type": scope.get("type") or "user",
            "scope_value": email,
            "etag": '"etag_offline"',
            "created_at": "2018-01-01T00:00:00",
            "updated_at": "2018-01-01T00:00:00",
        }
        state["calendar_acl_rules"].append(rule)
        remember(state, last_calendar_id=cal["id"])
        result["matched_id"] = rule["id"]
        return result
    if tool in {"acl.patch", "acl.update"}:
        cal = resolve_calendar(state, args.get("calendarId") or PRIMARY_CALENDAR_ID)
        rule = resolve_acl(
            state,
            args.get("ruleId") or args.get("id") or args.get("scope") or args.get("email"),
            cal.get("id") if cal else None,
        )
        if not rule:
            result.update(ok=False, reason="unresolved_acl")
            return result
        if args.get("role"):
            rule["role"] = args["role"]
        scope = args.get("scope") if isinstance(args.get("scope"), dict) else {}
        if scope.get("value"):
            rule["scope_value"] = scope["value"]
        return result
    if tool == "acl.delete":
        cal = resolve_calendar(state, args.get("calendarId") or PRIMARY_CALENDAR_ID)
        rule = resolve_acl(
            state,
            args.get("ruleId")
            or args.get("id")
            or args.get("scope_value")
            or args.get("scope")
            or args.get("email"),
            cal.get("id") if cal else None,
        )
        if not rule:
            result.update(ok=False, reason="unresolved_acl")
            return result
        rule["deleted"] = True
        rule["updated_at"] = "2018-01-01T00:00:00"
        remember(state, last_calendar_id=rule.get("calendar_id"))
        result["matched_id"] = rule.get("id")
        return result
    if tool == "freeBusy.query":
        items = args.get("items") if isinstance(args.get("items"), list) else []
        for item in items:
            if isinstance(item, dict):
                cal = resolve_calendar(state, item.get("id"))
                if cal:
                    remember(state, last_calendar_id=cal["id"])
        return result
    if tool in {
        "colors.get",
        "settings.list",
        "settings.get",
        "settings.watch",
        "calendarList.watch",
        "events.watch",
        "acl.watch",
        "channels.stop",
    }:
        if tool.endswith(".watch"):
            channel = {
                "id": next_id("channel", state.setdefault("calendar_channels", [])),
                "resource_id": arg_text(args, "id", "resourceId") or "resource_offline",
                "resource_uri": arg_text(args, "address", "resourceUri") or "",
                "type": arg_text(args, "type") or "web_hook",
                "address": arg_text(args, "address"),
                "expiration": args.get("expiration"),
                "token": arg_text(args, "token"),
                "params": args.get("params"),
                "payload": None,
                "user_id": AGENT_USER_ID,
                "created_at": "2018-01-01T00:00:00",
            }
            state["calendar_channels"].append(channel)
            result["matched_id"] = channel["id"]
        return result
    result.update(ok=False, reason="unsupported_tool")
    return result


def row_key(row: dict[str, Any]) -> tuple:
    return tuple(
        sorted(((k, json.dumps(v, sort_keys=True, ensure_ascii=False)) for (k, v) in row.items()))
    )


def entity_pk(entity: str, row: dict[str, Any]) -> tuple:
    keys_by_entity = {
        "calendar_users": ("id",),
        "calendars": ("id",),
        "calendar_list_entries": ("id",),
        "calendar_events": ("id",),
        "calendar_event_attendees": ("event_id", "email"),
        "calendar_acl_rules": ("id",),
        "calendar_settings": ("id",),
        "calendar_channels": ("id",),
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

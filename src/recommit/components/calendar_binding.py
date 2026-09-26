"""Calendar binding."""

from __future__ import annotations
import re
from typing import Any, Mapping
from recommit.components.typed_binder_core import (
    BinderSchema,
    EntityCatalog,
    arg_needs_bind,
    bind_arguments,
    build_binding_state,
    is_masked,
    strip_masks,
)

CALENDAR_SCHEMA = BinderSchema(
    catalogs={
        "calendars": EntityCatalog("calendars", ("summary", "description")),
        "calendar_users": EntityCatalog("calendar_users", ("display_name", "email"), id_field="id"),
        "calendar_events": EntityCatalog("calendar_events", ("summary",)),
        "calendar_acl_rules": EntityCatalog("calendar_acl_rules", ("scope_value", "role")),
        "calendar_list_entries": EntityCatalog(
            "calendar_list_entries", ("summary_override",), alt_id_fields=("calendar_id",)
        ),
    },
    arg_to_catalog={
        "calendar": "calendars",
        "id": "calendars",
        "ruleId": "calendar_acl_rules",
        "email": "calendar_users",
        "user": "calendar_users",
        "destination": "calendars",
        "destinationCalendarId": "calendars",
    },
    free_text_args=frozenset({"summary", "description", "location", "text", "query", "name"}),
    id_patterns=(
        "\\b[a-z0-9._%+-]+@[a-z0-9.-]+\\.[a-z]{2,}\\b",
        "\\bevent_[A-Za-z0-9_]+\\b",
        "\\bcal_[A-Za-z0-9_]+\\b",
        "\\bacl_[A-Za-z0-9_]+\\b",
    ),
    enums={
        "role": {
            "owner": "owner",
            "writer": "writer",
            "reader": "reader",
            "freebusyreader": "freeBusyReader",
            "free busy reader": "freeBusyReader",
        }
    },
)
READ_TOOLS = frozenset(
    {
        "calendarList.list",
        "calendarList.get",
        "calendars.get",
        "events.list",
        "events.get",
        "acl.list",
        "acl.get",
    }
)
CALENDAR_WRITE_TOOLS = frozenset(
    {
        "calendars.insert",
        "calendars.patch",
        "calendars.update",
        "calendars.delete",
        "calendars.clear",
        "calendarList.insert",
        "calendarList.patch",
        "calendarList.update",
        "calendarList.delete",
        "events.insert",
        "events.patch",
        "events.update",
        "events.delete",
        "events.import",
        "events.quickAdd",
        "events.move",
        "acl.insert",
        "acl.patch",
        "acl.update",
        "acl.delete",
    }
)
AGENT_EMAIL = "test.user@test.com"
DEFAULT_TZ = "America/Los_Angeles"
DATETIME_RE = re.compile(
    "\\b\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}(?::\\d{2})?(?:Z|[+-]\\d{2}:\\d{2})?\\b"
)
TZ_RE = re.compile(
    "\\b(?:America|Europe|Asia|Africa|Pacific|Australia)/[A-Za-z0-9_/+-]+\\b|\\bUTC\\b"
)
CALLED_TITLE_RE = re.compile(
    "(?:called|titled|named)\\s+[\\\"']?([A-Z][^\\\"'\\n]+?)[\\\"']?(?=\\s+on\\s+|\\s+from\\s+|\\s+for\\s+|\\s+at\\s+|\\s+to\\s+|[.,;]|$)",
    re.IGNORECASE,
)
NL_RANGE_RE = re.compile(
    "\\b(January|February|March|April|May|June|July|August|September|October|November|December)\\s+(\\d{1,2}),\\s*(\\d{4})\\s+from\\s+(\\d{1,2}):(\\d{2})\\s*(am|pm)\\s*-\\s*(\\d{1,2}):(\\d{2})\\s*(am|pm)\\b",
    re.IGNORECASE,
)
MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _to_24h(hour: int, minute: int, ampm: str) -> tuple[int, int]:
    ampm = ampm.lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    return (hour, minute)


def _called_titles(task: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for match in CALLED_TITLE_RE.finditer(task):
        title = re.sub("\\s+", " ", match.group(1)).strip(" \t\"'")
        title = re.sub("\\s+on\\s+$", "", title, flags=re.IGNORECASE).strip()
        if title and title.lower() not in seen:
            out.append(title)
            seen.add(title.lower())
    return out


def _nl_time_ranges(task: str, default_timezone: str = DEFAULT_TZ) -> list[tuple[str, str, str]]:
    """Return (start_iso, end_iso, timezone) tuples from English ranges."""
    out: list[tuple[str, str, str]] = []
    zones = set(TZ_RE.findall(task))
    if len(zones) > 1:
        # Multiple zones require semantic association; leave this to HOW.
        return out
    timezone = next(iter(zones), default_timezone)
    for match in NL_RANGE_RE.finditer(task):
        month = MONTHS[match.group(1).lower()]
        day = int(match.group(2))
        year = int(match.group(3))
        (sh, sm) = _to_24h(int(match.group(4)), int(match.group(5)), match.group(6))
        (eh, em) = _to_24h(int(match.group(7)), int(match.group(8)), match.group(9))
        start = f"{year:04d}-{month:02d}-{day:02d}T{sh:02d}:{sm:02d}:00"
        end = f"{year:04d}-{month:02d}-{day:02d}T{eh:02d}:{em:02d}:00"
        out.append((start, end, timezone))
    return out


def _resolve_calendar_id(state, args: dict[str, Any]) -> str | None:
    for key in ("calendarId", "calendar", "id"):
        value = args.get(key)
        if not is_masked(value):
            return str(value)
    hit = state.peek_catalog("calendars")
    if hit:
        return hit
    return "primary"


_PRIMARY_RE = re.compile(
    "\\b(?:my|the)\\s+(?:default\\s+|primary\\s+)?calendar\\b|\\bprimary calendar\\b"
)


def _task_named_calendars(task: str, seed: Mapping[str, Any]) -> list[str]:
    """Calendar summaries the task names, in the order the task names them.

    Longest-first within a position so a task naming "Harvest Schedule 2026"
    does not bind to a calendar merely called "Harvest". Order matters for
    `events.move`, where the source is named before the destination.
    """
    low = str(task or "").lower()
    hits: list[tuple[int, int, str]] = []
    for row in seed.get("calendars") or []:
        summary = str(row.get("summary") or "") if isinstance(row, Mapping) else ""
        if not summary:
            continue
        index = low.find(summary.lower())
        if index >= 0:
            hits.append((index, -len(summary), summary))
    return [summary for (_, _, summary) in sorted(hits)]


def _calendar_target(
    state,
    args: dict[str, Any],
    *,
    task: str,
    created_summary: str | None,
    seed: Mapping[str, Any] | None = None,
) -> str:
    for key in ("calendarId", "calendar", "id"):
        value = args.get(key)
        if not is_masked(value):
            return str(value)
    named = _task_named_calendars(task, seed or {})
    if named:
        return max(named, key=len)
    if created_summary:
        return created_summary
    if _PRIMARY_RE.search(task.lower()):
        return "primary"
    hit = state.peek_catalog("calendars")
    if hit:
        return hit
    return "primary"


def _take_email(state, *, task: str) -> str | None:
    emails = [value for value in state.structured_ids if "@" in str(value)]
    for email in emails:
        if email.lower() == AGENT_EMAIL:
            continue
        if email.lower() in task.lower():
            if email in state.structured_ids[state.structured_idx :]:
                while state.structured_idx < len(state.structured_ids):
                    value = state.take_id()
                    if value == email:
                        return email
                return email
            return email
    hit = state.peek_catalog("calendar_users")
    if hit and "@" in str(hit):
        return state.take_catalog("calendar_users")
    return None


def _role_from_task(task: str, schema: BinderSchema) -> str | None:
    lower = task.lower()
    phrase_map = (
        ("write access", "writer"),
        ("write permission", "writer"),
        ("editing access", "writer"),
        ("edit access", "writer"),
        ("make her writer", "writer"),
        ("make him writer", "writer"),
        ("as a writer", "writer"),
        ("as writer", "writer"),
        ("owner access", "owner"),
        ("as owner", "owner"),
        ("read access", "reader"),
        ("as a reader", "reader"),
        ("freebusyreader", "freeBusyReader"),
        ("free busy reader", "freeBusyReader"),
    )
    for phrase, role in phrase_map:
        if phrase in lower:
            return role
    role_map = schema.enums.get("role") or {}
    for key in sorted(role_map.keys(), key=len, reverse=True):
        if re.search(f"\\b{re.escape(key)}\\b", lower):
            return role_map[key]
    if re.search("\\bwrite\\b", lower):
        return "writer"
    return None


def _explicit_event_cancellation(task: str, event_id: str, seed: Mapping[str, Any]) -> bool:
    """Recognize only a complete, affirmative command naming this event.

    A keyword anywhere in a multi-action request is not evidence to cancel
    every event being updated. Complex instructions remain the HOW model's
    responsibility; existing status arguments are never overwritten here.
    """
    if not event_id or is_masked(event_id):
        return False
    match = re.fullmatch(
        r"(?:please\s+)?(?:cancel|delete|remove)\s+(?:the\s+)?(?:event\s+)?(.+?)\s*[.!]?",
        task.strip(),
        re.I,
    )
    if not match:
        return False
    target = match.group(1).strip().strip("'\"").casefold()
    if target == event_id.casefold():
        return True
    matches = {
        str(row["id"]).casefold()
        for row in seed.get("calendar_events", [])
        if row.get("id") and str(row.get("summary") or "").casefold() == target
    }
    return matches == {event_id.casefold()}


def _scoped_events(seed: Mapping[str, Any], calendar_id: Any) -> list[dict[str, Any]]:
    calendars = seed.get("calendars") or []
    target = str(calendar_id or "primary")
    if target == "primary":
        # Match the public environment's primary-calendar identity.
        self_users = [u for u in seed.get("calendar_users", []) if u.get("self") is True]
        ids = {
            str(c["id"])
            for c in calendars
            if any(
                (u.get("id") and c.get("owner_id") == u["id"])
                or (u.get("email") and c.get("id") == u["email"])
                for u in self_users
            )
        }
        if not ids and any(c.get("id") == AGENT_EMAIL for c in calendars):
            ids = {AGENT_EMAIL}
    elif any(c.get("id") == target for c in calendars):
        ids = {target}
    else:
        ids = {
            str(c["id"])
            for c in calendars
            if c.get("id") and str(c.get("summary") or "").casefold() == target.casefold()
        }
    if len(ids) != 1:
        return []
    return [e for e in seed.get("calendar_events", []) if e.get("calendar_id") in ids]


def _event_id_from_task(
    task: str, seed: Mapping[str, Any], state, calendar_id: Any = None
) -> str | None:
    events = _scoped_events(seed, calendar_id)
    for value in state.structured_ids[state.structured_idx :]:
        if str(value).startswith("event_") and any(e.get("id") == value for e in events):
            while state.structured_idx < len(state.structured_ids):
                taken = state.take_id()
                if taken == value:
                    return str(taken)
            return str(value)
    lower = task.lower()
    matches: list[tuple[int, str]] = []
    for row in events:
        if not isinstance(row, dict):
            continue
        summary = str(row.get("summary") or "").strip()
        event_id = str(row.get("id") or "").strip()
        if (
            summary
            and re.search(r"(?<!\w)" + re.escape(summary.lower()) + r"(?!\w)", lower)
            and event_id
        ):
            matches.append((len(summary), event_id))
    if not matches:
        return None
    longest = max(n for n, _ in matches)
    ids = {v for n, v in matches if n == longest}
    return next(iter(ids)) if len(ids) == 1 else None


def _calendar_timezone(args: Mapping[str, Any], seed: Mapping[str, Any]) -> str:
    """Interpret unspecified request times in the public user's local timezone."""
    target = str(args.get("calendarId") or "primary")
    self_users = [row for row in seed.get("calendar_users", []) if row.get("self") is True]
    user_ids = {row["id"] for row in self_users if row.get("id")}
    settings = {
        row.get("value")
        for row in seed.get("calendar_settings", [])
        if row.get("user_id") in user_ids and row.get("setting_id") == "timezone"
    }
    if len(settings) == 1:
        zone = next(iter(settings))
        if isinstance(zone, str) and TZ_RE.fullmatch(zone):
            return zone
    matches = []
    for row in seed.get("calendars", []):
        if target == "primary":
            selected = any(
                (user.get("id") and row.get("owner_id") == user["id"])
                or (user.get("email") and row.get("id") == user["email"])
                for user in self_users
            )
        else:
            selected = target in {str(row.get("id") or ""), str(row.get("summary") or "")}
        if selected:
            matches.append(row)
    if len(matches) == 1:
        zone = matches[0].get("time_zone") or matches[0].get("timeZone")
        if isinstance(zone, str) and TZ_RE.fullmatch(zone):
            return zone
    return DEFAULT_TZ


def _normalize_calendar_identifiers(
    plan: list[dict[str, Any]], seed: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Resolve unique public display names before they become locked bindings."""
    from recommit.components.argument_provenance import bind_seed_state

    def resolve(value: Any, rows: list[dict[str, Any]], *, tool: str, key: str) -> Any:
        if not isinstance(value, str) or is_masked(value) or value.startswith("$"):
            return value
        # An actual ID always takes precedence over another row's display name.
        if any(value == row.get("id") for row in rows):
            return value
        matches = {
            str(row["id"])
            for row in rows
            if row.get("id") and str(row.get("summary") or "").casefold() == value.casefold()
        }
        if len(matches) == 1:
            return bind_seed_state(
                next(iter(matches)), tool=tool, key=key, note="public_name_to_id"
            )
        if len(matches) > 1:
            return "[M]"
        # Unknown IDs may identify an external calendar not yet in the catalog.
        return value

    calendars = seed.get("calendars") or []
    for call in plan:
        tool, args = str(call.get("tool") or ""), call.get("arguments") or {}
        calendar_keys = {"calendarId", "calendar", "destination", "destinationCalendarId"}
        if tool.startswith(("calendarList.", "calendars.")):
            calendar_keys.add("id")
        for key in sorted(calendar_keys & args.keys()):
            if args[key] != "primary":
                args[key] = resolve(args[key], calendars, tool=tool, key=key)
        if "eventId" in args:
            events = _scoped_events(seed, args.get("calendarId"))
            args["eventId"] = resolve(args["eventId"], events, tool=tool, key="eventId")
    return plan


def _fill_event_time_fields(
    args: dict[str, Any],
    *,
    task: str,
    consumed: dict[str, int],
    default_timezone: str = DEFAULT_TZ,
) -> None:
    """Fill masked/absent start/end from ISO mentions or English ranges in the task."""
    ranges = _nl_time_ranges(task, default_timezone)
    range_idx = consumed.setdefault("range", 0)
    if range_idx < len(ranges) and (
        arg_needs_bind(args, "start")
        or "start" not in args
        or arg_needs_bind(args, "end")
        or ("end" not in args)
    ):
        (start_iso, end_iso, tz) = ranges[range_idx]
        consumed["range"] = range_idx + 1
        if arg_needs_bind(args, "start") or "start" not in args:
            args["start"] = {"dateTime": start_iso, "timeZone": tz}
        if arg_needs_bind(args, "end") or "end" not in args:
            args["end"] = {"dateTime": end_iso, "timeZone": tz}
        return
    datetimes = DATETIME_RE.findall(task)
    timezones = TZ_RE.findall(task)
    dt_idx = consumed.setdefault("dt", 0)
    tz_idx = consumed.setdefault("tz", 0)

    def next_dt() -> str | None:
        nonlocal dt_idx
        if dt_idx >= len(datetimes):
            return None
        value = datetimes[dt_idx]
        dt_idx += 1
        consumed["dt"] = dt_idx
        return value

    def next_tz() -> str | None:
        nonlocal tz_idx
        if len(set(timezones)) == 1:
            return timezones[0]
        if tz_idx >= len(timezones):
            return None
        value = timezones[tz_idx]
        tz_idx += 1
        consumed["tz"] = tz_idx
        return value

    start = args.get("start")
    if arg_needs_bind(args, "start") or ("start" not in args and datetimes):
        if not isinstance(start, dict) or is_masked(start):
            dt = next_dt()
            if dt:
                payload = {"dateTime": dt, "timeZone": next_tz() or default_timezone}
                args["start"] = payload
    elif isinstance(start, dict):
        if is_masked(start.get("dateTime")):
            dt = next_dt()
            if dt:
                start["dateTime"] = dt
        if is_masked(start.get("timeZone")):
            start["timeZone"] = next_tz() or default_timezone
        args["start"] = start
    end = args.get("end")
    remaining = len(datetimes) - consumed.get("dt", 0)
    if remaining > 0 and (arg_needs_bind(args, "end") or "end" not in args):
        if not isinstance(end, dict) or is_masked(end):
            dt = next_dt()
            if dt:
                payload = {
                    "dateTime": dt,
                    "timeZone": next_tz()
                    or (args.get("start") or {}).get("timeZone")
                    or default_timezone,
                }
                args["end"] = payload


def _repair_plan_dataflow(
    bound: list[dict[str, Any]], *, task: str, seed: Mapping[str, Any]
) -> list[dict[str, Any]]:
    named = _task_named_calendars(task, seed)
    made_calendar = made_event = False
    for call in bound:
        tool = str(call.get("tool") or "")
        args = call.get("arguments")
        if not isinstance(args, dict):
            continue
        if made_calendar and "calendarId" in args:
            if is_masked(args["calendarId"]):
                args["calendarId"] = "$last_calendar"
        if made_event and "eventId" in args:
            if is_masked(args["eventId"]):
                args["eventId"] = "$last_event"
        if tool == "events.move":
            source = str(args.get("calendarId") or "")
            destination = str(args.get("destination") or "")
            if not destination or destination.lower() == source.lower():
                others = [s for s in named if s.lower() != source.lower()]
                if others:
                    args["destination"] = others[-1]
        if tool == "calendars.insert":
            made_calendar = True
        if tool in {"events.insert", "events.quickAdd", "events.import"}:
            made_event = True
    return bound


def bind_calendar_plan(
    plan: list[dict[str, Any]],
    *,
    task: str,
    seed: dict[str, Any],
    consume_read_catalog: bool = False,
    ids_only: bool = False,
) -> list[dict[str, Any]]:
    """Bind a Calendar expanded plan via the shared typed binder + thin overrides.

    ``consume_read_catalog`` advances the entity cursor on each lookup instead of
    peeking, so an interleaved plan whose lookups precede their own write targets
    successive entities rather than re-resolving the first candidate every time.
    Only the multi-entity catalogs (events, ACL rules) are consumed; calendars
    stay on peek because writes share a single target calendar.

    ``ids_only=True`` leaves free-text summary/description/location masked.
    """
    from recommit.components.argument_provenance import attach_ledger_meta, track_provenance

    with track_provenance() as ledger:
        bound = _bind_calendar_plan_impl(
            plan, task=task, seed=seed, consume_read_catalog=consume_read_catalog, ids_only=ids_only
        )
        bound = _normalize_calendar_identifiers(bound, seed)
        return attach_ledger_meta(bound, ledger)


def _bind_calendar_plan_impl(
    plan: list[dict[str, Any]],
    *,
    task: str,
    seed: dict[str, Any],
    consume_read_catalog: bool = False,
    ids_only: bool = False,
) -> list[dict[str, Any]]:
    state = build_binding_state(task, seed, CALENDAR_SCHEMA)

    def read_hit(key: str) -> str | None:
        return state.take_read_catalog(key) if consume_read_catalog else state.peek_catalog(key)

    for title in _called_titles(task):
        if title not in state.quotes:
            state.quotes.append(title)
    bound: list[dict[str, Any]] = []
    time_consumed = {"dt": 0, "tz": 0, "range": 0}
    created_summary: str | None = None
    for call in plan:
        tool = str(call.get("tool") or "")
        args = dict(call.get("arguments") or {})
        if tool in READ_TOOLS:
            if tool in {"calendarList.list", "calendarList.get", "calendars.get"}:
                hit = state.peek_catalog("calendars")
                if hit:
                    if tool == "calendarList.list" and arg_needs_bind(args, "query"):
                        args["query"] = hit
                    elif arg_needs_bind(args, "calendarId"):
                        args["calendarId"] = hit
            elif tool in {"events.list", "events.get"}:
                if arg_needs_bind(args, "calendarId"):
                    args["calendarId"] = _resolve_calendar_id(state, args)
                if tool == "events.list" and arg_needs_bind(args, "q"):
                    hit = read_hit("calendar_events")
                    if hit:
                        args["q"] = hit
                if tool == "events.get" and arg_needs_bind(args, "eventId"):
                    event_id = _event_id_from_task(task, seed, state, args.get("calendarId"))
                    if event_id:
                        args["eventId"] = event_id
            elif tool in {"acl.list", "acl.get"}:
                if arg_needs_bind(args, "calendarId"):
                    args["calendarId"] = _resolve_calendar_id(state, args)
                if tool == "acl.get" and arg_needs_bind(args, "ruleId"):
                    hit = read_hit("calendar_acl_rules")
                    if hit:
                        args["ruleId"] = hit
            bound.append({"tool": tool, "arguments": strip_masks(args)})
            continue
        args = bind_arguments(tool, args, state=state, schema=CALENDAR_SCHEMA, ids_only=ids_only)
        if tool in CALENDAR_WRITE_TOOLS and arg_needs_bind(args, "calendarId"):
            if tool == "calendars.insert":
                args.pop("calendarId", None)
            else:
                args["calendarId"] = _calendar_target(
                    state, args, task=task, created_summary=created_summary, seed=seed
                )
        if tool == "events.insert":
            if not ids_only and arg_needs_bind(args, "summary") and state.peek_quote():
                args["summary"] = state.take_quote()
            if not ids_only and arg_needs_bind(args, "description") and state.peek_quote():
                args["description"] = state.take_quote()
            if not ids_only and arg_needs_bind(args, "location") and state.peek_quote():
                args["location"] = state.take_quote()
            _fill_event_time_fields(
                args,
                task=task,
                consumed=time_consumed,
                default_timezone=_calendar_timezone(args, seed),
            )
        elif tool in {"events.patch", "events.update"}:
            if arg_needs_bind(args, "eventId"):
                event_id = _event_id_from_task(task, seed, state, args.get("calendarId"))
                if event_id:
                    args["eventId"] = event_id
            if not ids_only and arg_needs_bind(args, "summary") and state.peek_quote():
                args["summary"] = state.take_quote()
            if not ids_only and arg_needs_bind(args, "description") and state.peek_quote():
                args["description"] = state.take_quote()
            if not ids_only and arg_needs_bind(args, "location") and state.peek_quote():
                args["location"] = state.take_quote()
            if "status" not in args and _explicit_event_cancellation(
                task, str(args.get("eventId") or ""), seed
            ):
                from recommit.components.argument_provenance import bind_task_span

                args["status"] = bind_task_span(
                    "cancelled", tool=tool, key="status", note="explicit_event_cancellation"
                )
            if (
                not ids_only
                and "location" not in args
                and ("location" in task.lower())
                and state.peek_quote()
            ):
                args["location"] = state.take_quote()
            _fill_event_time_fields(
                args,
                task=task,
                consumed=time_consumed,
                default_timezone=_calendar_timezone(args, seed),
            )
        elif tool == "events.delete":
            if arg_needs_bind(args, "eventId"):
                event_id = _event_id_from_task(task, seed, state, args.get("calendarId"))
                if event_id:
                    args["eventId"] = event_id
        elif tool == "events.move":
            if arg_needs_bind(args, "eventId"):
                event_id = _event_id_from_task(task, seed, state, args.get("calendarId"))
                if event_id:
                    args["eventId"] = event_id
            if arg_needs_bind(args, "destination"):
                hit = state.peek_catalog("calendars")
                if hit:
                    args["destination"] = hit
        elif tool == "events.quickAdd":
            if not ids_only and arg_needs_bind(args, "text") and state.peek_quote():
                args["text"] = state.take_quote()
        elif tool == "calendars.insert":
            if not ids_only and arg_needs_bind(args, "summary") and state.peek_quote():
                args["summary"] = state.take_quote()
            if not ids_only and arg_needs_bind(args, "description") and state.peek_quote():
                args["description"] = state.take_quote()
            summary = args.get("summary")
            if isinstance(summary, str) and (not is_masked(summary)):
                created_summary = summary
        elif tool in {"calendars.patch", "calendars.update", "calendars.delete", "calendars.clear"}:
            if arg_needs_bind(args, "calendarId"):
                hit = state.peek_catalog("calendars")
                if hit:
                    args["calendarId"] = hit
            if tool in {"calendars.patch", "calendars.update"}:
                if not ids_only and arg_needs_bind(args, "summary") and state.peek_quote():
                    args["summary"] = state.take_quote()
                if not ids_only and arg_needs_bind(args, "description") and state.peek_quote():
                    args["description"] = state.take_quote()
        elif tool == "calendarList.insert":
            if arg_needs_bind(args, "id"):
                hit = state.peek_catalog("calendars")
                if hit:
                    args["id"] = hit
        elif tool in {"calendarList.patch", "calendarList.update", "calendarList.delete"}:
            if arg_needs_bind(args, "calendarId"):
                hit = state.peek_catalog("calendars")
                if hit:
                    args["calendarId"] = hit
        elif tool == "acl.insert":
            if arg_needs_bind(args, "scope_value"):
                email = _take_email(state, task=task)
                if email:
                    args["scope_value"] = email
            if arg_needs_bind(args, "role"):
                role = _role_from_task(task, CALENDAR_SCHEMA)
                if role:
                    args["role"] = role
        elif tool in {"acl.patch", "acl.update", "acl.delete"}:
            if arg_needs_bind(args, "ruleId") or (
                tool == "acl.delete" and arg_needs_bind(args, "scope_value")
            ):
                email = _take_email(state, task=task)
                if email and arg_needs_bind(args, "scope_value"):
                    args["scope_value"] = email
                elif arg_needs_bind(args, "ruleId"):
                    hit = state.peek_catalog("calendar_acl_rules")
                    if hit:
                        args["ruleId"] = hit
            if tool == "acl.delete" and arg_needs_bind(args, "scope_value"):
                email = _take_email(state, task=task)
                if email:
                    args["scope_value"] = email
            if arg_needs_bind(args, "calendarId"):
                hit = state.peek_catalog("calendars")
                if hit:
                    for row in seed.get("calendars") or []:
                        if isinstance(row, dict) and str(row.get("summary") or "") == hit:
                            args["calendarId"] = str(row.get("id") or hit)
                            break
                    else:
                        args["calendarId"] = hit
        keep = {"calendarId", "eventId", "scope_value", "ruleId"} | set(
            CALENDAR_SCHEMA.free_text_args
        )
        bound.append({"tool": tool, "arguments": strip_masks(args, keep=keep)})
    return _repair_plan_dataflow(bound, task=task, seed=seed)

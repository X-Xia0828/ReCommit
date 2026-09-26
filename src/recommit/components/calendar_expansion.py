"""Calendar expansion."""

from __future__ import annotations
from collections import OrderedDict
from typing import Any, Mapping, Sequence
from recommit.components.calendar_graph import MUTATING_TOOLS
from recommit.components.slack_graph import PrerequisiteGraph
from recommit.components.contract_slot_coercion import CALENDAR_SLOT_KINDS
from recommit.components.contract_slot_coercion import coerce_slots as coerce_slot_dict

READ_DEFAULTS: dict[str, dict[str, Any]] = {
    "calendarList.list": {"query": "[M]"},
    "calendarList.get": {"calendarId": "[M]"},
    "calendars.get": {"calendarId": "[M]"},
    "events.list": {"calendarId": "[M]", "q": "[M]"},
    "events.get": {"calendarId": "[M]", "eventId": "[M]"},
    "acl.list": {"calendarId": "[M]"},
    "acl.get": {"calendarId": "[M]", "ruleId": "[M]"},
    "freeBusy.query": {"timeMin": "[M]", "timeMax": "[M]", "items": "[M]"},
}
WRITE_DEFAULTS: dict[str, dict[str, Any]] = {
    "calendars.insert": {"summary": "[M]"},
    "calendars.patch": {"calendarId": "[M]"},
    "calendars.update": {"calendarId": "[M]"},
    "calendars.delete": {"calendarId": "[M]"},
    "calendars.clear": {"calendarId": "[M]"},
    "calendarList.insert": {"id": "[M]"},
    "calendarList.patch": {"calendarId": "[M]"},
    "calendarList.update": {"calendarId": "[M]"},
    "calendarList.delete": {"calendarId": "[M]"},
    "events.insert": {"calendarId": "[M]", "summary": "[M]"},
    "events.patch": {"calendarId": "[M]", "eventId": "[M]"},
    "events.update": {"calendarId": "[M]", "eventId": "[M]"},
    "events.delete": {"calendarId": "[M]", "eventId": "[M]"},
    "events.import": {"calendarId": "[M]"},
    "events.quickAdd": {"calendarId": "[M]", "text": "[M]"},
    "events.move": {"calendarId": "[M]", "eventId": "[M]", "destination": "[M]"},
    "acl.insert": {"calendarId": "[M]", "role": "[M]", "scope_value": "[M]"},
    "acl.patch": {"calendarId": "[M]", "ruleId": "[M]"},
    "acl.update": {"calendarId": "[M]", "ruleId": "[M]"},
    "acl.delete": {"calendarId": "[M]", "scope_value": "[M]"},
    "settings.watch": {"id": "[M]", "type": "[M]", "address": "[M]"},
    "calendarList.watch": {"id": "[M]", "type": "[M]", "address": "[M]"},
    "events.watch": {"calendarId": "[M]", "id": "[M]", "type": "[M]", "address": "[M]"},
    "acl.watch": {"calendarId": "[M]", "id": "[M]", "type": "[M]", "address": "[M]"},
    "channels.stop": {"id": "[M]", "resourceId": "[M]"},
}
SUPPLIER_PRIORITY: dict[str, list[str]] = {
    "calendar_id": ["calendarList.list", "calendarList.get", "calendars.get", "calendars.insert"],
    "event_id": ["events.list", "events.get", "events.insert"],
    "acl_rule_id": ["acl.list", "acl.get", "acl.insert"],
}


def _choose_supplier(
    graph: PrerequisiteGraph, id_type: str, effect: str, *, include_mutating: bool = False
) -> str | None:
    candidates = list(
        graph.prerequisites_of(effect, include_mutating=include_mutating).get(id_type, ())
    )
    if not candidates:
        candidates = list(graph.suppliers_of(id_type, include_mutating=include_mutating) - {effect})
    if not candidates:
        return None
    priority = SUPPLIER_PRIORITY.get(id_type, [])
    read_candidates = [tool for tool in candidates if tool in graph.read_tools()]
    for tool in priority:
        if tool in read_candidates:
            return tool
    return sorted(read_candidates)[0] if read_candidates else None


def _read_call_for(supplier: str, write_args: Mapping[str, Any]) -> dict[str, Any]:
    """Read skeleton for one write, seeded with identifiers the write already has."""
    args = dict(READ_DEFAULTS.get(supplier, {}))
    for key in ("calendarId", "eventId", "ruleId"):
        if key in args and write_args.get(key) not in (None, "", "[M]"):
            args[key] = write_args[key]
    return {"tool": supplier, "arguments": args}


def expand_calendar_obligations(
    obligations: Sequence[Mapping[str, Any]],
    graph: PrerequisiteGraph,
    *,
    interleave_reads: bool = False,
    coerce_slots: bool = False,
) -> list[dict[str, Any]]:
    """Turn Calendar obligations into ordered calls with prerequisite reads.

    Default layout hoists a globally deduplicated read prefix ahead of every
    write.  ``interleave_reads`` instead linearises the obligation graph one
    obligation at a time, emitting each write's lookup immediately before it, so
    a write whose identifier slot is still masked resolves against the lookup
    issued for *that* obligation rather than whichever lookup happened to run
    first.
    """
    reads_needed: OrderedDict[str, None] = OrderedDict()
    write_calls: list[dict[str, Any]] = []
    interleaved: list[dict[str, Any]] = []
    for item in obligations:
        effect = str(item.get("effect") or "")
        if effect not in MUTATING_TOOLS:
            continue
        base = dict(WRITE_DEFAULTS.get(effect, {}))
        slots = item.get("slots") if isinstance(item.get("slots"), dict) else {}
        if coerce_slots:
            slots = coerce_slot_dict(slots, CALENDAR_SLOT_KINDS)
        for key, value in slots.items():
            if value not in (None, ""):
                base[key] = value
        write_calls.append({"tool": effect, "arguments": base})
        suppliers: list[str] = []
        for id_type in sorted(graph.prerequisites_of(effect, include_mutating=False)):
            supplier = _choose_supplier(graph, id_type, effect)
            if supplier:
                reads_needed.setdefault(supplier, None)
                if supplier not in suppliers:
                    suppliers.append(supplier)
        if interleave_reads:
            for supplier in suppliers:
                interleaved.append(_read_call_for(supplier, base))
            interleaved.append({"tool": effect, "arguments": base})
    if interleave_reads:
        return interleaved
    plan: list[dict[str, Any]] = []
    for tool in reads_needed:
        plan.append({"tool": tool, "arguments": dict(READ_DEFAULTS.get(tool, {}))})
    plan.extend(write_calls)
    return plan

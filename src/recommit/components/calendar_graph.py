"""Calendar graph."""

from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Mapping
from recommit.components.slack_graph import ContractError, PrerequisiteGraph, ToolContract

CALENDAR_ID = "calendar_id"
EVENT_ID = "event_id"
ACL_RULE_ID = "acl_rule_id"
IDENTIFIER_PARAMS: Mapping[str, str] = {
    "calendarId": CALENDAR_ID,
    "eventId": EVENT_ID,
    "ruleId": ACL_RULE_ID,
}
REST_TO_TOOL: Mapping[str, str] = {
    "GET /users/me/calendarList": "calendarList.list",
    "GET /users/me/calendarList/{calendarId}": "calendarList.get",
    "POST /users/me/calendarList": "calendarList.insert",
    "PATCH /users/me/calendarList/{calendarId}": "calendarList.patch",
    "PUT /users/me/calendarList/{calendarId}": "calendarList.update",
    "DELETE /users/me/calendarList/{calendarId}": "calendarList.delete",
    "POST /users/me/calendarList/watch": "calendarList.watch",
    "GET /calendars/{calendarId}": "calendars.get",
    "POST /calendars": "calendars.insert",
    "PATCH /calendars/{calendarId}": "calendars.patch",
    "PUT /calendars/{calendarId}": "calendars.update",
    "DELETE /calendars/{calendarId}": "calendars.delete",
    "POST /calendars/{calendarId}/clear": "calendars.clear",
    "GET /calendars/{calendarId}/events": "events.list",
    "GET /calendars/{calendarId}/events/{eventId}": "events.get",
    "POST /calendars/{calendarId}/events": "events.insert",
    "PATCH /calendars/{calendarId}/events/{eventId}": "events.patch",
    "PUT /calendars/{calendarId}/events/{eventId}": "events.update",
    "DELETE /calendars/{calendarId}/events/{eventId}": "events.delete",
    "POST /calendars/{calendarId}/events/import": "events.import",
    "POST /calendars/{calendarId}/events/quickAdd": "events.quickAdd",
    "POST /calendars/{calendarId}/events/{eventId}/move": "events.move",
    "GET /calendars/{calendarId}/events/{eventId}/instances": "events.instances",
    "POST /calendars/{calendarId}/events/watch": "events.watch",
    "GET /calendars/{calendarId}/acl": "acl.list",
    "GET /calendars/{calendarId}/acl/{ruleId}": "acl.get",
    "POST /calendars/{calendarId}/acl": "acl.insert",
    "PATCH /calendars/{calendarId}/acl/{ruleId}": "acl.patch",
    "PUT /calendars/{calendarId}/acl/{ruleId}": "acl.update",
    "DELETE /calendars/{calendarId}/acl/{ruleId}": "acl.delete",
    "POST /calendars/{calendarId}/acl/watch": "acl.watch",
    "POST /freeBusy": "freeBusy.query",
    "GET /colors": "colors.get",
    "GET /users/me/settings": "settings.list",
    "GET /users/me/settings/{setting}": "settings.get",
    "POST /users/me/settings/watch": "settings.watch",
    "POST /channels/stop": "channels.stop",
}
MUTATING_TOOLS: frozenset[str] = frozenset(
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
        "settings.watch",
        "calendarList.watch",
        "events.watch",
        "acl.watch",
        "channels.stop",
    }
)
DECLARED_SUPPLIES: Mapping[str, frozenset[str]] = {
    "calendarList.list": frozenset({CALENDAR_ID}),
    "calendarList.get": frozenset({CALENDAR_ID}),
    "calendarList.insert": frozenset({CALENDAR_ID}),
    "calendars.get": frozenset({CALENDAR_ID}),
    "calendars.insert": frozenset({CALENDAR_ID}),
    "events.list": frozenset({EVENT_ID}),
    "events.get": frozenset({EVENT_ID}),
    "events.insert": frozenset({EVENT_ID}),
    "events.quickAdd": frozenset({EVENT_ID}),
    "events.import": frozenset({EVENT_ID}),
    "acl.list": frozenset({ACL_RULE_ID}),
    "acl.get": frozenset({ACL_RULE_ID}),
    "acl.insert": frozenset({ACL_RULE_ID}),
}


class CalendarToolContract(ToolContract):
    @property
    def is_mutating(self) -> bool:
        return self.name in MUTATING_TOOLS

    def required_identifier_types(self) -> frozenset[str]:
        return frozenset(
            (
                IDENTIFIER_PARAMS[param]
                for param in self.required_params
                if param in IDENTIFIER_PARAMS
            )
        )

    def consumable_identifier_types(self) -> frozenset[str]:
        return frozenset(
            (
                IDENTIFIER_PARAMS[param]
                for param in self.required_params | self.optional_params
                if param in IDENTIFIER_PARAMS
            )
        )


def parse_calendar_docs(docs_path: Path) -> dict[str, tuple[frozenset[str], frozenset[str]]]:
    """Parse REST Calendar docs into tool-name -> (required, optional) params."""
    payload = json.loads(docs_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not payload:
        raise ContractError(f"no tool entries in {docs_path}")
    out: dict[str, tuple[frozenset[str], frozenset[str]]] = {}
    for rest_key, entry in payload.items():
        tool_name = REST_TO_TOOL.get(str(rest_key))
        if tool_name is None:
            continue
        if not isinstance(entry, dict):
            out[tool_name] = (frozenset(), frozenset())
            continue
        required: set[str] = set()
        optional: set[str] = set()
        parameters = entry.get("parameters") or {}
        if isinstance(parameters, dict):
            for _group, params in parameters.items():
                if not isinstance(params, dict):
                    continue
                for param_name, spec in params.items():
                    if isinstance(spec, dict) and spec.get("required"):
                        required.add(param_name)
                    else:
                        optional.add(param_name)
        for match in re.finditer("\\{([A-Za-z0-9_]+)\\}", str(rest_key)):
            required.add(match.group(1))
        body = entry.get("body")
        if isinstance(body, dict):
            for param_name, spec in body.items():
                if isinstance(spec, dict) and spec.get("required"):
                    required.add(param_name)
                else:
                    optional.add(param_name)
        out[tool_name] = (frozenset(required), frozenset(optional - required))
    return out


def build_calendar_graph(*, docs_path: Path, service: str = "calendar") -> PrerequisiteGraph:
    params = parse_calendar_docs(docs_path)
    if not params:
        raise ContractError(f"no mapped calendar tools in {docs_path}")
    unknown_mutating = sorted(MUTATING_TOOLS - set(params))
    if unknown_mutating:
        raise ContractError(
            f"MUTATING_TOOLS names operations absent from the docs map: {unknown_mutating}"
        )
    tools: dict[str, ToolContract] = {}
    for tool_name, (required, optional) in params.items():
        tools[tool_name] = CalendarToolContract(
            name=tool_name,
            required_params=required,
            optional_params=optional,
            supplies=DECLARED_SUPPLIES.get(tool_name, frozenset()),
        )
    return PrerequisiteGraph(service=service, tools=tools)

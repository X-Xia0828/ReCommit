"""Service contracts."""

from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any

DEFAULT_AGENTDIFF_ROOT = Path("data/agent-diff")
LINEAR_DOCS_RELATIVE_PATH = Path("examples/linear/testsuites/linear_docs/linear_api_full_docs.json")
CALENDAR_DOCS_RELATIVE_PATH = Path(
    "examples/calendar/testsuites/calendar_docs/calendar_api_full_docs.json"
)
BOX_DOCS_RELATIVE_PATH = Path("examples/box/testsuites/box_docs/box_api_full_docs.json")
CALENDAR_REST_TO_TOOL: dict[str, str] = {
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
BOX_ROUTE_NAMES: dict[str, str] = {
    "GET /folders/{folder_id}": "GET /folders/{id}",
    "PUT /folders/{folder_id}": "PUT /folders/{id}",
    "DELETE /folders/{folder_id}": "DELETE /folders/{id}",
    "GET /folders/{folder_id}/items": "GET /folders/{id}/items",
    "GET /files/{file_id}": "GET /files/{id}",
    "PUT /files/{file_id}": "PUT /files/{id}",
    "DELETE /files/{file_id}": "DELETE /files/{id}",
    "GET /files/{file_id}/content": "GET /files/{id}/content",
    "POST /files/{file_id}/content": "POST /files/{id}/content",
    "GET /files/{file_id}/comments": "GET /files/{id}/comments",
    "GET /files/{file_id}/tasks": "GET /files/{id}/tasks",
    "PUT /comments/{comment_id}": "PUT /comments/{id}",
    "DELETE /comments/{comment_id}": "DELETE /comments/{id}",
    "PUT /tasks/{task_id}": "PUT /tasks/{id}",
    "DELETE /tasks/{task_id}": "DELETE /tasks/{id}",
    "GET /hubs/{hub_id}": "GET /hubs/{id}",
    "PUT /hubs/{hub_id}": "PUT /hubs/{id}",
    "DELETE /hubs/{hub_id}": "DELETE /hubs/{id}",
    "POST /hubs/{hub_id}/manage_items": "POST /hubs/{id}/manage_items",
    "GET /collections/{collection_id}": "GET /collections/{id}",
    "PUT /collections/{collection_id}": "PUT /collections/{id}",
    "DELETE /collections/{collection_id}": "DELETE /collections/{id}",
}


def _drop_publication_artifacts(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _drop_publication_artifacts(item)
            for (key, item) in value.items()
            if key not in {"token", "example_request"}
        }
    if isinstance(value, list):
        return [_drop_publication_artifacts(item) for item in value]
    return value


def _normalize_box_tool_name(name: str) -> str:
    mapped = BOX_ROUTE_NAMES.get(name, name)
    return re.sub("\\{(folder|file|comment|hub|task|collection)_id\\}", "{id}", mapped)


def load_public_linear_contracts(
    agentdiff_root: Path = DEFAULT_AGENTDIFF_ROOT,
) -> dict[str, dict[str, Any]]:
    from recommit.components.call_normalization import normalize_contracts_for_executor

    docs_path = agentdiff_root / LINEAR_DOCS_RELATIVE_PATH
    docs = json.loads(docs_path.read_text(encoding="utf-8"))
    if not isinstance(docs, dict) or not docs:
        raise ValueError(f"expected Linear tool contracts in {docs_path}")
    raw = {
        str(name): _drop_publication_artifacts(contract)
        for (name, contract) in sorted(docs.items())
        if isinstance(contract, dict)
    }
    return normalize_contracts_for_executor(raw)


def load_public_calendar_contracts(
    agentdiff_root: Path = DEFAULT_AGENTDIFF_ROOT,
) -> dict[str, dict[str, Any]]:
    from recommit.components.call_normalization import normalize_contracts_for_executor

    docs_path = agentdiff_root / CALENDAR_DOCS_RELATIVE_PATH
    docs = json.loads(docs_path.read_text(encoding="utf-8"))
    if not isinstance(docs, dict) or not docs:
        raise ValueError(f"expected Calendar tool contracts in {docs_path}")
    contracts: dict[str, dict[str, Any]] = {}
    for rest_key, contract in docs.items():
        tool_name = CALENDAR_REST_TO_TOOL.get(str(rest_key))
        if tool_name is None or not isinstance(contract, dict):
            continue
        contracts[tool_name] = _drop_publication_artifacts(contract)
    if not contracts:
        raise ValueError(f"no mapped Calendar tool contracts in {docs_path}")
    return normalize_contracts_for_executor(dict(sorted(contracts.items())))


def load_public_box_contracts(
    agentdiff_root: Path = DEFAULT_AGENTDIFF_ROOT,
) -> dict[str, dict[str, Any]]:
    from recommit.components.call_normalization import normalize_contracts_for_executor

    docs_path = agentdiff_root / BOX_DOCS_RELATIVE_PATH
    docs = json.loads(docs_path.read_text(encoding="utf-8"))
    if not isinstance(docs, dict) or not docs:
        raise ValueError(f"expected Box tool contracts in {docs_path}")
    contracts: dict[str, dict[str, Any]] = {}
    for rest_key, contract in docs.items():
        if not isinstance(contract, dict):
            continue
        tool_name = _normalize_box_tool_name(str(rest_key))
        contracts[tool_name] = _drop_publication_artifacts(contract)
    if not contracts:
        raise ValueError(f"no Box tool contracts in {docs_path}")
    return normalize_contracts_for_executor(dict(sorted(contracts.items())))

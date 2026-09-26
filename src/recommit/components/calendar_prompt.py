"""Calendar prompt."""

from __future__ import annotations
import json
import re
from typing import Any
from recommit.components.service_prompt import build_service_prompt


def extract_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    fenced = re.search("```(?:json)?\\s*(\\{.*?\\})\\s*```", text, flags=re.S)
    if fenced:
        text = fenced.group(1)
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        obj = json.loads(text)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def parse_calls(raw: str) -> tuple[list[dict[str, Any]], bool]:
    from recommit.components.call_normalization import normalize_tool_arguments

    obj = extract_json_object(raw)
    if not obj:
        return ([], False)
    calls = obj.get("calls") or obj.get("tool_calls") or obj.get("actions") or []
    if not isinstance(calls, list):
        return ([], False)
    parsed = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        tool = call.get("tool") or call.get("name") or call.get("endpoint") or call.get("api")
        if not tool:
            continue
        args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        parsed.append({"tool": str(tool), "arguments": normalize_tool_arguments(args)})
    return (parsed, bool(parsed))


def table_summary(
    rows: list[dict[str, Any]], fields: list[str], limit: int = 80
) -> list[dict[str, Any]]:
    out = []
    for row in rows[:limit]:
        out.append({field: row.get(field) for field in fields if field in row})
    return out


def calendar_state_summary(seed: dict[str, Any]) -> str:
    payload = {
        "calendars": table_summary(
            seed.get("calendars", []), ["id", "summary", "description", "time_zone"], 120
        ),
        "calendar_list_entries": table_summary(
            seed.get("calendar_list_entries", []),
            [
                "id",
                "user_id",
                "calendar_id",
                "access_role",
                "primary",
                "selected",
                "hidden",
                "deleted",
            ],
            120,
        ),
        "calendar_events": table_summary(
            seed.get("calendar_events", []),
            [
                "id",
                "calendar_id",
                "summary",
                "description",
                "location",
                "status",
                "start_datetime",
                "end_datetime",
            ],
            220,
        ),
        "calendar_acl_rules": table_summary(
            seed.get("calendar_acl_rules", []),
            ["id", "calendar_id", "role", "scope_type", "scope_value"],
            160,
        ),
        "calendar_users": table_summary(
            seed.get("calendar_users", []), ["id", "email", "display_name"], 80
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_repair_prompt(
    *,
    task: str,
    failed_raw: str,
    failed_tools: list[str],
    summary: str,
    mode: str,
    public_contracts: dict[str, Any],
) -> tuple[str, str]:
    return build_service_prompt(
        service="calendar",
        mode=mode,
        task=task,
        failed_raw=failed_raw,
        failed_tools=failed_tools,
        summary=summary,
        public_contracts=public_contracts,
    )

"""Box prompt."""

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


def box_state_summary(seed: dict[str, Any]) -> str:
    payload = {
        "box_folders": table_summary(
            seed.get("box_folders", []), ["id", "name", "parent_id", "item_status"], 160
        ),
        "box_files": table_summary(
            seed.get("box_files", []),
            ["id", "name", "parent_id", "item_status", "description"],
            220,
        ),
        "box_comments": table_summary(
            seed.get("box_comments", []), ["id", "item_id", "message", "created_by"], 120
        ),
        "box_hubs": table_summary(seed.get("box_hubs", []), ["id", "title", "description"], 80),
        "box_hub_items": table_summary(
            seed.get("box_hub_items", []), ["hub_id", "item_id", "item_type"], 80
        ),
        "box_tasks": table_summary(
            seed.get("box_tasks", []), ["id", "item_id", "action", "message"], 80
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
        service="box",
        mode=mode,
        task=task,
        failed_raw=failed_raw,
        failed_tools=failed_tools,
        summary=summary,
        public_contracts=public_contracts,
    )

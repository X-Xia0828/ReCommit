"""Slack prompt."""

from __future__ import annotations
import json
import re
from typing import Any
from recommit.components.repair_prompt import build_schema_prompt


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


def build_repair_prompt(
    *,
    task: str,
    failed_raw: str,
    failed_tools: list[str],
    summary: str,
    mode: str,
    public_contracts: dict[str, Any],
) -> tuple[str, str]:
    return build_schema_prompt(
        mode=mode,
        task=task,
        visible_context={"slack_state_summary": summary[:12000]},
        failed_attempt={"tool_sequence": failed_tools, "raw_output": failed_raw[:1800]},
        public_tool_names=list(public_contracts),
        public_tool_contracts=public_contracts,
        output_contract='{"calls":[{"tool":"tool.name","arguments":{...}}]}',
    )

"""Linear prompt."""

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


def linear_state_summary(seed: dict[str, Any]) -> str:
    """Compact visible Linear state for repair prompts."""
    payload = {
        "teams": table_summary(seed.get("teams", []), ["id", "name", "key", "displayName"], 80),
        "users": table_summary(seed.get("users", []), ["id", "name", "displayName", "email"], 120),
        "workflow_states": table_summary(
            seed.get("workflow_states", []), ["id", "teamId", "name", "type"], 160
        ),
        "issue_labels": table_summary(seed.get("issue_labels", []), ["id", "teamId", "name"], 160),
        "issues": table_summary(
            seed.get("issues", []),
            [
                "id",
                "identifier",
                "title",
                "teamId",
                "stateId",
                "assigneeId",
                "priority",
                "priorityLabel",
                "description",
            ],
            220,
        ),
        "comments": table_summary(
            seed.get("comments", []), ["id", "issueId", "userId", "body"], 160
        ),
        "issue_relations": table_summary(
            seed.get("issue_relations", []), ["id", "issueId", "relatedIssueId", "type"], 160
        ),
        "issue_label_issue_association": table_summary(
            seed.get("issue_label_issue_association", []), ["issue_id", "issue_label_id"], 160
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
        service="linear",
        mode=mode,
        task=task,
        failed_raw=failed_raw,
        failed_tools=failed_tools,
        summary=summary,
        public_contracts=public_contracts,
    )

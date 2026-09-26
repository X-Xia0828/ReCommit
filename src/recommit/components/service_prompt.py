"""Service prompt."""

from __future__ import annotations
from typing import Any
from recommit.components.repair_prompt import build_schema_prompt

SERVICE_VISIBLE_CONTEXT_KEY = {
    "linear": "linear_state_summary",
    "calendar": "calendar_state_summary",
    "box": "box_state_summary",
}
SERVICE_OUTPUT_CONTRACT = {
    "linear": '{"calls":[{"tool":"tool.name","arguments":{...}}]}',
    "calendar": '{"calls":[{"tool":"tool.name","arguments":{...}}]}',
    "box": '{"calls":[{"tool":"METHOD /route","arguments":{...}}]}',
}


def build_service_prompt(
    *,
    service: str,
    mode: str,
    task: str,
    failed_raw: str,
    failed_tools: list[str],
    summary: str,
    public_contracts: dict[str, Any],
) -> tuple[str, str]:
    if service not in SERVICE_VISIBLE_CONTEXT_KEY:
        raise ValueError(f"unsupported service: {service}")
    visible_key = SERVICE_VISIBLE_CONTEXT_KEY[service]
    return build_schema_prompt(
        mode=mode,
        task=task,
        visible_context={visible_key: summary[:18000]},
        failed_attempt={"tool_sequence": failed_tools, "raw_output": failed_raw[:2200]},
        public_tool_names=list(public_contracts),
        public_tool_contracts=public_contracts,
        output_contract=SERVICE_OUTPUT_CONTRACT[service],
    )

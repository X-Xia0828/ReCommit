"""Repair prompt."""

from __future__ import annotations
import json
from collections.abc import Mapping, Sequence
from typing import Any

PROMPT_MODES = ("schema",)


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_schema_prompt(
    *,
    mode: str,
    task: str,
    visible_context: Any,
    failed_attempt: Any,
    public_tool_names: Sequence[str],
    public_tool_contracts: Mapping[str, Any] | Sequence[Any] | None,
    output_contract: str,
) -> tuple[str, str]:
    """Build the public-schema prompt used by expressive realization."""
    if mode not in PROMPT_MODES:
        raise ValueError(f"unsupported prompt mode: {mode}")
    if public_tool_contracts is None:
        raise ValueError(f"{mode} requires public tool contracts")
    system = "Return exactly one executable JSON object and no markdown."
    reflection = ""
    prompt = f"Produce an autoregressive recovery action for the current tool-agent state. Use only the user request, visible interaction history, observable tool feedback, and the public interface information included below.\n\n{reflection}User request:\n{task}\n\nPrevious failed attempt:\n{compact_json(failed_attempt)}\n\nVisible context:\n{compact_json(visible_context)}\n\nPublic tool names:\n{compact_json(sorted(set(public_tool_names)))}\n\n"
    prompt += f"Public tool contracts:\n{compact_json(public_tool_contracts)}\n\n"
    prompt += f"Required output:\n{output_contract}"
    return (system, prompt)

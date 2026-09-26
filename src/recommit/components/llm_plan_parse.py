"""Llm plan parse."""

from __future__ import annotations
import copy
import json
import re
from typing import Any, Mapping, Sequence
from recommit.components.call_normalization import normalize_tool_arguments

MASK_TOKENS = frozenset({"[M]", "<MASK>", "<mask>", "MASK"})


def extract_json_object(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
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


def is_masked(value: Any) -> bool:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped in MASK_TOKENS:
            return True
        return "[M]" in stripped
    if isinstance(value, Mapping):
        return any((is_masked(v) for v in value.values()))
    if isinstance(value, list):
        return any((is_masked(v) for v in value))
    return False


def merge_arguments(
    skeleton_args: Mapping[str, Any] | None,
    fill_args: Mapping[str, Any] | None,
    *,
    allow_extra: bool = True,
) -> dict[str, Any]:
    """Merge LLM fills into skeleton args without dropping concrete skeleton values."""
    out: dict[str, Any] = copy.deepcopy(dict(skeleton_args or {}))
    if not isinstance(fill_args, Mapping):
        return out
    for key, fill_value in fill_args.items():
        if key not in out:
            if allow_extra:
                out[key] = copy.deepcopy(fill_value)
            continue
        skeleton_value = out[key]
        if isinstance(skeleton_value, Mapping):
            if isinstance(fill_value, Mapping):
                out[key] = merge_arguments(skeleton_value, fill_value, allow_extra=allow_extra)
        elif is_masked(skeleton_value):
            out[key] = copy.deepcopy(fill_value)
    return out


def parse_llm_calls(raw: str) -> tuple[list[dict[str, Any]], bool]:
    obj = extract_json_object(raw)
    if not obj:
        return ([], False)
    calls = obj.get("calls") or obj.get("tool_calls") or obj.get("actions") or []
    if not isinstance(calls, list):
        return ([], False)
    parsed: list[dict[str, Any]] = []
    for call in calls:
        if not isinstance(call, Mapping):
            continue
        tool = call.get("tool") or call.get("name") or call.get("endpoint") or call.get("api")
        if not tool:
            continue
        args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        parsed.append({"tool": str(tool), "arguments": normalize_tool_arguments(args)})
    return (parsed, bool(parsed))


def _fill_args_by_index(
    obj: Mapping[str, Any], n: int, *, tools: Sequence[str] | None = None
) -> list[dict[str, Any] | None]:
    """Extract per-step argument fills aligned to skeleton length."""
    fills: list[dict[str, Any] | None] = [None] * n
    raw_fills = obj.get("fills")
    if isinstance(raw_fills, list):
        for item in raw_fills:
            if not isinstance(item, Mapping):
                continue
            idx = item.get("i", item.get("index", item.get("step")))
            args = item.get("arguments") if isinstance(item.get("arguments"), dict) else None
            if args is None and isinstance(item.get("args"), dict):
                args = item.get("args")
            if isinstance(idx, int) and 0 <= idx < n and isinstance(args, dict):
                fills[idx] = args
        if any(fills):
            return fills
    raw_args = obj.get("arguments")
    if isinstance(raw_args, list) and raw_args:
        for idx, args in enumerate(raw_args[:n]):
            if isinstance(args, dict):
                fills[idx] = args
        if any(fills):
            return fills
    calls = obj.get("calls") or obj.get("tool_calls") or obj.get("actions") or []
    if isinstance(calls, list):
        if tools is not None:
            names = [
                str(
                    call.get("tool")
                    or call.get("name")
                    or call.get("endpoint")
                    or call.get("api")
                    or ""
                )
                if isinstance(call, Mapping)
                else ""
                for call in calls
            ]
            if names != list(tools):
                # A shortened/reordered named plan is not a positional fill.
                # Only unambiguous tool identities can recover its arguments;
                # repeated tools remain unresolved unless the entire sequence
                # matches. The skeleton's tools and order never change.
                for idx, tool in enumerate(tools):
                    if tools.count(tool) != 1 or names.count(tool) != 1:
                        continue
                    call = calls[names.index(tool)]
                    args = call.get("arguments", call.get("args"))
                    if isinstance(args, dict):
                        fills[idx] = args
                return fills
        for idx, call in enumerate(calls[:n]):
            if not isinstance(call, Mapping):
                continue
            args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
            if not args and isinstance(call.get("args"), dict):
                args = call.get("args")
            fills[idx] = args if isinstance(args, dict) else {}
    return fills


def bind_skeleton_from_llm(
    raw: str, skeleton: Sequence[Mapping[str, Any]], *, allow_extra_keys: bool = True
) -> tuple[list[dict[str, Any]], bool]:
    """Bind LLM output onto a fixed skeleton: tools/order locked, only slots filled."""
    skeleton_list = [
        dict(step) for step in skeleton if isinstance(step, Mapping) and step.get("tool")
    ]
    if not skeleton_list:
        return ([], False)
    obj = extract_json_object(raw)
    if not obj:
        return ([], False)
    fills = _fill_args_by_index(
        obj, len(skeleton_list), tools=[str(step["tool"]) for step in skeleton_list]
    )
    if not any(fills):
        return ([], False)
    bound: list[dict[str, Any]] = []
    for step, fill in zip(skeleton_list, fills):
        skel_args = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
        merged = merge_arguments(skel_args, fill, allow_extra=allow_extra_keys)
        bound.append({"tool": str(step.get("tool")), "arguments": normalize_tool_arguments(merged)})
    return (bound, True)


def count_masked_slots(plan: Sequence[Mapping[str, Any]]) -> int:
    """Number of still-masked leaf slots across a plan's arguments."""

    def walk(value: Any) -> int:
        if isinstance(value, Mapping):
            return sum((walk(item) for item in value.values()))
        if isinstance(value, list):
            if is_masked(value):
                return 1
            return sum((walk(item) for item in value))
        return 1 if is_masked(value) else 0

    return sum((walk(call.get("arguments") or {}) for call in plan if isinstance(call, Mapping)))


def bind_llm_plan(
    raw: str, skeleton: Sequence[Mapping[str, Any]], *, binder: str = "skeleton"
) -> tuple[list[dict[str, Any]], bool]:
    if binder == "freeform":
        return parse_llm_calls(raw)
    if binder in {"skeleton", "layered", "evidence", "typed"}:
        return bind_skeleton_from_llm(raw, skeleton)
    raise ValueError(f"unknown binder kind: {binder!r}")

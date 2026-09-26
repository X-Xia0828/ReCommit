"""Evidence seed."""

from __future__ import annotations
import copy
import json
import re
from typing import Any, Mapping, Sequence

MASK_TOKENS = frozenset({"[M]", "<MASK>", "<mask>", "MASK"})
_PLACEHOLDER_RE = re.compile(
    "(?i)^(SEARCH_RESULT(_ID)?|RESULT_ID|PLACEHOLDER|TODO|TBD|YOUR_.+|<.*>|\\[.*\\]|xxx+)$"
)


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


def is_usable_evidence_value(value: Any) -> bool:
    if value is None or value == "" or is_masked(value):
        return False
    if isinstance(value, str):
        text = value.strip()
        if not text or _PLACEHOLDER_RE.match(text):
            return False
        if text.endswith("_ID") and text.upper() == text:
            return False
    return True


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


def _normalize_call(item: Mapping[str, Any]) -> dict[str, Any] | None:
    tool = item.get("tool") or item.get("name") or item.get("endpoint") or item.get("api")
    if not tool:
        return None
    args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
    return {"tool": str(tool), "arguments": dict(args)}


def _calls_from_failed_raw(failed_raw: str) -> list[dict[str, Any]]:
    text = (failed_raw or "").strip()
    if not text:
        return []

    def from_calls_list(calls: Any) -> list[dict[str, Any]]:
        if not isinstance(calls, list):
            return []
        out: list[dict[str, Any]] = []
        for item in calls:
            if isinstance(item, Mapping):
                parsed = _normalize_call(item)
                if parsed:
                    out.append(parsed)
        return out

    obj = extract_json_object(text)
    if obj is not None:
        if "calls" in obj:
            return from_calls_list(obj.get("calls"))
        proposal = obj.get("proposal")
        if isinstance(proposal, Mapping) and "calls" in proposal:
            return from_calls_list(proposal.get("calls"))
        if "tool" in obj or "name" in obj:
            one = _normalize_call(obj)
            return [one] if one else []
    try:
        parsed = json.loads(text)
    except Exception:
        return []
    if isinstance(parsed, list):
        return from_calls_list(parsed)
    return []


def _merge_seed(
    skeleton_args: Mapping[str, Any] | None,
    evidence_args: Mapping[str, Any] | None,
    *,
    overwrite_concrete: bool,
) -> dict[str, Any]:
    out: dict[str, Any] = copy.deepcopy(dict(skeleton_args or {}))
    if not isinstance(evidence_args, Mapping):
        return out
    for key, value in evidence_args.items():
        if not is_usable_evidence_value(value):
            continue
        if key not in out:
            out[key] = copy.deepcopy(value)
            continue
        current = out[key]
        if is_masked(current):
            out[key] = copy.deepcopy(value)
        elif isinstance(current, Mapping) and isinstance(value, Mapping):
            out[key] = _merge_seed(current, value, overwrite_concrete=overwrite_concrete)
        elif overwrite_concrete:
            out[key] = copy.deepcopy(value)
    return out


def seed_plan_from_failed_attempt(
    skeleton: Sequence[Mapping[str, Any]], failed_raw: str, *, overwrite_concrete: bool = False
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (seeded_plan, stats). Default: fill [M] only (no overwrite)."""
    evidence = _calls_from_failed_raw(failed_raw)
    used = [False] * len(evidence)
    seeded: list[dict[str, Any]] = []
    n_seeded_args = 0
    n_matched_steps = 0
    for step in skeleton:
        call = {
            "tool": step.get("tool"),
            "arguments": copy.deepcopy(dict(step.get("arguments") or {})),
        }
        tool = str(call.get("tool") or "")
        match_idx = None
        for i, ev in enumerate(evidence):
            if used[i]:
                continue
            if str(ev.get("tool") or "").strip().lower() == tool.strip().lower():
                match_idx = i
                break
        if match_idx is not None:
            used[match_idx] = True
            n_matched_steps += 1
            before = json.dumps(call["arguments"], sort_keys=True, ensure_ascii=False)
            call["arguments"] = _merge_seed(
                call["arguments"],
                evidence[match_idx].get("arguments") or {},
                overwrite_concrete=overwrite_concrete,
            )
            after = json.dumps(call["arguments"], sort_keys=True, ensure_ascii=False)
            if before != after:
                n_seeded_args += 1
        seeded.append(call)
    stats = {
        "evidence_calls": len(evidence),
        "matched_steps": n_matched_steps,
        "steps_with_arg_changes": n_seeded_args,
        "skeleton_steps": len(seeded),
    }
    return (seeded, stats)

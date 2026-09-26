"""Content gate."""

from __future__ import annotations
import copy
import re
from typing import Any, Callable, Collection, Mapping, Sequence
from recommit.components.argument_provenance import (
    EVIDENCE_PROVENANCE,
    collect_plan_provenance_events,
)
from recommit.components.llm_plan_parse import is_masked
from recommit.components.llm_plan_parse import count_masked_slots

Plan = list[dict[str, Any]]
BindFull = Callable[[Plan], Plan]
CONTENT_KEYS = frozenset(
    {
        "text",
        "topic",
        "name",
        "query",
        "purpose",
        "blocks",
        "title",
        "body",
        "description",
        "message",
        "content",
        "tags",
        "summary",
        "location",
        "q",
    }
)


def rules_preview_plan(plan: Sequence[Mapping[str, Any]], bind_full: BindFull) -> Plan:
    return bind_full([copy.deepcopy(dict(call)) for call in plan])


def _masked_slots(plan: Sequence[Mapping[str, Any]]) -> list[tuple[int, str, str]]:
    """Return (step_idx, tool, arg_key) for top-level masked arguments."""
    out: list[tuple[int, str, str]] = []
    for idx, step in enumerate(plan):
        tool = str(step.get("tool") or "")
        args = step.get("arguments") or {}
        if not isinstance(args, Mapping):
            continue
        for key, value in args.items():
            if is_masked(value):
                out.append((idx, tool, str(key)))
            elif isinstance(value, Mapping):
                for nested_key, nested_val in value.items():
                    if is_masked(nested_val):
                        out.append((idx, tool, f"{key}.{nested_key}"))
    return out


def _preview_has_concrete(
    preview: Sequence[Mapping[str, Any]], step_idx: int, arg_key: str
) -> bool:
    if step_idx >= len(preview):
        return False
    args = preview[step_idx].get("arguments") or {}
    if not isinstance(args, Mapping):
        return False
    if "." in arg_key:
        (parent, child) = arg_key.split(".", 1)
        nested = args.get(parent)
        if not isinstance(nested, Mapping):
            return False
        return child in nested and (not is_masked(nested.get(child)))
    return arg_key in args and (not is_masked(args.get(arg_key)))


def evidence_covered(
    plan_after_ids: Sequence[Mapping[str, Any]],
    preview: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    *,
    content_keys: Collection[str] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """True only when all masked slots are evidence-bound (not schema_default).

    ``content_keys`` names the free-text slots whose fills must carry an evidence
    tag. It defaults to this module's agent-diff set; a caller on another
    benchmark must pass its own, because a slot missing from the set skips the
    provenance check entirely and is silently treated as covered. That direction
    matters: an unrecognised content slot makes the gate fail *open* and skip the
    model, not fail closed.
    """
    keys = CONTENT_KEYS if content_keys is None else frozenset(content_keys)
    masked = _masked_slots(plan_after_ids)
    schema_defaults = [e for e in events if e.get("provenance") == "schema_default"]
    evidence_events = [e for e in events if e.get("provenance") in EVIDENCE_PROVENANCE]
    detail = {
        "masked_slots": len(masked),
        "schema_default_events": len(schema_defaults),
        "evidence_events": len(evidence_events),
        "provenance_events": len(events),
        "preview_residual": count_masked_slots(preview),
    }
    if schema_defaults:
        detail["reason"] = "schema_default_present"
        return (False, detail)
    missing: list[str] = []
    unverified_content: list[str] = []
    for step_idx, tool, key in masked:
        leaf = key.split(".")[-1]
        if not _preview_has_concrete(preview, step_idx, key):
            missing.append(f"{tool}.{key}")
            continue
        if leaf in keys:
            key_events = [
                e
                for e in events
                if e.get("key") in {leaf, key} and str(e.get("tool") or "") == tool
            ]
            if not key_events:
                unverified_content.append(f"{tool}.{key}")
            elif any((e.get("provenance") not in EVIDENCE_PROVENANCE for e in key_events)):
                unverified_content.append(f"{tool}.{key}")
    if missing:
        detail["reason"] = "masked_or_stripped"
        detail["missing"] = missing[:8]
        return (False, detail)
    if unverified_content:
        detail["reason"] = "unverified_content_fill"
        detail["unverified_content"] = unverified_content[:8]
        return (False, detail)
    detail["reason"] = "evidence_bound"
    return (True, detail)


def apply_content_gate(
    plan_after_ids: Sequence[Mapping[str, Any]],
    failed_raw: str,
    bind_full: BindFull,
    *,
    gate_mode: str = "evidence",
    content_keys: Collection[str] | None = None,
) -> tuple[Plan, dict[str, Any], bool, bool]:
    """Skip HOW only when task/state binding covers the unresolved arguments.

    Failed output is deliberately not evidence for skipping or locking slots.
    The argument remains accepted for compatibility with callers.
    """
    preview = rules_preview_plan(plan_after_ids, bind_full)
    events = collect_plan_provenance_events(preview)
    covered, detail = evidence_covered(plan_after_ids, preview, events, content_keys=content_keys)
    if gate_mode == "cover_all":
        covered = count_masked_slots(preview) == 0
    # PATCH/update schemas deliberately allow optional change fields. An empty
    # set of masks says nothing about which changes the user requested. Let HOW
    # supply that payload even when target identifiers have been resolved.
    if any(
        re.search(r"^PUT |(?:^|[.])(?:patch|update)$|Update$", str(c.get("tool") or ""))
        for c in plan_after_ids
    ):
        covered = False
        detail["reason"] = "open_update_payload_requires_how"
    stats = {
        **detail,
        "gate_mode": gate_mode,
        "gate": "evidence_cover_all" if covered else "grounded_slots_only",
        "steps_with_arg_changes": 0,
        "skeleton_steps": len(plan_after_ids),
    }
    if covered:
        return preview, stats, True, True
    return copy.deepcopy(list(plan_after_ids)), stats, False, False

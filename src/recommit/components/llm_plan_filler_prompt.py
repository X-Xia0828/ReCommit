"""Llm plan filler prompt."""

from __future__ import annotations
from typing import Any, Mapping, Sequence
from recommit.components.generation import compact_json

SERVICE_META = {
    "slack": {
        "system": "You bind masked slots for a Slack recovery plan.\nReturn exactly one JSON object and no markdown.",
        "fill_hints": "Slack-specific rules:\n- Resolve channel/user ids from the visible state summary when possible.\n- Keep message text faithful to the user task.\n- For a reply, resolve the requested parent message from the visible messages and supply thread_ts; do not confuse the reply body with the parent description.\n- Put Block Kit structures in the blocks array, not serialized inside text. Follow the requested structure; text is the plain-text fallback.\n- Planned reads have not executed yet: use only the public evidence actually shown, without inventing their results.",
    },
    "linear": {
        "system": "You bind masked slots for a Linear recovery plan.\nReturn exactly one JSON object and no markdown.",
        "fill_hints": "Linear-specific rules:\n- Resolve team/issue/user ids from the visible state summary.\n- Keep titles and bodies faithful to the user task.",
    },
    "calendar": {
        "system": "You bind masked slots for a Google Calendar recovery plan.\nReturn exactly one JSON object and no markdown.",
        "fill_hints": "Calendar-specific rules:\n- For events.insert/update/patch, you may add nested start/end objects ({dateTime, timeZone}) when the task specifies times.\n- Fill watch/stop channel fields (id, type, address, resourceId) from the task and visible state.",
    },
    "box": {
        "system": "You bind masked slots for a Box recovery plan.\nReturn exactly one JSON object and no markdown.",
        "fill_hints": "Box-specific rules:\n- Resolve file/folder ids from the visible state summary.\n- You may add nested argument fields required by the tool contract.",
    },
}
SKELETON_CONSTRAINT = "- You may add nested/optional argument fields on an existing step, but you must not add, remove, rename, or reorder tools."
LAYERED_CONSTRAINT = "- You may add nested/optional argument fields on an existing step, but you must not add, remove, rename, or reorder tools.\n- The skeleton contains task/state bindings and interface defaults, not values copied from the failed attempt. Keep these bindings and fill unresolved arguments; optional fields may also be required by the request."
FREEFORM_CONSTRAINT = "- The skeleton is a starting point: you may adjust the call list when a tool is missing or unnecessary, but every listed obligation must still be satisfied."


def obligation_inventory(obligations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in obligations:
        effect = str(item.get("effect") or "")
        if not effect:
            continue
        out.append({"effect": effect, "slots": dict(item.get("slots") or {})})
    return out


def compact_contract_entry(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only argument field names/types for prompt size (full schema is huge)."""
    args = contract.get("arguments")
    if isinstance(args, Mapping):
        slim: dict[str, Any] = {}
        for key, spec in args.items():
            if isinstance(spec, Mapping):
                slim[key] = {k: spec[k] for k in ("type", "required", "description") if k in spec}
            else:
                slim[key] = spec
        return {"arguments": slim}
    return dict(contract)


def filter_contracts(
    contracts: Mapping[str, Any], tools: Sequence[str], *, compact: bool = True
) -> dict[str, Any]:
    wanted = set(tools)
    out: dict[str, Any] = {}
    for name in sorted(wanted):
        if name not in contracts:
            continue
        entry = contracts[name]
        out[name] = compact_contract_entry(entry) if compact else entry
    return out


def build_llm_fill_prompt(
    *,
    service: str,
    task: str,
    state_summary: str,
    failed_tools: Sequence[str],
    failed_raw: str,
    obligations: Sequence[Mapping[str, Any]],
    skeleton_plan: Sequence[Mapping[str, Any]],
    public_contracts: Mapping[str, Any],
    execution_feedback: Sequence[Mapping[str, Any]] | None = None,
    binder: str = "skeleton",
) -> tuple[str, str]:
    meta = SERVICE_META[service]
    tools = [str(call.get("tool") or "") for call in skeleton_plan if call.get("tool")]
    contracts = filter_contracts(public_contracts, tools)
    freeform = binder == "freeform"
    if freeform:
        constraint = FREEFORM_CONSTRAINT
    elif binder in {"layered", "evidence", "typed"}:
        constraint = LAYERED_CONSTRAINT
    else:
        constraint = SKELETON_CONSTRAINT
    context_limit = 12000 if service == "slack" else 18000
    prompt = f"Bind the masked slots of a recovery skeleton.\n\nArchitecture:\n- The obligation inventory lists every mutating effect still required (WHAT).\n- The skeleton plan gives tool names and call order derived from the contract graph (GRAPH).\n- Your job is HOW: replace every [M] with a concrete value.\n- The failed proposal is fallible reference material, not an authoritative binding. Check its targets and content against the request and public state; correct wrong values and placeholders. Use public identifiers, never invent them.\n{constraint}\n\nUser task:\n{task.strip()}\n\nVisible state summary:\n{state_summary[:context_limit]}\n\nFailed agent tool sequence:\n{compact_json(list(failed_tools))}\n\nFailed raw proposal excerpt:\n{(failed_raw or '')[:1800]}\n\nObligation inventory:\n{compact_json(obligation_inventory(obligations))}\n\nSkeleton plan ({('editable' if freeform else 'tools locked')}):\n{compact_json(list(skeleton_plan))}\n\nPublic tool contracts (compact):\n{compact_json(contracts)}\n\n"
    fill_hints = meta.get("fill_hints")
    if fill_hints:
        prompt += f"{fill_hints}\n\n"
    if execution_feedback:
        prompt += f"Previous attempt execution feedback:\n{compact_json(list(execution_feedback)[:16])}\n\n"
    n = len([c for c in skeleton_plan if c.get("tool")])
    if freeform:
        prompt += 'Return exactly one compact JSON object:\n{"calls":[{"tool":"<tool name>","arguments":{...}}, ...]}\nRules: JSON only; no [M] may remain; resolve entities by title/name from the task when ids are unknown.'
    else:
        prompt += f'Return exactly one compact JSON object with one of these shapes:\n1) {{"fills":[{{"i":0,"arguments":{{...}}}}, ...]}}\n2) {{"calls":[{{"tool":"<same as skeleton>","arguments":{{...}}}}, ...]}}\nRules: JSON only; exactly align to the {n} skeleton steps; keep each tool name identical to the skeleton; fill every [M]; resolve entities by title/name from the task when ids are unknown.'
    return (meta["system"], prompt)

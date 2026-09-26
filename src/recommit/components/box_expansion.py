"""Box expansion."""

from __future__ import annotations
import copy
from collections import OrderedDict
from typing import Any, Mapping, Sequence
from recommit.components.box_graph import MUTATING_TOOLS
from recommit.components.slack_graph import PrerequisiteGraph
from recommit.components.contract_slot_coercion import BOX_SLOT_KINDS
from recommit.components.contract_slot_coercion import coerce_slots as coerce_slot_dict

READ_DEFAULTS: dict[str, dict[str, Any]] = {
    "GET /search": {"query": "[M]"},
    "GET /folders/{id}": {"id": "[M]"},
    "GET /folders/{id}/items": {"id": "[M]"},
    "GET /files/{id}": {"id": "[M]"},
    "GET /hubs": {},
    "GET /collections": {},
    "GET /users/me": {},
}
WRITE_DEFAULTS: dict[str, dict[str, Any]] = {
    "POST /folders": {"name": "[M]", "parent_id": "[M]"},
    "PUT /folders/{id}": {"id": "[M]"},
    "DELETE /folders/{id}": {"id": "[M]"},
    "POST /files/content": {"name": "[M]", "parent_id": "[M]"},
    "PUT /files/{id}": {"id": "[M]"},
    "DELETE /files/{id}": {"id": "[M]"},
    "POST /comments": {"item": {"id": "[M]", "type": "file"}, "message": "[M]"},
    "PUT /comments/{id}": {"id": "[M]", "message": "[M]"},
    "DELETE /comments/{id}": {"id": "[M]"},
    "POST /hubs": {"title": "[M]"},
    "PUT /hubs/{id}": {"id": "[M]"},
    "POST /hubs/{id}/manage_items": {
        "id": "[M]",
        "operations": [{"action": "add", "item": {"id": "[M]", "type": "folder"}}],
    },
    "POST /collections": {"name": "[M]"},
    "POST /tasks": {"item": {"id": "[M]", "type": "file"}, "message": "[M]"},
    "PUT /tasks/{id}": {"id": "[M]"},
    "DELETE /tasks/{id}": {"id": "[M]"},
}
SUPPLIER_PRIORITY: dict[str, list[str]] = {
    "file_id": ["GET /search", "GET /folders/{id}/items", "GET /files/{id}", "POST /files/content"],
    "folder_id": ["GET /search", "GET /folders/{id}", "GET /folders/{id}/items", "POST /folders"],
    "comment_id": ["GET /files/{id}"],
    "hub_id": ["GET /hubs", "POST /hubs"],
    "task_id": ["GET /files/{id}"],
    "user_id": ["GET /users/me"],
    "collection_id": ["GET /collections"],
}


def _choose_supplier(
    graph: PrerequisiteGraph, id_type: str, effect: str, *, include_mutating: bool = False
) -> str | None:
    candidates = list(
        graph.prerequisites_of(effect, include_mutating=include_mutating).get(id_type, ())
    )
    if not candidates:
        candidates = list(graph.suppliers_of(id_type, include_mutating=include_mutating) - {effect})
    if not candidates:
        return None
    priority = SUPPLIER_PRIORITY.get(id_type, [])
    read_candidates = [tool for tool in candidates if tool in graph.read_tools()]
    for tool in priority:
        if tool in read_candidates:
            return tool
    return sorted(read_candidates)[0] if read_candidates else None


def _read_call_for(supplier: str, write_args: Mapping[str, Any]) -> dict[str, Any]:
    """Read skeleton for one write, seeded with identifiers the write already has."""
    args = dict(READ_DEFAULTS.get(supplier, {}))
    if "id" in args and write_args.get("id") not in (None, "", "[M]"):
        args["id"] = write_args["id"]
    return {"tool": supplier, "arguments": args}


def expand_box_obligations(
    obligations: Sequence[Mapping[str, Any]],
    graph: PrerequisiteGraph,
    *,
    interleave_reads: bool = False,
    coerce_slots: bool = False,
) -> list[dict[str, Any]]:
    """Turn Box obligations into ordered calls with prerequisite reads.

    ``interleave_reads`` linearises the obligation graph one obligation at a
    time so each write's lookup runs immediately before it (see the Calendar
    expander for the rationale).
    """
    reads_needed: OrderedDict[str, None] = OrderedDict()
    write_calls: list[dict[str, Any]] = []
    interleaved: list[dict[str, Any]] = []
    for item in obligations:
        effect = str(item.get("effect") or "")
        if effect not in MUTATING_TOOLS:
            continue
        base = copy.deepcopy(WRITE_DEFAULTS.get(effect, {}))
        slots = item.get("slots") if isinstance(item.get("slots"), dict) else {}
        if coerce_slots:
            slots = coerce_slot_dict(slots, BOX_SLOT_KINDS)
        for key, value in slots.items():
            if value not in (None, ""):
                base[key] = value
        write_calls.append({"tool": effect, "arguments": base})
        demand_types = set(graph.prerequisites_of(effect, include_mutating=False))
        if effect == "POST /folders":
            demand_types.add("folder_id")
        if effect in {"POST /comments", "POST /tasks"}:
            demand_types.add("file_id")
        if effect == "POST /files/content":
            demand_types.add("folder_id")
        suppliers: list[str] = []
        for id_type in sorted(demand_types):
            supplier = _choose_supplier(graph, id_type, effect)
            if supplier:
                reads_needed.setdefault(supplier, None)
                if supplier not in suppliers:
                    suppliers.append(supplier)
        if interleave_reads:
            for supplier in suppliers:
                interleaved.append(_read_call_for(supplier, base))
            interleaved.append({"tool": effect, "arguments": base})
    if interleave_reads:
        return interleaved
    plan: list[dict[str, Any]] = []
    for tool in reads_needed:
        plan.append({"tool": tool, "arguments": dict(READ_DEFAULTS.get(tool, {}))})
    plan.extend(write_calls)
    return plan

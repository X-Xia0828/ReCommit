"""Canonical binding."""

from __future__ import annotations
import copy
from typing import Any, Mapping, Sequence
from recommit.components.content_gate import apply_content_gate
from recommit.components.llm_plan_parse import bind_llm_plan, count_masked_slots


def run_typed_residual(
    *,
    service: str,
    expanded: Sequence[Mapping[str, Any]],
    failed_raw: str,
    failed_tools: Sequence[str],
    task: str,
    state_summary: str,
    obligations: Sequence[Mapping[str, Any]],
    public_contracts: Mapping[str, Any],
    generator: Any,
    sample_seed: int | None,
    execution_feedback: Sequence[Mapping[str, Any]] | None,
    bind_ids,
    bind_full,
    content_gate: bool = True,
    gate_mode: str = "evidence",
    content_keys: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], bool, str, dict[str, Any], int | None]:
    from recommit.components.llm_plan_filler_prompt import build_llm_fill_prompt

    plan = bind_ids(copy.deepcopy(list(expanded)))
    grounded_plan = copy.deepcopy(plan)
    skip_llm = False
    prompt_failed_raw = failed_raw
    if content_gate:
        (plan, evidence_stats, skip_llm, skip_prompt_raw) = apply_content_gate(
            plan, failed_raw, bind_full, gate_mode=gate_mode, content_keys=content_keys
        )
        if skip_prompt_raw:
            prompt_failed_raw = ""
    else:
        evidence_stats = {}
    if not skip_llm:
        # Failed arguments remain in the prompt as fallible suggestions. They
        # must not fill masks, become immutable values, or bypass generation.
        plan = grounded_plan
        evidence_stats["gate"] = "grounded_slots_only"
        evidence_stats["steps_with_arg_changes"] = 0
    evidence_stats = dict(evidence_stats)
    evidence_stats["mode"] = "typed_residual"
    evidence_stats["content_gate"] = content_gate
    evidence_stats["gate_mode"] = gate_mode if content_gate else "off"
    residual = count_masked_slots(plan)
    last_raw = ""
    parse_ok = True
    if skip_llm:
        return (plan, parse_ok, last_raw, evidence_stats, residual)
    if plan and not skip_llm:
        (system, prompt) = build_llm_fill_prompt(
            service=service,
            task=task,
            state_summary=state_summary,
            failed_tools=list(failed_tools or []),
            failed_raw=prompt_failed_raw,
            obligations=obligations,
            skeleton_plan=plan,
            public_contracts=public_contracts,
            execution_feedback=execution_feedback,
            binder="typed",
        )
        last_raw = generator.generate(system, prompt, seed=sample_seed)
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        (plan, parse_ok) = bind_llm_plan(last_raw, plan, binder="typed")
    if parse_ok:
        plan = bind_full([dict(call) for call in plan])
    return (plan, parse_ok, last_raw, evidence_stats, residual)

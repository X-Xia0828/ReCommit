"""Canonical argument binding and expressive support-constrained realization."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping

from .components.canonical_binding import run_typed_residual
from .services import component, prerequisite_graph, write_types
from .types import Episode, ServiceContext


def canonical(episode: Episode, context: ServiceContext, support: list[str], generator, seed: int):
    service = context.service
    obligations = [{"effect": effect, "slots": {}} for effect in support]
    graph = prerequisite_graph(context)
    expansion, binding = component(service + "_expansion"), component(service + "_binding")
    if service == "slack":
        expanded = expansion.expand_obligations(obligations, graph, task=episode.question)

        def bind(plan, ids_only):
            return binding.bind_plan(plan, episode.question, context.seed, ids_only=ids_only)
    else:
        options = {"interleave_reads": True} if service in {"box", "calendar"} else {}
        if service == "calendar":
            options["coerce_slots"] = True
        expanded = getattr(expansion, "expand_" + service + "_obligations")(
            obligations, graph, **options
        )

        def bind(plan, ids_only):
            options = {"consume_read_catalog": True} if service in {"box", "calendar"} else {}
            return getattr(binding, "bind_" + service + "_plan")(
                plan, task=episode.question, seed=context.seed, ids_only=ids_only, **options
            )

    calls, parsed, raw, details, _ = run_typed_residual(
        service=service,
        expanded=expanded,
        failed_raw=episode.raw_output,
        failed_tools=episode.proposal_tools,
        task=episode.question,
        state_summary=(
            component("slack_state").realization_summary(context.seed, episode.question)
            if service == "slack"
            else context.state_summary
        ),
        obligations=obligations,
        public_contracts=context.contracts,
        generator=generator,
        sample_seed=seed,
        execution_feedback=None,
        bind_ids=lambda p: bind(p, True),
        bind_full=lambda p: bind(p, False),
        content_gate=True,
        gate_mode="evidence",
    )
    return calls, parsed, raw, details


def project(calls, support: list[str], context: ServiceContext):
    """Keep public reads and allowed writes; reject unknown or malformed tools."""
    if not isinstance(calls, list):
        return [], False
    writes, permitted = write_types(context.service), set(support)
    kept = []
    for call in calls:
        if not isinstance(call, Mapping) or not call.get("tool"):
            return [], False
        tool = str(call["tool"])
        if tool in writes:
            if tool in permitted:
                kept.append(copy.deepcopy(dict(call)))
        elif tool in context.contracts:
            kept.append(copy.deepcopy(dict(call)))
        else:
            return [], False
    return kept, True


def expressive_prompt(episode: Episode, context: ServiceContext, support: list[str]):
    module = component(context.service + "_prompt")
    system, prompt = module.build_repair_prompt(
        task=episode.question,
        failed_raw=episode.raw_output,
        failed_tools=list(episode.proposal_tools),
        summary=context.state_summary,
        mode="schema",
        public_contracts=context.contracts,
    )
    prompt += (
        "\n\nA diffusion language model supplies the following permitted support of "
        f"mutating operation types:\n{json.dumps(sorted(set(support)), ensure_ascii=False)}\n\n"
        "Generate the complete recovery plan using any subset of these mutating types and "
        "no mutating type outside the support. A permitted type may be used zero, one, or "
        "multiple times; choose the actual multiplicity, targets, arguments, and order from "
        "the user request and visible public state. Public read-only operations are allowed. "
        "The system will deterministically remove mutating calls outside the supplied support. "
        "Return only the required JSON object."
    )
    return system, prompt


def expressive(episode: Episode, context: ServiceContext, support: list[str], generator, seed: int):
    system, prompt = expressive_prompt(episode, context, support)
    raw = generator.generate(system, prompt, seed=seed)
    calls, parsed = component(context.service + "_prompt").parse_calls(raw)
    calls, valid = project(calls, support, context)
    return calls, bool(parsed and valid), raw, {}

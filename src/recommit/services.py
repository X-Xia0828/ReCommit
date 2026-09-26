"""Public service adapters and resettable local simulators."""

from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path

from .types import ServiceContext
from .execution import executor_module

SEED_FILES = {
    "box": "box_default.json",
    "calendar": "calendar_default.json",
    "linear": "linear_expanded.json",
    "slack": "slack_bench_v2.json",
}
CALENDAR_EXTRA_WRITES = {"calendarList.watch", "events.watch", "acl.watch", "channels.stop"}


def component(name: str):
    return importlib.import_module("recommit.components." + name)


def vocabulary(service: str) -> list[str]:
    if service not in SEED_FILES:
        raise ValueError(f"Unsupported service: {service}")
    return sorted(component(service + "_graph").MUTATING_TOOLS)


def write_types(service: str) -> set[str]:
    """Filtering covers every write, including writes outside the WHAT vocabulary."""
    return set(vocabulary(service)) | (CALENDAR_EXTRA_WRITES if service == "calendar" else set())


def load_context(service: str, benchmark_root: Path) -> ServiceContext:
    vocabulary(service)
    benchmark_root = Path(benchmark_root).resolve()
    path = benchmark_root / "examples" / service / "seeds" / SEED_FILES[service]
    seed = json.loads(path.read_text(encoding="utf-8"))
    if service == "slack":
        summary = component("slack_state").state_summary(seed)
        contracts = component("slack_contracts").load_public_slack_contracts(benchmark_root)
    else:
        summary = getattr(component(service + "_prompt"), service + "_state_summary")(seed)
        contracts = getattr(
            component("service_contracts"), "load_public_" + service + "_contracts"
        )(benchmark_root)
    return ServiceContext(service, seed, summary, contracts, str(benchmark_root))


def prerequisite_graph(context: ServiceContext):
    """Build grounding dependencies from the explicitly supplied public interface."""
    if not context.benchmark_root:
        raise ValueError("Set benchmark_root or construct context with load_context()")
    service = context.service
    root = Path(context.benchmark_root)
    docs = (
        root
        / "examples"
        / service
        / "testsuites"
        / (service + "_docs")
        / (service + "_api_full_docs.json")
    )
    module = component(service + "_graph")
    if service == "slack":
        return module.build_graph(
            docs_path=docs, backend_path=root / "backend/src/services/slack/api/methods.py"
        )
    return getattr(module, "build_" + service + "_graph")(docs_path=docs)


def simulator(context: ServiceContext):
    """Return an executor that resets public state before every plan, including reruns.

    This local simulation adapter never reads task answers or benchmark assertions.
    It does not contact live service APIs.
    """
    module = executor_module(context.service)

    def execute(plan):
        state = copy.deepcopy(context.seed)
        before = (
            module.snapshot_tables(context.seed)
            if context.service in {"box", "calendar"}
            else copy.deepcopy(context.seed)
        )
        steps = [module.execute_call(state, copy.deepcopy(call)) for call in plan]
        after = module.snapshot_tables(state) if context.service in {"box", "calendar"} else state
        changes = module.diff_state(before, after)
        return {
            "execution": steps,
            "execution_all_ok": all(s.get("ok") for s in steps)
            and (bool(steps) or context.service == "slack"),
            "state_diff_counts": {key: len(value) for key, value in changes.items()},
        }

    return execute

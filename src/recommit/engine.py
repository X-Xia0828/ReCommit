"""Fixed-budget repair. All attempts use the original failure context."""

from __future__ import annotations

import copy
import time

from .how import canonical, expressive
from .types import Episode, ServiceContext


def schedule(budget: int = 13) -> list[tuple[str, int]]:
    if budget < 1:
        raise ValueError("budget must be positive")
    return [("canonical" if i % 2 == 0 else "expressive", i // 2) for i in range(budget)]


def public_observation(result):
    return {
        key: copy.deepcopy(result[key])
        for key in ("execution", "execution_all_ok", "state_diff_counts")
        if key in result
    }


def recover(
    episode: Episode,
    context: ServiceContext,
    supports: list[list[str]],
    generator,
    execute,
    *,
    budget: int = 13,
    seed: int = 1234,
    prune: bool = True,
    what_seconds: float = 0.0,
) -> dict:
    """Generate and execute every trial against an independently reset environment.

    ``execute(plan)`` must reset state for each call and return public observations.
    No evaluator success flag is used for generation, pruning, or early stopping.
    """
    order = schedule(budget)
    if len(supports) < (budget + 1) // 2:
        raise ValueError("Insufficient ranked supports for the requested budget")
    if what_seconds < 0:
        raise ValueError("what_seconds cannot be negative")
    trials = []
    start = time.perf_counter()
    for mode, rank in order:
        begin = time.perf_counter()
        calls, parsed, raw, _ = (canonical if mode == "canonical" else expressive)(
            episode, context, list(supports[rank]), generator, seed + rank
        )
        initial_calls = copy.deepcopy(calls)
        observation = (
            public_observation(execute(copy.deepcopy(calls)))
            if parsed
            else {"execution": [], "execution_all_ok": False, "state_diff_counts": {}}
        )
        initial_observation = copy.deepcopy(observation)
        removed = []
        if prune and calls and not observation.get("execution_all_ok"):
            removed = [
                i
                for i, step in enumerate(observation.get("execution", []))
                if i < len(calls) and not step.get("ok")
            ]
            if removed and len(removed) < len(calls):
                calls = [c for i, c in enumerate(calls) if i not in set(removed)]
                observation = public_observation(execute(copy.deepcopy(calls)))
            else:
                removed = []
        trials.append(
            {
                "position": len(trials) + 1,
                "realization": mode,
                "support_rank": rank + 1,
                "support": list(supports[rank]),
                "seed": seed + rank,
                "calls": calls,
                "parse_ok": parsed,
                "raw_output": raw,
                "public_observation": observation,
                "unpruned_calls": initial_calls,
                "unpruned_observation": initial_observation,
                "removed_positions": removed,
                "seconds": time.perf_counter() - begin,
                "prefix_seconds": what_seconds + time.perf_counter() - start,
            }
        )
    return {
        "failure_id": episode.failure_id,
        "test_id": episode.test_id,
        "what_seconds": what_seconds,
        "trials": trials,
    }

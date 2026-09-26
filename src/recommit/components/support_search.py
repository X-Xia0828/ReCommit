"""Support search."""

from __future__ import annotations
import heapq
import math
from dataclasses import dataclass
from typing import Mapping, Sequence
from recommit.components.legend import NONE


@dataclass(frozen=True)
class Candidate:
    """A distinct operation-type set and its inclusion-flip search cost."""

    obligations: tuple[str, ...]
    regret: float
    ranks: tuple[int, ...]
    assignment: tuple[str, ...]

    @property
    def cardinality(self) -> int:
        return len(self.obligations)

    def flips_from(self, other: "Candidate") -> dict[str, int]:
        cardinality = 0
        identity = 0
        for mine, theirs in zip(self.assignment, other.assignment):
            if mine == theirs:
                continue
            if mine == NONE or theirs == NONE:
                cardinality += 1
            else:
                identity += 1
        return {"cardinality_flips": cardinality, "identity_flips": identity}


def inclusion_scores(distributions: Sequence[Mapping[str, float]]) -> dict[str, float]:
    effects: set[str] = set()
    for distribution in distributions:
        effects.update((str(key) for key in distribution))
    effects.discard(NONE)
    scores: dict[str, float] = {}
    for effect in sorted(effects):
        absent = 1.0
        for distribution in distributions:
            absent *= 1.0 - min(1.0, max(0.0, float(distribution.get(effect, 0.0))))
        scores[effect] = 1.0 - absent
    return scores


def kbest_inclusion_sets(
    distributions: Sequence[Mapping[str, float]], limit: int, *, max_flips: int = 6
) -> list[Candidate]:
    """Top-``limit`` sets under the pooled inclusion model of :func:`inclusion_scores`.

    Each effect is an independent Bernoulli with probability ``q(e)``, so the
    most probable set includes every effect with ``q(e) > 1/2`` and the cost of
    disagreeing with that decision for effect ``e`` is ``|logit q(e)|``.  Ranking
    subsets by summed flip cost is again k-best over independent choices, so the
    same heap applies -- but now every candidate differs from its neighbours in
    *which effects are asserted*, one effect at a time, cheapest first.

    ``max_flips`` bounds how far from the top decision a candidate may sit; the
    heap would otherwise happily enumerate the whole power set.
    """
    if limit <= 0:
        return []
    scores = inclusion_scores(distributions)
    if not scores:
        return []
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    base = tuple((effect for (effect, score) in ordered if score > 0.5))
    costs: list[tuple[str, float]] = []
    for effect, score in ordered:
        clamped = min(1.0 - 1e-09, max(1e-09, float(score)))
        costs.append((effect, abs(math.log(clamped / (1.0 - clamped)))))
    costs.sort(key=lambda item: item[1])
    heap: list[tuple[float, tuple[int, ...]]] = [(0.0, ())]
    visited: set[tuple[int, ...]] = {()}
    candidates: list[Candidate] = []
    seen: set[tuple[str, ...]] = set()
    while heap and len(candidates) < limit:
        (cost, flipped) = heapq.heappop(heap)
        members = set(base)
        for index in flipped:
            effect = costs[index][0]
            if effect in members:
                members.discard(effect)
            else:
                members.add(effect)
        obligations = tuple(sorted(members))
        if obligations not in seen:
            seen.add(obligations)
            candidates.append(
                Candidate(
                    obligations=obligations,
                    regret=float(cost),
                    ranks=tuple(flipped),
                    assignment=obligations,
                )
            )
        if len(flipped) >= max_flips:
            continue
        start = flipped[-1] + 1 if flipped else 0
        for index in range(start, len(costs)):
            key = flipped + (index,)
            if key in visited:
                continue
            visited.add(key)
            heapq.heappush(heap, (cost + costs[index][1], key))
    return candidates

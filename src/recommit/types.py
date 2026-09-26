"""Public inputs to the repair method."""

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class Episode:
    """A request and the original failed attempt; no evaluator fields."""

    failure_id: str
    test_id: str
    question: str
    raw_output: str = ""
    proposal_tools: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "Episode":
        return cls(
            failure_id=str(row["failure_id"]),
            test_id=str(row["test_id"]),
            question=str(row["question"]),
            raw_output=str(row.get("raw_output") or ""),
            proposal_tools=tuple(map(str, row.get("proposal_tools") or [])),
        )


@dataclass(frozen=True)
class ServiceContext:
    """Public catalog, state summary, and tool contracts for one service."""

    service: str
    seed: dict[str, Any]
    state_summary: str
    contracts: dict[str, Any]
    benchmark_root: str = ""

    def __post_init__(self):
        if self.service not in {"box", "calendar", "linear", "slack"}:
            raise ValueError(f"Unsupported service: {self.service}")

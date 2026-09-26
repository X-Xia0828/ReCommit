"""What prompt."""

from __future__ import annotations
import json
from typing import Any, Mapping, Sequence
from recommit.components.legend import Legend

SYSTEM = "You recover from a failed tool-using agent attempt.\nThe attempt parsed and executed, but left the service in a state that does not satisfy the user's task.\nYou decide which mutating effects the recovery still needs, by filling a fixed-length block of obligation slots."


def format_evidence(evidence: Sequence[Mapping[str, Any]]) -> str:
    """Render accumulated execution evidence, executor-visible facts only."""
    if not evidence:
        return "  (nothing executed yet)"
    lines: list[str] = []
    for entry in evidence:
        round_index = entry.get("round")
        tool = entry.get("tool") or "?"
        args = entry.get("arguments")
        outcome = entry.get("outcome") or entry.get("status") or "?"
        head = f"  round {round_index}: {tool}"
        if isinstance(args, Mapping) and args:
            head += f"({json.dumps(args, ensure_ascii=False)[:200]})"
        lines.append(f"{head} -> {outcome}")
        returned = entry.get("returned")
        if returned:
            lines.append(f"      returned: {json.dumps(returned, ensure_ascii=False)[:200]}")
        diff = entry.get("state_diff")
        if diff:
            lines.append(
                f"      observed state change: {json.dumps(diff, ensure_ascii=False)[:200]}"
            )
    return "\n".join(lines)


def build_block_prompt(
    *,
    question: str,
    failed_tools: Sequence[str],
    failed_raw: str,
    legend: Legend,
    block_size: int,
    evidence: Sequence[Mapping[str, Any]] = (),
    state_summary: str | None = None,
) -> tuple[str, str]:
    """Return ``(system, prompt)``; the block is appended by the scorer."""
    parts = [
        f"User task:\n{(question or '').strip()}\n",
        f"Failed agent tool sequence:\n{json.dumps(list(failed_tools), ensure_ascii=False)}\n",
        f"Failed raw proposal excerpt:\n{(failed_raw or '')[:800]}\n",
    ]
    if state_summary:
        parts.append(f"Visible service state:\n{state_summary.strip()}\n")
    parts.append(f"Execution evidence so far:\n{format_evidence(evidence)}\n")
    parts.append(f"Effect legend (one code per mutating operation):\n{legend.render()}\n")
    codes = ", ".join(sorted(legend.code_to_effect))
    parts.append(
        f"Below, an obligation block of {block_size} slots follows the marker 'OBLIGATION BLOCK:'.\nEach slot is exactly ONE character, drawn from: {codes}, {legend.none_code}\n- Put an effect's code in a slot the recovery still needs.\n- Put '{legend.none_code}' in a slot that carries no obligation.\n- Never write an operation name; write only its one-character code.\n- Slots already filled were decided from the evidence above; re-judge them freely if the evidence no longer supports them.\n- Do not propose effects the evidence shows are already achieved.\n- Order does not matter; use as many slots as the task needs and no more.\nFormat: {block_size} single characters separated by spaces."
    )
    return (SYSTEM, "\n".join(parts))

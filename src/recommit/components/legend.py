"""Legend."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Iterable, Mapping

NONE = "."
CANDIDATE_CODEPOINTS: tuple[str, ...] = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


class LegendError(RuntimeError):
    """Raised when a service vocabulary cannot be legend-encoded."""


@dataclass(frozen=True)
class Legend:
    """Bijection between a service's effect vocabulary and single-token codes."""

    code_to_effect: Mapping[str, str]
    effect_to_code: Mapping[str, str]
    none_code: str = NONE
    glosses: Mapping[str, str] = field(default_factory=dict)

    @property
    def codes(self) -> tuple[str, ...]:
        """All slot-value codes, ``NONE`` last."""
        return tuple(sorted(self.code_to_effect)) + (self.none_code,)

    def render(self) -> str:
        """The legend as it appears in the prompt."""
        lines = []
        for code in sorted(self.code_to_effect):
            effect = self.code_to_effect[code]
            gloss = self.glosses.get(effect, "")
            lines.append(f"  {code} = {effect}" + (f"  -- {gloss}" if gloss else ""))
        lines.append(f"  {self.none_code} = (no obligation in this slot)")
        return "\n".join(lines)


def build_legend(
    effects: Iterable[str], *, is_single_token: "callable[[str], bool] | None" = None
) -> Legend:
    ordered = sorted({str(effect) for effect in effects})
    usable = [
        code for code in CANDIDATE_CODEPOINTS if is_single_token is None or is_single_token(code)
    ]
    if len(ordered) > len(usable):
        raise LegendError(
            f"{len(ordered)} effects but only {len(usable)} single-token codepoints available; extend CANDIDATE_CODEPOINTS with verified codes"
        )
    if is_single_token is not None and (not is_single_token(NONE)):
        raise LegendError(f"the NONE code {NONE!r} is not single-token for this tokenizer")
    code_to_effect = {code: effect for (code, effect) in zip(usable, ordered)}
    effect_to_code = {effect: code for (code, effect) in code_to_effect.items()}
    return Legend(code_to_effect=code_to_effect, effect_to_code=effect_to_code)

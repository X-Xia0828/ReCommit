"""Argument provenance."""

from __future__ import annotations
import contextvars
from dataclasses import dataclass, field
from typing import Any

_LEDGER: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "arg_provenance_ledger", default=None
)


@dataclass
class ProvenanceLedger:
    events: list[dict[str, Any]] = field(default_factory=list)

    def record(
        self, *, provenance: str, value: Any, tool: str = "", key: str = "", note: str = ""
    ) -> None:
        self.events.append(
            {
                "provenance": provenance,
                "tool": tool,
                "key": key,
                "note": note,
                "value_preview": _preview(value),
            }
        )

    @property
    def constant_events(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e["provenance"] == "constant"]

    def assert_no_constants(self) -> None:
        if self.constant_events:
            raise AssertionError(
                "forbidden constant provenance: "
                + "; ".join(
                    (
                        f"{e['tool']}.{e['key']}={e['value_preview']!r}"
                        for e in self.constant_events[:5]
                    )
                )
            )


def _preview(value: Any, limit: int = 80) -> str:
    text = value if isinstance(value, str) else repr(value)
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def current_ledger() -> ProvenanceLedger | None:
    return _LEDGER.get()


def record(provenance: str, value: Any, *, tool: str = "", key: str = "", note: str = "") -> Any:
    """Record provenance for ``value`` and return ``value`` unchanged."""
    ledger = current_ledger()
    if ledger is not None:
        ledger.record(provenance=provenance, value=value, tool=tool, key=key, note=note)
    return value


def bind_task_span(value: Any, *, tool: str = "", key: str = "", note: str = "") -> Any:
    return record("task_span", value, tool=tool, key=key, note=note)


def bind_seed_state(value: Any, *, tool: str = "", key: str = "", note: str = "") -> Any:
    return record("seed_state", value, tool=tool, key=key, note=note)


def bind_schema_default(value: Any, *, tool: str = "", key: str = "", note: str = "") -> Any:
    return record("schema_default", value, tool=tool, key=key, note=note)


class track_provenance:
    """``with track_provenance() as ledger:``"""

    def __enter__(self) -> ProvenanceLedger:
        self._ledger = ProvenanceLedger()
        self._token = _LEDGER.set(self._ledger)
        return self._ledger

    def __exit__(self, *exc: object) -> None:
        _LEDGER.reset(self._token)


EVIDENCE_PROVENANCE = frozenset({"task_span", "seed_state"})


def attach_ledger_meta(
    bound: list[dict[str, Any]], ledger: ProvenanceLedger
) -> list[dict[str, Any]]:
    """Attach compact provenance summary onto a bound plan (non-invasive)."""
    for call in bound:
        tool = str(call.get("tool") or "")
        call.setdefault("_meta", {})["provenance_events"] = [
            e for e in ledger.events if e.get("tool") == tool
        ]
    if bound:
        bound[0].setdefault("_meta", {})["bind_provenance"] = {
            "constant_count": len(ledger.constant_events),
            "events": list(ledger.events),
            "evidence_count": sum(
                (1 for e in ledger.events if e.get("provenance") in EVIDENCE_PROVENANCE)
            ),
            "schema_default_count": sum(
                (1 for e in ledger.events if e.get("provenance") == "schema_default")
            ),
        }
    return bound


def collect_plan_provenance_events(plan: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Read provenance events attached by binders (``_meta.bind_provenance``)."""
    if not plan:
        return []
    events: list[dict[str, Any]] = []
    first = plan[0] if isinstance(plan[0], dict) else {}
    meta = first.get("_meta") or {} if isinstance(first, dict) else {}
    bp = meta.get("bind_provenance") or {}
    events.extend(list(bp.get("events") or []))
    if events:
        return events
    for call in plan:
        if not isinstance(call, dict):
            continue
        events.extend(list((call.get("_meta") or {}).get("provenance_events") or []))
    return events

"""Typed binder core."""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

MASK = "[M]"


@dataclass(frozen=True)
class EntityCatalog:
    """One seed directory that can resolve an opaque identifier."""

    table: str
    name_fields: tuple[str, ...]
    id_field: str = "id"
    alt_id_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class BinderSchema:
    """Thin domain schema for the shared binder."""

    catalogs: Mapping[str, EntityCatalog]
    arg_to_catalog: Mapping[str, str]
    free_text_args: frozenset[str]
    id_patterns: tuple[str, ...] = ()
    enums: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    tool_arg_catalogs: Mapping[str, Mapping[str, str]] = field(default_factory=dict)


def quoted_texts(task: str) -> list[str]:
    """Extract quoted literals without treating English contractions as quotes.

    ``we're / she's / there's`` must not open a quote span that swallows the
    rest of the sentence until the next apostrophe.
    """
    out: list[str] = []
    seen: set[str] = set()
    pattern = "\"([^\"]+)\"|(?<![A-Za-z])\\'([^\\']+)\\'(?![A-Za-z])"
    for match in re.finditer(pattern, task):
        text = (match.group(1) or match.group(2) or "").strip()
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def extract_structured_ids(task: str, patterns: Sequence[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, task):
            value = match.group(0)
            if value not in seen:
                out.append(value)
                seen.add(value)
    return out


def seed_name_hits(task: str, seed: Mapping[str, Any], catalog: EntityCatalog) -> list[str]:
    """Return display values present in the task that resolve via the catalog."""
    rows = seed.get(catalog.table) or []
    if not isinstance(rows, list):
        return []
    lower = task.lower()
    hits: list[str] = []
    seen: set[str] = set()
    candidates: list[tuple[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for field_name in catalog.name_fields + catalog.alt_id_fields:
            name = str(row.get(field_name) or "").strip()
            if name:
                candidates.append((name, str(row.get(catalog.id_field) or name)))
    candidates.sort(key=lambda item: len(item[0]), reverse=True)
    identities: dict[str, set[str]] = {}
    for name, key in candidates:
        identities.setdefault(name.casefold(), set()).add(key)
    for name, key in candidates:
        token = name.lower()
        matching_ids = identities[name.casefold()]
        if (
            token
            and len(matching_ids) == 1
            and re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", lower)
            and (key not in seen)
        ):
            hits.append(name)
            seen.add(key)
    return hits


def token_overlap_hits(
    task: str,
    seed: Mapping[str, Any],
    catalog: EntityCatalog,
    *,
    min_overlap: int = 2,
    min_token_len: int = 4,
) -> list[str]:
    """Fallback directory match when the full name is not literally in the task."""
    rows = seed.get(catalog.table) or []
    if not isinstance(rows, list):
        return []
    lower = task.lower()
    scored: list[tuple[int, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get(catalog.name_fields[0]) if catalog.name_fields else "") or ""
        alt = ""
        if catalog.alt_id_fields:
            alt = str(row.get(catalog.alt_id_fields[0]) or "")
        tokens = [
            tok for tok in re.findall("[a-z0-9]+", title.lower()) if len(tok) >= min_token_len
        ]
        overlap = sum((1 for tok in tokens if tok in lower))
        if overlap >= min_overlap:
            scored.append((overlap, alt or title))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [value for (_, value) in scored]


@dataclass
class BindingState:
    quotes: list[str]
    quote_idx: int = 0
    structured_ids: list[str] = field(default_factory=list)
    structured_idx: int = 0
    catalog_hits: dict[str, list[str]] = field(default_factory=dict)
    catalog_idx: dict[str, int] = field(default_factory=dict)
    read_catalog_idx: dict[str, int] = field(default_factory=dict)

    def take_quote(self) -> str | None:
        if self.quote_idx < len(self.quotes):
            value = self.quotes[self.quote_idx]
            self.quote_idx += 1
            return value
        return None

    def peek_quote(self) -> str | None:
        if self.quote_idx < len(self.quotes):
            return self.quotes[self.quote_idx]
        return None

    def take_id(self) -> str | None:
        if self.structured_idx < len(self.structured_ids):
            value = self.structured_ids[self.structured_idx]
            self.structured_idx += 1
            return value
        return None

    def take_catalog(self, key: str) -> str | None:
        hits = self.catalog_hits.get(key) or []
        idx = self.catalog_idx.get(key, 0)
        if idx < len(hits):
            self.catalog_idx[key] = idx + 1
            return hits[idx]
        return hits[0] if hits else None

    def peek_catalog(self, key: str) -> str | None:
        hits = self.catalog_hits.get(key) or []
        idx = self.catalog_idx.get(key, 0)
        if idx < len(hits):
            return hits[idx]
        return hits[0] if hits else None

    def take_read_catalog(self, key: str) -> str | None:
        """Next candidate for a lookup, on a cursor independent of the writes."""
        hits = self.catalog_hits.get(key) or []
        idx = self.read_catalog_idx.get(key, 0)
        if idx < len(hits):
            self.read_catalog_idx[key] = idx + 1
            return hits[idx]
        return hits[-1] if hits else None


def build_binding_state(task: str, seed: Mapping[str, Any], schema: BinderSchema) -> BindingState:
    catalog_hits: dict[str, list[str]] = {}
    for key, catalog in schema.catalogs.items():
        hits = seed_name_hits(task, seed, catalog)
        # Partial word overlap is a suggestion for HOW, not an immutable ID.
        catalog_hits[key] = hits
    return BindingState(
        quotes=quoted_texts(task),
        structured_ids=extract_structured_ids(task, schema.id_patterns),
        catalog_hits=catalog_hits,
    )


def is_masked(value: Any) -> bool:
    return value in (None, "", MASK) or value == [MASK]


def arg_needs_bind(args: Mapping[str, Any], key: str) -> bool:
    """True only when the argument is present and still masked.

    Important: ``is_masked(None)`` is True, so adapters must not call
    ``is_masked(args.get(key))`` for optional keys that are absent — that
    falsely invents fields and burns the quote/id queues.
    """
    return key in args and args[key] is not None and is_masked(args[key])


def bind_arguments(
    tool: str,
    args: Mapping[str, Any],
    *,
    state: BindingState,
    schema: BinderSchema,
    ids_only: bool = False,
) -> dict[str, Any]:
    """Fill masked arguments using catalog / structured IDs / quote queue.

    ``ids_only=True`` skips ``free_text_args`` so content slots stay ``[M]`` for
    the HOW model to fill.

    When a provenance ledger is active, catalog hits are ``seed_state`` and
    quotes are ``task_span``.
    """
    from recommit.components.argument_provenance import bind_seed_state, bind_task_span

    out = dict(args)
    for key, value in list(out.items()):
        if value is None or value == "" or not is_masked(value):
            continue
        catalog_key = schema.tool_arg_catalogs.get(tool, {}).get(
            key, schema.arg_to_catalog.get(key)
        )
        if catalog_key:
            if catalog_key in {"issues", "comments"} and state.structured_ids:
                hit = state.take_id() or state.peek_catalog(catalog_key)
            else:
                hit = state.take_catalog(catalog_key)
            if hit:
                out[key] = bind_seed_state(hit, tool=tool, key=key, note=f"catalog:{catalog_key}")
                continue
        if key in schema.enums:
            continue
        if ids_only:
            continue
        if key in schema.free_text_args:
            quote = state.take_quote()
            if quote:
                out[key] = bind_task_span(quote, tool=tool, key=key, note="quoted_span")
    return out


def strip_masks(args: Mapping[str, Any], *, keep: Iterable[str] = ()) -> dict[str, Any]:
    keep_set = set(keep)
    return {
        key: value
        for (key, value) in args.items()
        if value is None or value == "" or not is_masked(value) or key in keep_set
    }

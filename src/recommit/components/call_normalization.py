"""Call normalization."""

from __future__ import annotations
from typing import Any, Mapping


def _merge_dict(dst: dict[str, Any], src: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in src.items():
        if key not in dst or dst[key] in (None, "", {}, []):
            dst[key] = value
    return dst


def normalize_tool_arguments(arguments: Any) -> dict[str, Any]:
    """Flatten nested public-API argument envelopes into executor-facing keys."""
    if not isinstance(arguments, dict):
        return {}
    out = dict(arguments)
    nested_input = out.pop("input", None)
    if isinstance(nested_input, dict):
        out = _merge_dict(dict(nested_input), out)
    parameters = out.pop("parameters", None)
    if isinstance(parameters, dict):
        for section in ("path", "query", "body"):
            section_value = parameters.get(section)
            if isinstance(section_value, dict):
                out = _merge_dict(dict(section_value), out)
        for key, value in parameters.items():
            if key in {"path", "query", "body"}:
                continue
            if key not in out:
                out[key] = value
    body = out.get("body")
    if isinstance(body, dict):
        out.pop("body")
        out = _merge_dict(dict(body), out)
    attributes = out.pop("attributes", None)
    if isinstance(attributes, dict):
        out = _merge_dict(dict(attributes), out)
    elif isinstance(attributes, str) and "name" not in out:
        out["attributes"] = attributes
    if "id" not in out:
        for alias in ("folder_id", "file_id", "comment_id", "hub_id", "task_id", "collection_id"):
            if alias in out:
                out["id"] = out[alias]
                break
    return out


_SCHEMA_META_KEYS = frozenset({"type", "required", "description", "example"})


def _is_schema_fields_wrapper(section_value: Mapping[str, Any]) -> bool:
    """True only for ``{type, ..., fields:{...}}`` wrappers — not a param named fields."""
    nested = section_value.get("fields")
    if not isinstance(nested, dict) or not nested:
        return False
    return all((key in _SCHEMA_META_KEYS or key == "fields" for key in section_value))


def _flatten_argument_fields(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Build a flat argument-field map from a public contract blob."""
    fields: dict[str, Any] = {}
    arguments = contract.get("arguments")
    if isinstance(arguments, dict):
        if "input" in arguments and isinstance(arguments.get("input"), dict):
            input_spec = arguments["input"]
            nested_fields = input_spec.get("fields")
            if isinstance(nested_fields, dict):
                fields.update(nested_fields)
            else:
                non_meta = {
                    key: value
                    for (key, value) in input_spec.items()
                    if key not in _SCHEMA_META_KEYS
                }
                if non_meta and all(
                    (isinstance(value, dict) and "type" in value for value in non_meta.values())
                ):
                    fields.update(non_meta)
            for key, value in arguments.items():
                if key == "input":
                    continue
                fields[key] = value
        else:
            fields.update(arguments)
    parameters = contract.get("parameters")
    if isinstance(parameters, dict):
        for section in ("path", "query", "body"):
            section_value = parameters.get(section)
            if isinstance(section_value, dict):
                if _is_schema_fields_wrapper(section_value):
                    fields.update(section_value["fields"])
                else:
                    fields.update(section_value)
            elif section_value is not None and section not in fields:
                fields[section] = section_value
    return fields


def executor_facing_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Rewrite a public contract so prompts show flat executor-facing arguments."""
    cleaned = dict(contract)
    arguments = contract.get("arguments")
    had_nesting = isinstance(arguments, dict) and "input" in arguments or "parameters" in contract
    fields = _flatten_argument_fields(contract)
    if fields or had_nesting:
        cleaned["arguments"] = fields
        cleaned.pop("parameters", None)
    return cleaned


def normalize_contracts_for_executor(contracts: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(name): executor_facing_contract(contract)
        for (name, contract) in contracts.items()
        if isinstance(contract, Mapping)
    }

"""Slack graph."""

from __future__ import annotations
import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


class ContractError(RuntimeError):
    """Raised when the declared tables and the parsed API disagree."""


CHANNEL_ID = "channel_id"
USER_ID = "user_id"
MESSAGE_TS = "message_ts"
IDENTIFIER_PARAMS: Mapping[str, str] = {
    "channel": CHANNEL_ID,
    "user": USER_ID,
    "users": USER_ID,
    "ts": MESSAGE_TS,
    "timestamp": MESSAGE_TS,
    "thread_ts": MESSAGE_TS,
}
SUPPLY_MARKERS: Mapping[str, str] = {
    "_serialize_conversation": CHANNEL_ID,
    "_format_channel_id": CHANNEL_ID,
    "_serialize_user": USER_ID,
    "_format_user_id": USER_ID,
}
MESSAGE_CONTAINER_KEYS: frozenset[str] = frozenset({"messages", "message"})
MESSAGE_AUTHOR_KEY = "user"
MUTATING_TOOLS: frozenset[str] = frozenset(
    {
        "chat.postMessage",
        "chat.update",
        "chat.delete",
        "reactions.add",
        "reactions.remove",
        "conversations.create",
        "conversations.invite",
        "conversations.kick",
        "conversations.join",
        "conversations.leave",
        "conversations.rename",
        "conversations.setTopic",
        "conversations.archive",
        "conversations.unarchive",
    }
)


@dataclass(frozen=True)
class ToolContract:
    """One operation's public surface."""

    name: str
    required_params: frozenset[str]
    optional_params: frozenset[str]
    supplies: frozenset[str]

    @property
    def is_mutating(self) -> bool:
        return self.name in MUTATING_TOOLS

    def required_identifier_types(self) -> frozenset[str]:
        """Identifier types this operation cannot run without."""
        return frozenset(
            (
                IDENTIFIER_PARAMS[param]
                for param in self.required_params
                if param in IDENTIFIER_PARAMS
            )
        )

    def consumable_identifier_types(self) -> frozenset[str]:
        """Identifier types this operation can accept, required or not.

        Documented optionality does not mean the argument is dispensable in
        practice. `conversations.open` lists `users` as optional because a caller
        may instead pass an already-known conversation, yet opening a direct
        message is exactly the case where the user identifier is the argument.
        Demand is therefore computed from required parameters only, while the
        question of whether a read is useful is computed from all of them.
        """
        return frozenset(
            (
                IDENTIFIER_PARAMS[param]
                for param in self.required_params | self.optional_params
                if param in IDENTIFIER_PARAMS
            )
        )


@dataclass(frozen=True)
class PrerequisiteGraph:
    service: str
    tools: Mapping[str, ToolContract]

    def suppliers_of(
        self, identifier_type: str, *, include_mutating: bool = False
    ) -> frozenset[str]:
        """Operations whose response carries this identifier type.

        Reads only by default.  `include_mutating` also admits state-changing
        operations, which is required because some identifiers legitimately come
        from an earlier write: creating a conversation returns the identifier the
        next write needs, and no read could have returned it beforehand.
        """
        return frozenset(
            (
                name
                for (name, contract) in self.tools.items()
                if identifier_type in contract.supplies
                and (include_mutating or not contract.is_mutating)
            )
        )

    def prerequisites_of(
        self, tool_name: str, *, include_mutating: bool = False
    ) -> Mapping[str, frozenset[str]]:
        """For each identifier the operation needs, the operations that supply it.

        An empty candidate set means the API offers nothing that returns the
        identifier. The operation itself is excluded so a tool is never its
        own prerequisite.
        """
        contract = self.tools.get(tool_name)
        if contract is None:
            raise ContractError(f"unknown tool: {tool_name}")
        return {
            identifier_type: self.suppliers_of(identifier_type, include_mutating=include_mutating)
            - {tool_name}
            for identifier_type in sorted(contract.required_identifier_types())
        }

    def mutating_tools(self) -> frozenset[str]:
        return frozenset((name for (name, c) in self.tools.items() if c.is_mutating))

    def read_tools(self) -> frozenset[str]:
        return frozenset((name for (name, c) in self.tools.items() if not c.is_mutating))


def parse_docs(docs_path: Path) -> dict[str, tuple[frozenset[str], frozenset[str]]]:
    """Read required and optional parameter names per operation from the docs."""
    payload = json.loads(docs_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not payload:
        raise ContractError(f"no tool entries in {docs_path}")
    out: dict[str, tuple[frozenset[str], frozenset[str]]] = {}
    for tool_name, entry in payload.items():
        required: set[str] = set()
        optional: set[str] = set()
        for params in (entry.get("parameters") or {}).values():
            if not isinstance(params, dict):
                continue
            for param_name, spec in params.items():
                if param_name == "token":
                    continue
                if isinstance(spec, dict) and spec.get("required"):
                    required.add(param_name)
                else:
                    optional.add(param_name)
        out[str(tool_name)] = (frozenset(required), frozenset(optional - required))
    return out


def _function_definitions(tree: ast.Module) -> dict[str, ast.AST]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _handler_map(tree: ast.Module, table_name: str) -> dict[str, str]:
    """Read the operation-name -> handler-function mapping from the backend."""
    for node in ast.walk(tree):
        targets: Iterable[ast.AST]
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if not (isinstance(target, ast.Name) and target.id == table_name):
                continue
            if not isinstance(node.value, ast.Dict):
                raise ContractError(f"{table_name} is not a dict literal")
            mapping = {}
            for key, value in zip(node.value.keys, node.value.values):
                if isinstance(key, ast.Constant) and isinstance(value, ast.Name):
                    mapping[str(key.value)] = value.id
            if not mapping:
                raise ContractError(f"{table_name} yielded no entries")
            return mapping
    raise ContractError(f"{table_name} not found")


def _called_names(node: ast.AST) -> set[str]:
    return {
        child.func.id
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }


def _literal_dict_keys(node: ast.AST) -> set[str]:
    keys: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Dict):
            for key in child.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
    return keys


def _supplied_identifiers(handler: ast.AST, functions: Mapping[str, ast.AST]) -> frozenset[str]:
    """Which identifier types an operation's response carries.

    Resolved one level deep: a handler that calls a serializing helper inherits
    that helper's identifier type, and a handler whose own response literal
    carries a message timestamp key supplies message timestamps.
    """
    called = _called_names(handler)
    supplies = {
        identifier_type for (marker, identifier_type) in SUPPLY_MARKERS.items() if marker in called
    }
    keys = _literal_dict_keys(handler)
    if keys & MESSAGE_CONTAINER_KEYS:
        supplies.add(MESSAGE_TS)
        if MESSAGE_AUTHOR_KEY in keys:
            supplies.add(USER_ID)
    return frozenset(supplies)


def build_graph(
    *,
    docs_path: Path,
    backend_path: Path,
    service: str = "slack",
    handler_table: str = "SLACK_HANDLERS",
) -> PrerequisiteGraph:
    """Derive the prerequisite graph from the docs and the backend implementation."""
    params = parse_docs(docs_path)
    tree = ast.parse(backend_path.read_text(encoding="utf-8"))
    functions = _function_definitions(tree)
    handlers = _handler_map(tree, handler_table)
    missing_handlers = sorted(set(params) - set(handlers))
    if missing_handlers:
        raise ContractError(f"documented operations with no backend handler: {missing_handlers}")
    unknown_mutating = sorted(MUTATING_TOOLS - set(params))
    if unknown_mutating:
        raise ContractError(
            f"MUTATING_TOOLS names operations absent from the docs: {unknown_mutating}"
        )
    tools: dict[str, ToolContract] = {}
    for tool_name, (required, optional) in params.items():
        handler = functions.get(handlers[tool_name])
        if handler is None:
            raise ContractError(f"handler body missing for {tool_name}")
        tools[tool_name] = ToolContract(
            name=tool_name,
            required_params=required,
            optional_params=optional,
            supplies=_supplied_identifiers(handler, functions),
        )
    return PrerequisiteGraph(service=service, tools=tools)

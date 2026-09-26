"""Box graph."""

from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Mapping
from recommit.components.slack_graph import ContractError, PrerequisiteGraph, ToolContract

FILE_ID = "file_id"
FOLDER_ID = "folder_id"
COMMENT_ID = "comment_id"
HUB_ID = "hub_id"
TASK_ID = "task_id"
USER_ID = "user_id"
COLLECTION_ID = "collection_id"
IDENTIFIER_PARAMS: Mapping[str, str] = {
    "file_id": FILE_ID,
    "folder_id": FOLDER_ID,
    "comment_id": COMMENT_ID,
    "hub_id": HUB_ID,
    "task_id": TASK_ID,
    "user_id": USER_ID,
    "collection_id": COLLECTION_ID,
    "id": FILE_ID,
}
TOOL_ID_PARAM_TYPE: Mapping[str, str] = {
    "GET /files/{id}": FILE_ID,
    "PUT /files/{id}": FILE_ID,
    "DELETE /files/{id}": FILE_ID,
    "GET /files/{id}/content": FILE_ID,
    "POST /files/{id}/content": FILE_ID,
    "GET /files/{id}/comments": FILE_ID,
    "GET /files/{id}/tasks": FILE_ID,
    "GET /folders/{id}": FOLDER_ID,
    "PUT /folders/{id}": FOLDER_ID,
    "DELETE /folders/{id}": FOLDER_ID,
    "GET /folders/{id}/items": FOLDER_ID,
    "PUT /comments/{id}": COMMENT_ID,
    "DELETE /comments/{id}": COMMENT_ID,
    "PUT /tasks/{id}": TASK_ID,
    "DELETE /tasks/{id}": TASK_ID,
    "GET /hubs/{id}": HUB_ID,
    "PUT /hubs/{id}": HUB_ID,
    "DELETE /hubs/{id}": HUB_ID,
    "POST /hubs/{id}/manage_items": HUB_ID,
    "GET /collections/{id}": COLLECTION_ID,
    "PUT /collections/{id}": COLLECTION_ID,
    "DELETE /collections/{id}": COLLECTION_ID,
}
DOC_TO_TOOL: Mapping[str, str] = {
    "GET /folders/{folder_id}": "GET /folders/{id}",
    "PUT /folders/{folder_id}": "PUT /folders/{id}",
    "DELETE /folders/{folder_id}": "DELETE /folders/{id}",
    "GET /folders/{folder_id}/items": "GET /folders/{id}/items",
    "GET /files/{file_id}": "GET /files/{id}",
    "PUT /files/{file_id}": "PUT /files/{id}",
    "DELETE /files/{file_id}": "DELETE /files/{id}",
    "GET /files/{file_id}/content": "GET /files/{id}/content",
    "POST /files/{file_id}/content": "POST /files/{id}/content",
    "GET /files/{file_id}/comments": "GET /files/{id}/comments",
    "GET /files/{file_id}/tasks": "GET /files/{id}/tasks",
    "PUT /comments/{comment_id}": "PUT /comments/{id}",
    "DELETE /comments/{comment_id}": "DELETE /comments/{id}",
    "PUT /tasks/{task_id}": "PUT /tasks/{id}",
    "DELETE /tasks/{task_id}": "DELETE /tasks/{id}",
    "GET /hubs/{hub_id}": "GET /hubs/{id}",
    "PUT /hubs/{hub_id}": "PUT /hubs/{id}",
    "DELETE /hubs/{hub_id}": "DELETE /hubs/{id}",
    "POST /hubs/{hub_id}/manage_items": "POST /hubs/{id}/manage_items",
    "GET /collections/{collection_id}": "GET /collections/{id}",
    "PUT /collections/{collection_id}": "PUT /collections/{id}",
    "DELETE /collections/{collection_id}": "DELETE /collections/{id}",
}
MUTATING_TOOLS: frozenset[str] = frozenset(
    {
        "POST /folders",
        "PUT /folders/{id}",
        "DELETE /folders/{id}",
        "POST /files/content",
        "PUT /files/{id}",
        "DELETE /files/{id}",
        "POST /files/{id}/content",
        "POST /comments",
        "PUT /comments/{id}",
        "DELETE /comments/{id}",
        "POST /tasks",
        "PUT /tasks/{id}",
        "DELETE /tasks/{id}",
        "POST /hubs",
        "PUT /hubs/{id}",
        "DELETE /hubs/{id}",
        "POST /hubs/{id}/manage_items",
        "POST /collections",
        "PUT /collections/{id}",
        "DELETE /collections/{id}",
    }
)
DECLARED_SUPPLIES: Mapping[str, frozenset[str]] = {
    "GET /search": frozenset({FILE_ID, FOLDER_ID, HUB_ID, COLLECTION_ID}),
    "GET /users/me": frozenset({USER_ID}),
    "GET /folders/{id}": frozenset({FOLDER_ID}),
    "GET /folders/{id}/items": frozenset({FILE_ID, FOLDER_ID}),
    "POST /folders": frozenset({FOLDER_ID}),
    "GET /files/{id}": frozenset({FILE_ID}),
    "POST /files/content": frozenset({FILE_ID}),
    "GET /files/{id}/comments": frozenset({COMMENT_ID}),
    "POST /comments": frozenset({COMMENT_ID}),
    "GET /files/{id}/tasks": frozenset({TASK_ID}),
    "POST /tasks": frozenset({TASK_ID}),
    "GET /hubs": frozenset({HUB_ID}),
    "GET /hubs/{id}": frozenset({HUB_ID}),
    "POST /hubs": frozenset({HUB_ID}),
    "GET /collections": frozenset({COLLECTION_ID}),
    "GET /collections/{id}": frozenset({COLLECTION_ID}),
    "POST /collections": frozenset({COLLECTION_ID}),
}


class BoxToolContract(ToolContract):
    def required_identifier_types(self) -> frozenset[str]:
        types: set[str] = set()
        for param in self.required_params:
            if param == "id" and self.name in TOOL_ID_PARAM_TYPE:
                types.add(TOOL_ID_PARAM_TYPE[self.name])
            elif param in IDENTIFIER_PARAMS and param != "id":
                types.add(IDENTIFIER_PARAMS[param])
        if self.name in TOOL_ID_PARAM_TYPE:
            types.add(TOOL_ID_PARAM_TYPE[self.name])
        return frozenset(types)

    def consumable_identifier_types(self) -> frozenset[str]:
        return self.required_identifier_types() | frozenset(
            (
                IDENTIFIER_PARAMS[param]
                for param in self.required_params | self.optional_params
                if param in IDENTIFIER_PARAMS and param != "id"
            )
        )

    @property
    def is_mutating(self) -> bool:
        return self.name in MUTATING_TOOLS


def _normalize_tool_name(name: str) -> str:
    return DOC_TO_TOOL.get(name, name)


def parse_box_docs(docs_path: Path) -> dict[str, tuple[frozenset[str], frozenset[str]]]:
    payload = json.loads(docs_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not payload:
        raise ContractError(f"no tool entries in {docs_path}")
    out: dict[str, tuple[frozenset[str], frozenset[str]]] = {}
    for rest_key, entry in payload.items():
        tool_name = _normalize_tool_name(str(rest_key))
        tool_name = re.sub("\\{(folder|file|comment|hub|task|collection)_id\\}", "{id}", tool_name)
        required: set[str] = set()
        optional: set[str] = set()
        if isinstance(entry, dict):
            parameters = entry.get("parameters") or {}
            if isinstance(parameters, dict):
                for _group, params in parameters.items():
                    if not isinstance(params, dict):
                        continue
                    for param_name, spec in params.items():
                        leaf = param_name.split(".")[0].replace("[]", "")
                        if isinstance(spec, dict) and spec.get("required"):
                            required.add(leaf)
                        else:
                            optional.add(leaf)
        for match in re.finditer("\\{([A-Za-z0-9_]+)\\}", tool_name):
            required.add(match.group(1))
        out[tool_name] = (frozenset(required), frozenset(optional - required))
    return out


def build_box_graph(*, docs_path: Path, service: str = "box") -> PrerequisiteGraph:
    params = parse_box_docs(docs_path)
    for name in MUTATING_TOOLS:
        params.setdefault(name, (frozenset({"id"} if "{id}" in name else frozenset()), frozenset()))
    tools: dict[str, ToolContract] = {}
    for tool_name, (required, optional) in params.items():
        tools[tool_name] = BoxToolContract(
            name=tool_name,
            required_params=required,
            optional_params=optional,
            supplies=DECLARED_SUPPLIES.get(tool_name, frozenset()),
        )
    return PrerequisiteGraph(service=service, tools=tools)

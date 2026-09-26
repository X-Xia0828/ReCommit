"""Slack contracts."""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any

DEFAULT_AGENTDIFF_ROOT = Path("data/agent-diff")
PUBLIC_DOCS_RELATIVE_PATH = Path("examples/slack/testsuites/slack_docs/slack_api_full_docs.json")
NON_ACTION_METHODS = frozenset({"auth.test", "reactions.get"})


def _drop_authentication_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _drop_authentication_fields(item)
            for (key, item) in value.items()
            if key not in {"token", "example_request"}
        }
    if isinstance(value, list):
        return [_drop_authentication_fields(item) for item in value]
    return value


def load_public_slack_contracts(
    agentdiff_root: Path = DEFAULT_AGENTDIFF_ROOT,
) -> dict[str, dict[str, Any]]:
    """Load the public benchmark action contracts without credentials/examples."""
    from recommit.components.call_normalization import normalize_contracts_for_executor

    docs_path = agentdiff_root / PUBLIC_DOCS_RELATIVE_PATH
    docs = json.loads(docs_path.read_text(encoding="utf-8"))
    if not isinstance(docs, dict):
        raise ValueError(f"expected a method mapping in {docs_path}")
    contracts = {
        name: _drop_authentication_fields(contract)
        for (name, contract) in sorted(docs.items())
        if name not in NON_ACTION_METHODS
    }
    if len(contracts) != 25:
        raise ValueError(f"expected 25 public Slack action methods, found {len(contracts)}")
    return normalize_contracts_for_executor(contracts)

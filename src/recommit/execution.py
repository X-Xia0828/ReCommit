"""Shared, method-independent entry point for local public-state execution."""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path

EXECUTOR_VERSION = "state-contract-v1"
SERVICES = ("box", "calendar", "linear", "slack")


def executor_module(service: str):
    if service not in SERVICES:
        raise ValueError(f"Unsupported service: {service}")
    return importlib.import_module(f"recommit.components.{service}_simulator")


def step_executor(service: str):
    """Return the same state-mutating function to any method or evaluator.

    No strategy, request, task identifier, or hidden success label is accepted.
    Plan reset and method-specific pruning are responsibilities of the caller.
    """
    return executor_module(service).execute_call


def executor_fingerprint() -> dict[str, str]:
    """Identify implementation bytes, including shared argument normalization."""
    directory = Path(__file__).parent
    files = [Path(__file__), directory / "components/call_normalization.py"]
    files += [Path(executor_module(service).__file__) for service in SERVICES]
    return {
        str(path.relative_to(directory)).replace("\\", "/"): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in files
    }

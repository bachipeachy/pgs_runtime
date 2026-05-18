"""
execution_context.py — Mutable execution state container.

Governed by: CONSTITUTION_EXECUTION_V0

Holds payload, tracks termination, accumulates results.
No interpretation, no routing, no capability execution.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any
from enum import Enum

from omnibachi.implementation.execution.machine.execution_policy_loader import ExecutionPolicy
from omnibachi.implementation.execution.machine.trace_emitter import TraceEmitter


class ExitCondition(Enum):
    """Exit conditions per §8."""
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    ABORT = "ABORT"
    TIMEOUT = "TIMEOUT"


class NodeStatus(Enum):
    """Node execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class NodeState:
    """State of a single node."""
    node_id: str
    status: NodeStatus = NodeStatus.PENDING
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    duration_ms: int = 0


class ExecutionContext:
    """Mutable execution state container."""

    def __init__(
        self,
        *,
        workflow_code: str,
        initial_payload: dict[str, Any],
        trace_emitter: TraceEmitter,
        policy: ExecutionPolicy,
    ):
        self.workflow_code = workflow_code
        self.payload: dict[str, Any] = dict(initial_payload)
        self._trace = trace_emitter
        self._policy = policy

        # Exit state
        self._exited = False
        self._exit_condition: ExitCondition | None = None
        self._exit_reason: str | None = None
        self._exit_reason_code: str | None = None

        # Node states
        self._node_states: dict[str, NodeState] = {}

    @property
    def execution_id(self) -> str:
        return self._trace.execution_id

    @property
    def exited(self) -> bool:
        return self._exited

    @property
    def exit_condition(self) -> ExitCondition | None:
        return self._exit_condition

    @property
    def exit_reason(self) -> str | None:
        return self._exit_reason

    @property
    def exit_reason_code(self) -> str | None:
        return self._exit_reason_code

    @property
    def trace(self) -> TraceEmitter:
        return self._trace

    @property
    def policy(self) -> ExecutionPolicy:
        return self._policy

    def get_node_state(self, node_id: str) -> NodeState:
        """Get or create node state."""
        if node_id not in self._node_states:
            self._node_states[node_id] = NodeState(node_id=node_id)
        return self._node_states[node_id]

    def update_payload(self, updates: dict[str, Any]) -> None:
        """Update payload with results."""
        self.payload.update(updates)

    def store_result(self, key: str, value: Any) -> None:
        """Store a single result in payload."""
        self.payload[key] = value

    def get_value(self, path: str) -> Any:
        """
        Get value from payload using JSONPath-like syntax.

        Supports: $.payload.key or just key
        """
        if path.startswith("$.payload."):
            path = path[10:]
        elif path.startswith("$."):
            path = path[2:]

        parts = path.split(".")
        value = self.payload
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None
        return value

    def mark_success(self) -> None:
        """Mark execution as successful completion."""
        self._exited = True
        self._exit_condition = ExitCondition.SUCCESS
        self._exit_reason = "All terminal nodes completed"
        self._exit_reason_code = "COMPLETED"

    def mark_failure(self, reason: str, *, exit_reason_code: str) -> None:
        """Mark execution as failed."""
        self._exited = True
        self._exit_condition = ExitCondition.FAILURE
        self._exit_reason = reason
        self._exit_reason_code = exit_reason_code

    def mark_abort(self, reason: str) -> None:
        """Mark execution as aborted (policy violation)."""
        self._exited = True
        self._exit_condition = ExitCondition.ABORT
        self._exit_reason = reason
        self._exit_reason_code = "ABORT"

    def mark_timeout(self) -> None:
        """Mark execution as timed out."""
        self._exited = True
        self._exit_condition = ExitCondition.TIMEOUT
        self._exit_reason = "Execution timeout exceeded"
        self._exit_reason_code = "TIMEOUT"

    def compute_context_hash(self) -> str:
        """Compute hash of current context state."""
        content = json.dumps(self.payload, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

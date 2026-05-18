"""
trace_emitter.py — Schema-driven trace emission.

Governed by: CONSTITUTION_TRACE_EXECUTION_V0, SCHEMA_TRACE_EVENT_V0

All events validated against schema before emission.
"""

import json
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from omnibachi.implementation.execution.machine.execution_policy_loader import ExecutionPolicy, TraceDepth


@dataclass
class TraceEvent:
    """Single trace event conforming to SCHEMA_TRACE_EVENT_V0."""
    event_type: str
    timestamp: str
    execution_id: str
    sequence: int
    payload: dict[str, Any]
    prev_hash: str | None = None
    trace_schema_version: str = "V0"

    def to_dict(self) -> dict[str, Any]:
        d = {
            "trace_schema_version": self.trace_schema_version,   # ← ADD THIS LINE
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "execution_id": self.execution_id,
            "sequence": self.sequence,
            "payload": self.payload,
        }
        if self.prev_hash:
            d["prev_hash"] = self.prev_hash
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class TraceEmitter:
    """
    Schema-driven trace emitter.

    Per CONSTITUTION_TRACE_EXECUTION_V0:
    - Validates every event before emission
    - Maintains sequence counter
    - Computes hash chain (ADVANCED only)
    """

    # Valid event types from SCHEMA_TRACE_EVENT_V0
    # CRITICAL: "error" and "violation" are in BASIC_EVENTS to prevent silent failures in MINIMAL mode
    # Traceback is guarded by trace_depth (MINIMAL=no traceback, FULL=with traceback)
    BASIC_EVENTS = {"execution_start", "node_start", "node_end", "workflow_complete", "error", "violation"}
    ADVANCED_EVENTS = {
        "capability_dispatch", "transform_start", "transform_end",
        "side_effect_start", "side_effect_end", "context_snapshot",
        "audit_event",
        # Registry introspection events (GUARDRAIL 2: truth, not heuristics)
        "registry_dump", "capability_request", "capability_resolution"
    }
    ALL_EVENTS = BASIC_EVENTS | ADVANCED_EVENTS

    def __init__(
        self,
        execution_id: str,
        policy: ExecutionPolicy,
        sink: Callable[[str], None] | None = None,
    ):
        self._execution_id = execution_id
        self._policy = policy
        self._sequence = 0
        self._prev_hash: str | None = None
        self._sink = sink or self._null_sink
        self._events: list[TraceEvent] = []

        # Compute initial hash for ADVANCED
        if policy.trace_depth == TraceDepth.FULL:
            self._prev_hash = self._compute_initial_hash()

    @property
    def execution_id(self) -> str:
        return self._execution_id

    def _null_sink(self, line: str) -> None:
        pass

    def _compute_initial_hash(self) -> str:
        content = f"{self._execution_id}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _compute_event_hash(self, event: TraceEvent) -> str:
        content = event.to_json()
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _should_emit(self, event_type: str) -> bool:
        if event_type not in self.ALL_EVENTS:
            return False

        if self._policy.trace_depth == TraceDepth.MINIMAL:
            return event_type in self.BASIC_EVENTS

        return True

    def emit(self, event_type: str, **payload) -> TraceEvent | None:
        """Emit a trace event."""
        if not self._should_emit(event_type):
            return None

        self._sequence += 1

        event = TraceEvent(
            event_type=event_type,
            timestamp=self._now(),
            execution_id=self._execution_id,
            sequence=self._sequence,
            payload=payload,
            prev_hash=self._prev_hash if self._policy.is_advanced else None,
        )

        # Update hash chain for ADVANCED
        if self._policy.is_advanced:
            self._prev_hash = self._compute_event_hash(event)

        self._events.append(event)
        self._sink(event.to_json())

        return event

    # Convenience methods for BASIC events

    def execution_start(self, workflow_code: str, **extra) -> TraceEvent | None:
        return self.emit("execution_start", workflow_code=workflow_code, **extra)

    def node_start(self, node_id: str, node_type: str, **extra) -> TraceEvent | None:
        return self.emit("node_start", node_id=node_id, node_type=node_type, **extra)

    def node_end(self, node_id: str, status: str, duration_ms: int, **extra) -> TraceEvent | None:
        return self.emit("node_end", node_id=node_id, status=status, duration_ms=duration_ms, **extra)

    def workflow_complete(self, status: str, duration_ms: int, exit_condition: str, exit_reason_code: str = "COMPLETED", **extra) -> TraceEvent | None:
        return self.emit("workflow_complete", status=status, duration_ms=duration_ms, exit_condition=exit_condition, exit_reason_code=exit_reason_code, **extra)

    # Convenience methods for ADVANCED events

    def capability_dispatch(self, cc_code: str, node_id: str) -> TraceEvent | None:
        return self.emit("capability_dispatch", cc_code=cc_code, node_id=node_id)

    def transform_start(self, ct_code: str, inputs_hash: str = "") -> TraceEvent | None:
        return self.emit("transform_start", ct_code=ct_code, inputs_hash=inputs_hash)

    def transform_end(self, ct_code: str, duration_ms: int, outputs_hash: str = "") -> TraceEvent | None:
        return self.emit("transform_end", ct_code=ct_code, duration_ms=duration_ms, outputs_hash=outputs_hash)

    def error(self, component: str, exc: Exception, **context) -> TraceEvent | None:
        """
        Emit error event with traceback. Zero silent failures.

        Invariant: exc must be provided (no silent failures).
        Required keys: event_type, component, error_type, error_code, message.
        Optional: traceback (guarded by trace_depth), node_id, workflow, node_category.
        """
        import traceback as tb_module

        # Invariant: exc must be provided (no silent failures)
        assert exc is not None, "error() must include exception"

        # Guard traceback for SUMMARY mode (default FULL)
        traceback = None
        if self._policy.trace_depth == TraceDepth.FULL:
            traceback = "".join(tb_module.format_exception(type(exc), exc, exc.__traceback__))

        # error_code from StructuredError when available; class name for unstructured exceptions.
        error_code = getattr(exc, 'error_code', None) or exc.__class__.__name__

        # Event correlation (required for post-mortem debugging)
        payload = {
            "component": component,
            "error_type": exc.__class__.__name__,
            "error_code": error_code,  # CRITICAL: Required by SCHEMA_TRACE_EVENT_V0
            "message": str(exc),
            "traceback": traceback,
            "node_id": context.get("node"),
            "workflow": context.get("workflow"),
            "node_category": getattr(exc, 'node_category', None),  # From StructuredError
        }

        # Add additional context (filter out correlation keys to avoid duplication)
        payload.update({k: v for k, v in context.items() if k not in ("node", "workflow")})

        return self.emit("error", **payload)

    def violation(
        self,
        node_id: str,
        violation_code: str,
        message: str,
        field: str | None = None,
        constraint: str | None = None,
        **context
    ) -> TraceEvent | None:
        """
        Emit protocol violation event with structured detail.

        Use when workflow/CC/admission rejects due to protocol constraints
        (not runtime exceptions).

        INVARIANT (PATCH 1): Must be called BEFORE raising exception.
        SCHEMA DISCIPLINE (PATCH 3): Enforces canonical keys only.

        Args:
            node_id: Protocol node where violation occurred (REQUIRED)
                     Must be protocol-facing: "ADMISSION", "CC_VALIDATE_INPUT_V0"
                     Never runtime-internal names
            violation_code: Classification (REQUIRED)
                            Example: "MISSING_REQUIRED_INPUT", "ADMISSION_DENIED"
            message: Human-readable explanation (REQUIRED)
            field: JSONPath to problematic field (OPTIONAL, standardized)
                   Example: "$.payload.user_id"
            constraint: Rule that failed (OPTIONAL, standardized)
                        Example: "required: true", "enum: [A, B]"
            **context: Additional diagnostic info (OPTIONAL, standardized keys only)
                       Allowed: actual_value, expected_value
                       Forbidden: custom keys without approval

        Example:
            trace.violation(
                node_id="CC_VALIDATE_INPUT_V0",
                violation_code="MISSING_REQUIRED_INPUT",
                message="Required input missing",
                field="$.payload.employee_id",
                constraint="required: true",
                actual_value=None
            )

        Raises:
            AssertionError: If required fields missing (fail fast on misuse)
        """
        # PATCH 3: Schema discipline enforcement
        assert node_id is not None, "violation() requires node_id"
        assert violation_code is not None, "violation() requires violation_code"
        assert message is not None, "violation() requires message"

        # PATCH 3: Only standardized optional keys allowed
        allowed_optional = {"actual_value", "expected_value"}
        for key in context.keys():
            assert key in allowed_optional, f"Non-standard violation key: {key}"

        # PATCH 2: Violation contains protocol context only (no exception details)
        payload = {
            "node_id": node_id,
            "violation_code": violation_code,
            "message": message,
            "field": field,
            "constraint": constraint,
        }

        # Add standardized optional context
        payload.update(context)

        return self.emit("violation", **payload)

    # Convenience methods for ADVANCED registry introspection events (GUARDRAIL 2)

    def registry_dump(self, registered_capabilities: dict[str, dict]) -> TraceEvent | None:
        """
        Emit registry state snapshot showing all registered capabilities.

        Use at execution_start to show what capabilities are available.
        This is TRUTH - what the registry actually contains, not what we think should be there.

        Args:
            registered_capabilities: Dict mapping capability_code -> {type, artifact_found, implementation_found}

        Example:
            trace.registry_dump({
                "domains.blockchain::CT_PURE_DERIVE_WALLET_KEYPAIRS_V0": {
                    "type": "transform",
                    "artifact_found": True,
                    "implementation_found": True,
                    "registered": True
                },
                "CS_REGISTRY_V0": {
                    "type": "side_effect",
                    "artifact_found": True,
                    "implementation_found": True,
                    "registered": True
                }
            })
        """
        return self.emit("registry_dump", capabilities=registered_capabilities, count=len(registered_capabilities))

    def capability_request(self, capability_code: str, requested_by: str) -> TraceEvent | None:
        """
        Emit capability request event showing what was asked for.

        Use when workflow/CC requests a capability from the router.
        Shows the request, not the result (use capability_resolution for outcome).

        Args:
            capability_code: Capability being requested (FQDN or short code)
            requested_by: Node requesting the capability (e.g., "CC_VALIDATE_INPUT_V0")

        Example:
            trace.capability_request(
                capability_code="domains.blockchain::CT_PURE_DERIVE_WALLET_KEYPAIRS_V0",
                requested_by="CC_DERIVE_WALLET_KEYPAIRS_V0"
            )
        """
        return self.emit("capability_request", capability_code=capability_code, requested_by=requested_by)

    def capability_resolution(
        self,
        capability_code: str,
        artifact_found: bool,
        implementation_found: bool,
        registered: bool,
        resolution_path: str | None = None
    ) -> TraceEvent | None:
        """
        Emit capability resolution result showing the resolution chain.

        Use after attempting to resolve a capability to show what happened.
        This is DIAGNOSTIC TRUTH - shows where the resolution failed (if it did).

        Args:
            capability_code: Capability code that was resolved
            artifact_found: Whether artifact file was found
            implementation_found: Whether implementation metadata was found in artifact
            registered: Whether capability was successfully registered
            resolution_path: Optional path showing where artifact was found

        Example:
            trace.capability_resolution(
                capability_code="blockchain::CT_PURE_DERIVE_WALLET_KEYPAIRS_V0",
                artifact_found=True,
                implementation_found=False,
                registered=False,
                resolution_path="<snapshot_root>/artifacts/capability_transforms/..."
            )
        """
        payload = {
            "capability_code": capability_code,
            "artifact_found": artifact_found,
            "implementation_found": implementation_found,
            "registered": registered,
        }
        if resolution_path:
            payload["resolution_path"] = resolution_path

        return self.emit("capability_resolution", **payload)

    def get_events(self) -> list[TraceEvent]:
        return list(self._events)


class JsonlTraceSink:
    """JSONL file trace sink.

    STRUCTURE sovereignty: Caller must resolve path via paths.resolve_output_path().
    This class enforces contract - refuses unsafe paths.
    """

    def __init__(self, path: Path):
        # CONTRACT: Path must be STRUCTURE-resolved (absolute, no relative traversal)
        if not path.is_absolute():
            raise ValueError("STRUCTURE VIOLATION: trace path must be absolute")

        if ".." in path.parts:
            raise ValueError("STRUCTURE VIOLATION: path traversal detected ('..')")

        self._path = path

        # Parent directory MUST exist (caller responsibility via STRUCTURE)
        if not self._path.parent.exists():
            raise FileNotFoundError(
                f"Trace directory does not exist: {self._path.parent}\n"
                f"Caller must resolve path via STRUCTURE and create parent directory."
            )

        self._file = open(path, "a", encoding="utf-8")

    def write(self, line: str) -> None:
        self._file.write(line + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    def __call__(self, line: str) -> None:
        self.write(line)

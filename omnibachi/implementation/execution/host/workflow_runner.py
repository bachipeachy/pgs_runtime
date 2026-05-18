"""
workflow_runner.py — Protocol-governed workflow execution.

Governed by: CONSTITUTION_EXECUTION_V0

This module provides the high-level interface for workflow execution.
It is the single authority for workflow orchestration.

Input Resolution Rule (§6):
    All node input expressions SHALL be resolved prior to capability dispatch.
    Capability bindings SHALL resolve only against resolved node inputs.

Null Resolution Rule (§7):
    Any unresolved expression within a composite structure SHALL invalidate
    the entire resolution and emit a violation.
"""

import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from omnibachi.implementation.execution.machine.admission import check_admission
from omnibachi.implementation.execution.machine.dag_model import DAG, DAGNode, build_dag_from_workflow
from omnibachi.implementation.execution.machine.errors import StructuredError
from omnibachi.implementation.execution.machine.execution_context import ExecutionContext
from omnibachi.implementation.execution.machine.execution_policy_loader import ExecutionPolicy, BASIC_POLICY
from omnibachi.implementation.execution.machine.trace_emitter import TraceEmitter
from omnibachi.implementation.execution.machine.capability import CapabilityPipeline, CapabilityRouter
from omnibachi.implementation.execution.machine.capability.expression_resolver import resolve_expression

import re

# ── Format validators ─────────────────────────────────────────────────
# Minimal, spec-driven. No external dependencies.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")

def _is_valid_email(value: str) -> bool:
    """Check basic email format: local@domain.tld (TLD >= 2 chars)."""
    return bool(_EMAIL_RE.match(value))


# ── Exit classification — governance-frozen mapping ───────────────────
# Maps the last result_status before EXIT to a canonical exit_reason_code.
# No dynamic string synthesis. Unknown status → governance violation.
_EXIT_REASON_MAP: dict[str, str] = {
    "SUCCESS":        "COMPLETED",
    "ACK":            "COMPLETED",
    "ALREADY_EXISTS": "EXIT_ALREADY_EXISTS",
    "NOT_FOUND":      "EXIT_NOT_FOUND",
    "VIOLATION":      "EXIT_VIOLATION",
    "BACKEND_ERROR":  "EXIT_BACKEND_ERROR",
    "NACK":           "EXIT_REJECTED",
}

_SUCCESS_EXITS: frozenset[str] = frozenset({"COMPLETED"})


class WorkflowRunner:
    """
    Workflow runner using DAG-driven execution.

    Traverses DAG following edges based on result_status.
    No business logic, no policy decisions - pure protocol interpretation.
    """

    def __init__(
        self,
        workflow_spec: dict[str, Any],
        intent_specs: dict[str, Any],
        capability_contracts: dict[str, Any],
        capability_router: CapabilityRouter,
        ct_ir_registry: dict[str, dict] | None = None,
        data_root: Path | None = None,
    ):
        self._workflow_spec = workflow_spec
        self._intent_specs = intent_specs
        self._capability_contracts = capability_contracts
        self._capability_router = capability_router
        self._data_root = data_root

        # Extract workflow namespace for FQDN construction
        # PROTOCOL: Compiled artifacts have namespace at top-level (compiler metadata)
        self._workflow_namespace = workflow_spec.get("namespace")

        # Build immutable DAG
        self._dag = build_dag_from_workflow(workflow_spec)

        # Create capability pipeline
        # SOVEREIGNTY: ct_ir_registry must be provided by compiler (pre-validated CT-IR)
        self._pipeline = CapabilityPipeline(
            capability_router=capability_router,
            capability_contracts=capability_contracts,
            ct_ir_registry=ct_ir_registry,
            workflow_namespace=self._workflow_namespace,
        )

    @property
    def dag(self) -> DAG:
        """Return the immutable DAG."""
        return self._dag

    def _execute_intent(self, node: DAGNode, ctx: ExecutionContext) -> str:
        """
        Execute intent node, return result_status.

        Enforces the full intent input spec: required, type, enum, format.
        Supports nested field validation via 'fields' on object inputs.
        Returns ACK if all constraints pass, NACK on first violation.
        """
        intent_spec = self._intent_specs.get(node.capability_code)

        # ARCHITECTURE: Runtime must work from pre-loaded artifacts ONLY (from snapshot)
        # All intent specs must be pre-loaded from protocol_snapshot/
        # No dynamic resolution - fail hard if not in _intent_specs dict
        if not intent_spec:
            raise StructuredError(
                error_code="INTENT_NOT_FOUND",
                node_category="IN",
                message=f"Intent not found: {node.capability_code}",
            )

        # PROTOCOL: Compiled artifacts have core in frontmatter
        frontmatter = intent_spec.get("frontmatter", {})
        inputs_spec = frontmatter.get("core", {}).get("inputs", {})

        for field, spec in inputs_spec.items():
            value = ctx.payload.get(field)

            # Required check
            if spec.get("required", False) and value is None:
                return "NACK"

            # Type check (when value present)
            if value is not None:
                expected_type = spec.get("type")
                if expected_type == "string" and not isinstance(value, str):
                    return "NACK"
                if expected_type == "object" and not isinstance(value, dict):
                    return "NACK"

            # Enum check
            allowed = spec.get("enum")
            if allowed and value not in allowed:
                return "NACK"

            # Nested field validation for object inputs
            fields_spec = spec.get("fields")
            if fields_spec and isinstance(value, dict):
                if not self._validate_fields(value, fields_spec):
                    return "NACK"

        return "ACK"

    @staticmethod
    def _validate_fields(obj: dict, fields_spec: dict) -> bool:
        """Validate nested fields within an object input."""
        for field_name, field_spec in fields_spec.items():
            value = obj.get(field_name)

            if field_spec.get("required", False) and (value is None or value == ""):
                return False

            if value is not None:
                expected_type = field_spec.get("type")
                if expected_type == "string" and not isinstance(value, str):
                    return False

                # Format validation
                fmt = field_spec.get("format")
                if fmt == "email" and isinstance(value, str):
                    if not _is_valid_email(value):
                        return False

        return True

    def _execute_transport_ingress(self, node: DAGNode, ctx: ExecutionContext) -> str:
        """
        Execute transport egress (TI) node, return result_status.

        TI nodes validate structural presence of required payload fields
        based on the TI admission schema. Detailed validation is delegated
        to CC_VALIDATE_HTTP_REQUEST_V0 downstream.

        Returns ACK if payload has required top-level fields, NACK otherwise.
        """
        ti_spec = self._intent_specs.get(node.capability_code)
        if not ti_spec:
            raise StructuredError(
                error_code="E_TI_SPEC_NOT_FOUND",
                node_category="TI",
                message=f"Missing TI spec for {node.capability_code}",
            )

        # PROTOCOL: Compiled artifacts have core in frontmatter
        frontmatter = ti_spec.get("frontmatter", {})
        admission_schema = frontmatter.get("core", {}).get("admission_schema", {})
        request_body = ctx.payload.get("request_body", {})

        for field, spec in admission_schema.items():
            if spec.get("required", False) and field not in request_body:
                return "NACK"

        return "ACK"

    def _execute_capability_contract(self, node: DAGNode, ctx: ExecutionContext) -> str:
        """
        Execute capability contract node, return result_status.

        Per Input Resolution Rule:
        - Resolve node input bindings against context payload
        - Pass resolved inputs to capability pipeline
        """
        # Stage 1: Resolve node input bindings against context payload
        # Include both payload and results in state for $.payload.* and $.results.* resolution
        resolved_inputs = {}
        state = {
            "payload": ctx.payload,
            "results": ctx.payload.get("results", {})
        }
        logger.debug("CC=%s input_bindings=%s", node.capability_code, node.input_bindings)
        logger.debug("CC=%s results_keys=%s", node.capability_code, list(state["results"].keys()))
        for name, expr in node.input_bindings.items():
            resolved_inputs[name] = resolve_expression(expr, state)
        logger.debug("CC=%s resolved_inputs=%s", node.capability_code, resolved_inputs)

        # GUARDRAIL 2: Emit capability request (what CC is being asked for)
        if ctx.policy.is_advanced:
            ctx.trace.capability_request(
                capability_code=node.capability_code,
                requested_by=node.node_id,
            )

        # Stage 2: Execute capability pipeline with resolved inputs
        try:
            result = self._pipeline.execute(
                cc_code=node.capability_code,
                payload=resolved_inputs,
            )
            # GUARDRAIL 2: Emit successful capability resolution
            if ctx.policy.is_advanced:
                ctx.trace.capability_resolution(
                    capability_code=node.capability_code,
                    artifact_found=True,
                    implementation_found=True,
                    registered=True,
                )
        except StructuredError as e:
            # GUARDRAIL 2: Emit failed capability resolution (shows WHY it failed)
            if ctx.policy.is_advanced:
                # Determine what failed based on error code
                if e.error_code == "CAPABILITY_DISPATCH_FAILED" and "No host for capability" in str(e):
                    # Runtime not registered
                    ctx.trace.capability_resolution(
                        capability_code=node.capability_code,
                        artifact_found=True,  # CC artifact was found (else different error)
                        implementation_found=False,  # Unknown at this point
                        registered=False,  # TRUTH: Not in registry
                    )
                elif e.error_code == "CAPABILITY_NOT_FOUND":
                    # CC artifact not found
                    ctx.trace.capability_resolution(
                        capability_code=node.capability_code,
                        artifact_found=False,
                        implementation_found=False,
                        registered=False,
                    )
                elif e.error_code == "CT_IR_NOT_FOUND":
                    # CT-IR not in registry
                    ctx.trace.capability_resolution(
                        capability_code=node.capability_code,
                        artifact_found=True,
                        implementation_found=False,
                        registered=False,
                    )
            # Re-raise to preserve existing error handling
            raise

        # Stage 3: Store CC outputs in results[node_id] for $.results.* coordinate system
        # Strip plumbing fields (node_code, result_status) from outputs
        outputs = {k: v for k, v in result.items() if k not in ("node_code", "result_status")}

        # Initialize results dict if needed
        if "results" not in ctx.payload:
            ctx.payload["results"] = {}

        # Nest outputs under node_id (short YAML key) for workflow-level $.results.NODE_ID.* access
        # node.node_id is the YAML key (e.g. CC_GENERATE_ACTOR_ID_V0)
        # node.capability_code is the FQDN (e.g. blockchain::CC_GENERATE_ACTOR_ID_V0)
        # Protocol expressions use short node keys: $.results.CC_GENERATE_ACTOR_ID_V0.*
        ctx.payload["results"][node.node_id] = outputs
        logger.debug("CC=%s stored outputs=%s", node.node_id, outputs)

        return result.get("result_status", "SUCCESS")

    def execute(
        self,
        payload: dict[str, Any],
        trace_emitter: TraceEmitter,
        policy: ExecutionPolicy = BASIC_POLICY,
    ) -> ExecutionContext:
        """
        Execute the workflow following DAG edges.

        Args:
            payload: Initial workflow payload
            trace_emitter: Trace emitter for observability
            policy: Execution policy

        Returns:
            ExecutionContext with final state
        """
        ctx = ExecutionContext(
            workflow_code=self._dag.workflow_code,
            initial_payload=payload,
            trace_emitter=trace_emitter,
            policy=policy,
        )

        # Emit execution start — trace_schema_version pins the parser contract.
        # Increment this when the trace event schema changes in a breaking way.
        trace_emitter.execution_start(
            workflow_code=self._dag.workflow_code,
            policy_profile=policy.policy_profile,
            trace_schema_version="TRACE_SCHEMA_V0",
        )

        # GUARDRAIL 2: Emit registry state snapshot (TRUTH - what's actually registered)
        if policy.is_advanced:
            registered_capabilities = self._capability_router.get_registered_capabilities()
            trace_emitter.registry_dump(registered_capabilities)

        # ── Admission gate (read-only, pre-DAG) ──────────────
        try:
            # PROTOCOL: Compiled artifacts have core in frontmatter
            frontmatter = self._workflow_spec.get("frontmatter", {})
            admission = frontmatter.get("core", {}).get("admission")
            if admission and self._data_root:
                admission_result = check_admission(
                    admission, ctx.payload, self._data_root,
                )
                if admission_result != "ADMITTED":
                    # PATCH 1: Emit violation BEFORE raising (ordering invariant)
                    trace_emitter.violation(
                        node_id="ADMISSION",  # Protocol-facing identifier
                        violation_code="ADMISSION_DENIED",
                        message=f"Admission denied: {admission_result}",
                        constraint=admission_result,  # e.g., "requires EV_EMPLOYEE_REGISTERED_V0"
                    )
                    # Now raise (violation already emitted)
                    raise StructuredError(
                        error_code="ADMISSION_DENIED",
                        node_category="WF",
                        message=f"Admission denied: {admission_result}",
                    )
        except StructuredError as e:
            # PATCH 2: Error event contains minimal exception info only
            # (violation event already contains protocol context)
            trace_emitter.error(
                component="admission_check",
                exc=e,
                node_id="ADMISSION",
                node_category=e.node_category,
            )
            ctx.mark_failure(str(e), exit_reason_code="ADMISSION_DENIED")
            return ctx

        # Start at entry node
        if not self._dag.entry_nodes:
            ctx.mark_failure("No entry node", exit_reason_code="NO_ENTRY_NODE")
            return ctx

        current_node_id = self._dag.entry_nodes[0]
        last_result_status = None
        last_node_id = None

        # Traverse DAG following edges
        while current_node_id and not ctx.exited:
            node = self._dag.get_node(current_node_id)
            if not node:
                ctx.mark_failure(f"Node not found: {current_node_id}", exit_reason_code="NODE_NOT_FOUND")
                break

            # Check for terminal/exit node — classify based on edge that led here
            if node.node_type == "exit":
                trace_emitter.node_start(
                    node_id=node.node_id,
                    node_type=node.node_type,
                )
                trace_emitter.node_end(
                    node_id=node.node_id,
                    status="completed",
                    duration_ms=0,
                )

                # EXIT node's declared reason takes precedence when explicit.
                # "COMPLETED" → unconditional success (supports best-effort patterns).
                # "FAILED" → unconditional failure.
                # "HTTP_200" → transport success.
                # "HTTP_xxx" (other) → transport failure with domain exit reason.
                # "EXITED" or unset → classify based on the edge that led here.
                if node.exit_reason == "COMPLETED":
                    ctx.mark_success()
                elif node.exit_reason == "HTTP_200":
                    ctx.mark_success()
                elif node.exit_reason and node.exit_reason.startswith("HTTP_"):
                    ctx.mark_failure(
                        f"Transport exit: {node.exit_reason}",
                        exit_reason_code=f"EXIT_{node.exit_reason}",
                    )
                elif node.exit_reason == "FAILED":
                    exit_reason_code = _EXIT_REASON_MAP.get(last_result_status, "EXECUTION_ERROR")
                    ctx.mark_failure(
                        f"Workflow exited via {last_result_status} at {last_node_id}",
                        exit_reason_code=exit_reason_code,
                    )
                else:
                    # Edge-based classification for generic exits
                    exit_reason_code = _EXIT_REASON_MAP.get(last_result_status)
                    if exit_reason_code is None:
                        ctx.mark_failure(
                            f"Unmapped exit status: {last_result_status} at {last_node_id}",
                            exit_reason_code="GOVERNANCE_VIOLATION",
                        )
                    elif exit_reason_code in _SUCCESS_EXITS:
                        ctx.mark_success()
                    else:
                        ctx.mark_failure(
                            f"Workflow exited via {last_result_status} at {last_node_id}",
                            exit_reason_code=exit_reason_code,
                        )
                break

            # Emit node start
            trace_emitter.node_start(
                node_id=node.node_id,
                node_type=node.node_type,
                capability_code=node.capability_code,
            )

            start_time = time.perf_counter()

            # Execute node based on type
            try:
                if node.node_type == "intent":
                    result_status = self._execute_intent(node, ctx)
                elif node.node_type == "transport_ingress":
                    result_status = self._execute_transport_ingress(node, ctx)
                elif node.node_type == "capability_contract":
                    trace_emitter.capability_dispatch(
                        cc_code=node.capability_code,
                        node_id=node.node_id,
                    )
                    result_status = self._execute_capability_contract(node, ctx)
                else:
                    result_status = "SUCCESS"

            except StructuredError as e:
                trace_emitter.error(
                    component="workflow_executor",
                    exc=e,
                    node_id=node.node_id,
                    node_category=e.node_category,
                )
                ctx.mark_failure(str(e), exit_reason_code="EXECUTION_ERROR")
                break
            except Exception as e:
                wrapped = StructuredError(
                    error_code="EXECUTION_ERROR",
                    node_category="WF",
                    message=str(e),
                    cause=e,
                )
                trace_emitter.error(
                    component="workflow_executor",
                    exc=wrapped,
                    node_id=node.node_id,
                    node_category=wrapped.node_category,
                )
                ctx.mark_failure(str(wrapped), exit_reason_code="EXECUTION_ERROR")
                break

            duration_ms = int((time.perf_counter() - start_time) * 1000)

            # Emit node end
            trace_emitter.node_end(
                node_id=node.node_id,
                status=result_status,
                duration_ms=duration_ms,
            )

            # Track last status and node for exit classification
            last_result_status = result_status
            last_node_id = current_node_id

            # Follow edge based on result_status
            next_node_id = self._dag.get_next_node(current_node_id, result_status)
            if not next_node_id:
                ctx.mark_failure(
                    f"No transition from {current_node_id} for {result_status}",
                    exit_reason_code="NO_TRANSITION",
                )
                break

            current_node_id = next_node_id

        return ctx

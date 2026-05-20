"""
workflow_gateway.py — Single execution facade for all transports.

Governed by: CONSTITUTION_EXECUTION_V0

SNAPSHOT-BASED EXECUTION:
All artifacts loaded from internal protocol_snapshot/artifacts/ via
strict FQDN-to-filename mapping. No search, no recursion.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from omnibachi.implementation.execution import (
    ExitCondition,
    BASIC_POLICY,
    ADVANCED_POLICY,
    TraceEmitter,
    generate_execution_id,
)
from omnibachi.implementation.execution.host import WorkflowRunner
from omnibachi.implementation.execution.host import RuntimeLoader
from omnibachi.implementation.ingress.gateway.execution_result import ExecutionResult
from omnibachi.implementation.egress.trace.trace_egress_adapter import TraceEgressAdapter
from omnibachi.implementation.egress.trace.trace_png_renderer import render_png

# ── Plumbing fields stripped from result_payload ──────────────────
_PLUMBING_KEYS = frozenset({
    "node_code",
    "result_status",
})

# ── Gateway result enrichment — static message map ────────────────
_EXIT_MESSAGES: dict[str, str | None] = {
    "COMPLETED":            None,
    "ADMISSION_DENIED":     "Workflow prerequisites not met. Check execution sequence.",
    "EXIT_NOT_FOUND":       "Required entity not found. A prior workflow may need to run first.",
    "EXIT_VIOLATION":       "Input validation failed. Check payload structure.",
    "EXIT_BACKEND_ERROR":   "Infrastructure error during execution.",
    "EXIT_REJECTED":        "Request rejected. Check input values.",
    "GOVERNANCE_VIOLATION": "Internal governance violation. Contact support.",
    "NO_TRANSITION":        "Workflow routing error. No transition for result status.",
    "EXECUTION_ERROR":      "Execution error. Check trace for details.",
    "NO_ENTRY_NODE":        "Workflow has no entry node.",
    "NODE_NOT_FOUND":       "Workflow references a missing node.",
    "SNAPSHOT_NOT_FOUND":   "Internal protocol snapshot not found.",
    "WORKFLOW_NOT_FOUND":   "Specified workflow artifact not found in snapshot.",
    "RB_NOT_FOUND":         "Required runtime binding artifact not found in snapshot.",
}

_DEFAULT_FAILURE_MESSAGE = "Execution did not complete normally."


def _fqdn_to_filename(fqdn: str) -> str:
    """
    Deterministic mapping: FQDN -> filename.
    Replaces namespace delimiter '::' with '__'.
    """
    return f"{fqdn.replace('::', '__')}.json"


def load_workflow_artifact(snapshot_root: Path, wf_fqdn: str) -> dict:
    """
    Load workflow artifact directly from snapshot using strict mapping.

    Location: {snapshot_root}/artifacts/workflows/{wf_fqdn_as_filename}.json
    """
    filename = _fqdn_to_filename(wf_fqdn)
    path = snapshot_root / "artifacts" / "workflows" / filename
    if not path.exists():
        raise FileNotFoundError(f"Workflow artifact not found at {path}")
    with open(path) as f:
        return json.load(f)


def load_rb_artifact(snapshot_root: Path, rb_fqdn: str) -> Path:
    """
    Resolve path to RB artifact directly from snapshot using strict mapping.

    Location: {snapshot_root}/artifacts/runtime_bindings/{rb_fqdn_as_filename}.json
    """
    filename = _fqdn_to_filename(rb_fqdn)
    path = snapshot_root / "artifacts" / "runtime_bindings" / filename
    if not path.exists():
        raise FileNotFoundError(f"Runtime binding artifact not found at {path}")
    return path


def _load_artifact(snapshot_root: Path, artifact_type_dir: str, fqdn: str) -> dict:
    """
    Load a single artifact by FQDN. Fails hard if not found.
    """
    path = snapshot_root / "artifacts" / artifact_type_dir / _fqdn_to_filename(fqdn)
    if not path.exists():
        raise FileNotFoundError(f"Artifact not found at {path}")
    with open(path) as f:
        return json.load(f)


def _load_workflow_dependencies(snapshot_root: Path, wf_artifact: dict) -> tuple[dict, dict, dict]:
    """
    Load intents, capability contracts, and CT-IR declared in the workflow nodes.
    No filesystem scanning. Strict FQDN lookup only.

    Returns:
        (intent_specs, capability_contracts, ct_ir_registry)
    """
    nodes = wf_artifact.get("frontmatter", {}).get("core", {}).get("nodes", {})

    intent_specs: dict[str, dict] = {}
    capability_contracts: dict[str, dict] = {}
    ct_ir_registry: dict[str, dict] = {}

    for node_key, node in nodes.items():
        node_type = node.get("type")
        fqdn = node.get("fqdn_id") or node.get("code")

        if node_type == "IN" and fqdn:
            artifact = _load_artifact(snapshot_root, "intents", fqdn)
            intent_specs[fqdn] = artifact

        elif node_type == "CC" and fqdn:
            artifact = _load_artifact(snapshot_root, "capability_contracts", fqdn)
            capability_contracts[fqdn] = artifact

            # Trace CT references in CC pipeline steps → load CT-IR
            pipeline = artifact.get("frontmatter", {}).get("core", {}).get("pipeline", [])
            for step in pipeline:
                ct_fqdn = step.get("transform")
                if ct_fqdn and ct_fqdn not in ct_ir_registry:
                    ct_artifact = _load_artifact(snapshot_root, "capability_transforms", ct_fqdn)
                    ct_ir = ct_artifact.get("ct_ir")
                    if ct_ir is None:
                        raise FileNotFoundError(f"CT artifact missing 'ct_ir' field: {ct_fqdn}")
                    ct_ir_registry[ct_fqdn] = ct_ir

    return intent_specs, capability_contracts, ct_ir_registry


class _WorkflowNotFound(Exception):
    pass


class _RBNotFound(Exception):
    pass


def execute_workflow(
    *,
    workflow_code: str | None = None,
    intent_code: str | None = None,
    payload: dict,
    runtime_binding: str | None = None,
    snapshot_root: Path | None = None,
    data_root: Path,
    trace_root: Path,
    mode: str = "runtime",
) -> tuple[ExecutionResult, list]:
    """
    Execute a workflow and return (ExecutionResult, list[TraceEvent]).

    Per CONSTITUTION_TRACE_EXECUTION_V0:
    - Execution is pure: no file I/O during execution loop
    - Trace events collected in memory, flushed via TraceEgressAdapter after execution
    - data_root MUST be provided explicitly (no __file__ traversal)
    """
    if not workflow_code and not intent_code:
        raise ValueError("Either workflow_code or intent_code must be provided")

    start_time = time.perf_counter()
    execution_id = generate_execution_id()

    # ── Path Resolution ───────────────────────────────────────
    # snapshot_root: resolved from PGS_WORKSPACE env var when not passed explicitly.
    # Lives at {workspace}/protocol_snapshot/ — populated by sync_protocol_snapshot.sh.
    # __file__ traversal is forbidden (CLAUDE.md: no relative path or parent traversal).
    if snapshot_root is None:
        workspace_str = os.environ.get("PGS_WORKSPACE")
        if not workspace_str:
            raise ValueError(
                "SNAPSHOT_ROOT_REQUIRED: pass snapshot_root explicitly "
                "or set PGS_WORKSPACE env var"
            )
        snapshot_root = Path(workspace_str) / "protocol_snapshot"

    # data_root: CS domain state (registry, events) — per CONSTITUTION_TRACE_EXECUTION_V0 §5
    # trace_root: execution observability artifacts — separate concern from domain data
    # Both MUST be provided explicitly — no __file__ traversal, no implicit defaults
    if not data_root.is_absolute():
        raise ValueError(
            f"DATA_ROOT_REQUIRED: data_root must be an absolute path, got: {data_root}"
        )
    if not trace_root.is_absolute():
        raise ValueError(
            f"TRACE_ROOT_REQUIRED: trace_root must be an absolute path, got: {trace_root}"
        )

    # ── STEP 1: Load Artifacts ────────────────────────────────
    if not snapshot_root.exists():
        return ExecutionResult(
            status="FAILED",
            exit_reason_code="SNAPSHOT_NOT_FOUND",
            trace_id=execution_id,
            duration_ms=0,
            result_payload={},
            workflow_code=workflow_code or intent_code,
            module="",
            error_code="SNAPSHOT_NOT_FOUND",
            message=f"Snapshot root not found: {snapshot_root}",
        ), []

    try:
        # 1. Resolve workflow FQDN
        if not workflow_code:
            intent_artifact = _load_artifact(snapshot_root, "intents", intent_code)
            wf_fqdn = intent_artifact["core"]["workflow"]
        else:
            wf_fqdn = workflow_code

        # 2. Load Workflow
        wf_artifact = load_workflow_artifact(snapshot_root, wf_fqdn)

        # 3. Resolve RB — declared in frontmatter.runtime_binding (single canonical field)
        rb_fqdn = runtime_binding or wf_artifact["frontmatter"]["runtime_binding"]
        if not rb_fqdn:
            raise ValueError(f"Workflow {wf_fqdn} does not declare runtime_binding")

        # 4. Load RB
        rb_artifact_path = load_rb_artifact(snapshot_root, rb_fqdn)

    except FileNotFoundError as e:
        msg = str(e)
        reason = "WORKFLOW_NOT_FOUND" if "Workflow artifact" in msg else "RB_NOT_FOUND"
        return ExecutionResult(
            status="FAILED",
            exit_reason_code=reason,
            trace_id=execution_id,
            duration_ms=0,
            result_payload={},
            workflow_code=workflow_code or intent_code,
            module="",
            error_code="NOT_FOUND",
            message=msg,
        ), []
    except Exception as e:
        return ExecutionResult(
            status="FAILED",
            exit_reason_code="EXECUTION_ERROR",
            trace_id=execution_id,
            duration_ms=0,
            result_payload={},
            workflow_code=workflow_code or intent_code,
            module="",
            error_code="INTERNAL_ERROR",
            message=str(e),
        ), []

    # ── STEP 2: Execution ─────────────────────────────────────
    data_root.mkdir(parents=True, exist_ok=True)
    namespace = wf_artifact["namespace"]
    if not namespace:
        raise ValueError(f"Workflow artifact {wf_fqdn} missing namespace field")
    module = namespace.lower().replace("_", ".")

    # Instantiate router via RuntimeLoader
    # data_root passed explicitly — per CONSTITUTION_TRACE_EXECUTION_V0 §5
    runtime_loader = RuntimeLoader(
        rb_path=rb_artifact_path,
        snapshot_root=snapshot_root,
        module_name=module,
        module_data_root=str(data_root),
    )
    capability_router = runtime_loader.load()

    # Execution is pure w.r.t. trace — no sink injected during execution
    # Per CONSTITUTION_TRACE_EXECUTION_V0 §2: TraceEmitter collects to memory buffer
    policy = ADVANCED_POLICY if mode == "authoring" else BASIC_POLICY
    trace_emitter = TraceEmitter(execution_id=execution_id, policy=policy)

    intent_specs, capability_contracts, ct_ir_registry = _load_workflow_dependencies(snapshot_root, wf_artifact)

    runner = WorkflowRunner(
        workflow_spec=wf_artifact,
        intent_specs=intent_specs,
        capability_contracts=capability_contracts,
        capability_router=capability_router,
        ct_ir_registry=ct_ir_registry,
        data_root=data_root,
    )

    try:
        ctx = runner.execute(payload, trace_emitter, policy)
    except Exception as e:
        trace_emitter.error(component="workflow_executor", exc=e, workflow=wf_fqdn, node=None)
        raise

    # Compute exit fields before emitting workflow_complete — emitter must receive
    # them before get_events() so the terminal event appears in the flushed trace.
    duration_ms = int((time.perf_counter() - start_time) * 1000)
    exit_status = "SUCCESS" if ctx.exit_condition == ExitCondition.SUCCESS else "FAILED"
    reason_code = ctx.exit_reason_code or "COMPLETED"
    trace_emitter.workflow_complete(
        status=exit_status,
        duration_ms=duration_ms,
        exit_condition=ctx.exit_condition.value if ctx.exit_condition else "UNKNOWN",
        exit_reason_code=reason_code,
    )

    # ── STEP 3: Egress — flush trace as value post-execution ──
    # Per CONSTITUTION_TRACE_EXECUTION_V0 §7: egress owns all trace I/O
    trace_events = trace_emitter.get_events()

    # Route traces into domain/subdomain directories for human-inspectable organization.
    # Domain derived from namespace; subdomain from frontmatter (declared in WF source).
    trace_domain = namespace
    trace_subdomain = wf_artifact.get("frontmatter", {}).get("subdomain", "")
    if trace_subdomain:
        trace_path = trace_root / trace_domain / trace_subdomain / execution_id / f"{execution_id}.jsonl"
    else:
        trace_path = trace_root / trace_domain / execution_id / f"{execution_id}.jsonl"

    TraceEgressAdapter().flush(trace_events, trace_path)

    # Render DAG + trace overlay PNG — requires wf_artifact, called here
    # not inside flush() which only owns serialization + MD projection.
    render_png(
        wf_artifact=wf_artifact,
        trace_events=[e.to_dict() for e in trace_events],
        png_path=trace_path.with_suffix(".png"),
    )

    return ExecutionResult(
        status=exit_status,
        exit_reason_code=reason_code,
        trace_id=execution_id,
        duration_ms=duration_ms,
        result_payload={k: v for k, v in ctx.payload.items() if k not in _PLUMBING_KEYS},
        workflow_code=wf_fqdn,
        module=module,
        error_code=None if exit_status == "SUCCESS" else reason_code,
        message=_EXIT_MESSAGES.get(reason_code, _DEFAULT_FAILURE_MESSAGE) if exit_status != "SUCCESS" else None,
    ), trace_events

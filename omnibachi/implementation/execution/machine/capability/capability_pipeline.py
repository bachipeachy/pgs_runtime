"""
capability_pipeline.py — Capability contract pipeline execution.

Governed by: CONSTITUTION_EXECUTION_V0

Executes capability contract pipelines deterministically.
"""

import logging
from typing import Any

from omnibachi.implementation.execution.machine.capability.capability_router import CapabilityRouter

logger = logging.getLogger(__name__)
from omnibachi.implementation.execution.machine.capability.expression_resolver import (
    resolve_inputs,
    resolve_outputs,
)
from omnibachi.implementation.execution.machine.errors import StructuredError
from omnibachi.implementation.execution.machine.transforms import execute_ct
from omnibachi.implementation.execution.machine.transforms.ct_executor import CTExecutionError


class CapabilityPipeline:
    """
    Executes capability contract pipelines.

    Responsibilities:
    - Execute CC pipelines deterministically
    - Delegate CS/CT execution
    - Enforce protocol-declared on_result routing
    - Resolve input/output expressions

    Non-responsibilities:
    - Result status invention
    - Control flow decisions outside protocol
    - DAG interpretation
    - CT-IR loading/validation (delegated to compiler)
    """

    def __init__(
        self,
        capability_router: CapabilityRouter,
        capability_contracts: dict[str, Any],
        ct_ir_registry: dict[str, dict] | None = None,
        workflow_namespace: str | None = None,
    ):
        self._router = capability_router
        self._contracts = capability_contracts
        self._ct_ir_registry = ct_ir_registry or {}
        self._workflow_namespace = workflow_namespace

    def execute(
        self,
        cc_code: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Execute a capability contract pipeline.

        Returns:
            Result dict with result_status and outputs
        """
        contract = self._contracts.get(cc_code)

        # ARCHITECTURE: Runtime must work from pre-loaded artifacts ONLY (from snapshot)
        # All capability contracts must be pre-loaded from protocol_snapshot/
        # No dynamic resolution - fail hard if not in _contracts dict
        if not contract:
            raise StructuredError(
                error_code="CAPABILITY_NOT_FOUND",
                node_category="CC",
                message=f"Unknown capability contract: {cc_code}",
            )

        # PROTOCOL: Compiled artifacts have core in frontmatter
        # No fallbacks - if frontmatter missing, artifact is malformed
        frontmatter = contract.get("frontmatter")
        if not frontmatter:
            raise StructuredError(
                error_code="ARTIFACT_MALFORMED",
                node_category="CC",
                message=f"CC {cc_code}: missing frontmatter (not a compiled artifact)",
            )
        core = frontmatter.get("core", {})

        pipeline = core.get("pipeline", [])

        # §4.5 Schema Enforcement - Reject non-canonical schemas
        if not pipeline:
            raise StructuredError(
                error_code="SCHEMA_VIOLATION",
                node_category="CC",
                message=f"CC {cc_code}: pipeline is empty or missing",
            )

        if isinstance(pipeline[0], str):
            raise StructuredError(
                error_code="SCHEMA_VIOLATION",
                node_category="CC",
                message=f"CC {cc_code}: String-based pipeline is forbidden. Use canonical step-based schema per CONSTITUTION_CAPABILITY_CONTRACTS_V0 §4.4",
            )

        if "bindings" in core:
            raise StructuredError(
                error_code="SCHEMA_VIOLATION",
                node_category="CC",
                message=f"CC {cc_code}: Separate bindings section is forbidden. Use inline step bindings per CONSTITUTION_CAPABILITY_CONTRACTS_V0 §4.4",
            )

        result_status_contract = core.get("result_status_contract", {})
        on_input_failure = result_status_contract.get("on_input_failure", "VIOLATION")

        pipeline_state: dict[str, Any] = {
            "inputs": payload,
            "results": {},
        }

        # CONSTITUTIONAL GUARD: capability_result must NEVER exist at pipeline state top level
        # It must be scoped per step: pipeline_state["results"][step_name]["capability_result"]
        assert "capability_result" not in pipeline_state, \
            "PROTOCOL VIOLATION: capability_result must be scoped per step, not global"

        final_status = None

        # Execute canonical pipeline (step-based schema)
        for step in pipeline:
            # Extract capability code from step (canonical schema)
            step_id = step.get("step")
            capability_code = step.get("transform") or step.get("side_effect")

            if not capability_code:
                raise StructuredError(
                    error_code="SCHEMA_VIOLATION",
                    node_category="CC",
                    message=f"CC {cc_code}: Step '{step_id}' missing transform or side_effect declaration",
                )

            # Extract inline bindings from step (canonical schema)
            step_inputs = step.get("inputs", {})
            step_outputs = step.get("outputs", {})
            step_on_result = step.get("on_result", {})
            op = step.get("op")  # For CS operations
            store = step.get("store")  # For entity-based storage resolution

            step_state = {
                "payload": pipeline_state["inputs"],
                "inputs": pipeline_state["inputs"],
                "results": pipeline_state["results"],
                "capability_result": None,
            }

            # Build binding object from inline step declarations
            binding = {
                "inputs": step_inputs,
                "outputs": step_outputs,
                "on_result": step_on_result,
                "op": op,
            }

            # Resolve inputs
            logger.debug("step=%r cap=%r step_inputs=%s", step_id, capability_code, step_inputs)
            logger.debug("step=%r pipeline_inputs=%s results_keys=%s", step_id, pipeline_state["inputs"], list(pipeline_state["results"].keys()))
            cap_inputs, failed_input = resolve_inputs(binding, step_state)
            if failed_input:
                logger.warning(
                    "Input resolution failed  cc=%s  step=%s  failed_input=%s  binding_inputs=%s  state_inputs=%s",
                    cc_code, step_id or capability_code, failed_input,
                    binding.get("inputs", {}), step_state.get("inputs", {}),
                )
                return self._build_result(
                    cc_code=cc_code,
                    status=on_input_failure,
                    outputs={},
                    exited_at=step_id or capability_code,
                )

            # Execute capability
            logger.debug("Executing capability  %s  inputs=%s", capability_code, cap_inputs)
            # PROTOCOL: Capability code may be FQDN (namespace::CODE) or bare code (CT_*/CS_*)
            # Check if it's a transform: contains ::CT_ or starts with CT_
            is_transform = "::CT_" in capability_code or capability_code.startswith("CT_")
            if is_transform:
                result = self._execute_ct(capability_code, cap_inputs, binding)
            else:
                result = self._execute_cs(capability_code, op, cap_inputs, store)

            step_state["capability_result"] = result
            current_status = result.get("result_status")
            logger.debug("Capability result  %s  status=%s  result=%s", capability_code, current_status, result)

            # Resolve outputs
            outputs = resolve_outputs(binding, step_state)

            logger.debug("Outputs resolved  step=%s  binding=%s  resolved=%s", step_id or capability_code, binding.get("outputs", {}), outputs)

            # Store both capability_result AND resolved outputs for JSONPath resolution
            # Supports both:
            # - $.results.STEP_NAME.capability_result.value (raw CS/CT result)
            # - $.results.STEP_NAME.output_name (resolved output bindings)
            pipeline_state["results"][step_id or capability_code] = {
                "capability_result": result,
                **outputs  # Merge resolved outputs at same level
            }

            # If outputs resolved a result_status, it overrides the
            # capability-level status for routing.  This allows CT steps
            # whose return value contains a business result_status (e.g.
            # VIOLATION from a validator) to govern on_result routing
            # through the CC output bindings.
            if "result_status" in outputs:
                current_status = outputs["result_status"]

            final_status = current_status

            if current_status is None:
                raise StructuredError(
                    error_code="CAPABILITY_DISPATCH_FAILED",
                    node_category="CC",
                    message=f"Capability {capability_code} (step: {step_id}) missing result_status",
                )

            # Check on_result routing — exit unless explicitly "continue"
            action = step_on_result.get(current_status)
            if action != "continue":
                return self._build_result(
                    cc_code=cc_code,
                    status=current_status,
                    outputs=outputs,
                    exited_at=step_id or capability_code,
                )

        # Pipeline completed
        last_step = pipeline[-1] if pipeline else None
        if last_step:
            last_step_id = last_step.get("step") or (last_step.get("transform") or last_step.get("side_effect"))
            final_outputs = pipeline_state["results"].get(last_step_id, {})
        else:
            final_outputs = {}

        return self._build_result(
            cc_code=cc_code,
            status=final_status,
            outputs=final_outputs,
        )

    def _execute_ct(
        self,
        ct_code: str,
        inputs: dict[str, Any],
        binding: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Execute a capability transform.

        SOVEREIGNTY: CT-IR must be pre-loaded and validated by compiler.
        Execution layer blindly executes validated IR.
        """
        on_ct_result = binding.get("on_ct_result", {})
        on_success = on_ct_result.get("on_success", "SUCCESS")
        on_failure = on_ct_result.get("on_failure", "VIOLATION")

        # Lookup pre-validated CT-IR (loaded by compiler)
        ct_ir = self._ct_ir_registry.get(ct_code)
        if not ct_ir:
            raise StructuredError(
                error_code="CT_IR_NOT_FOUND",
                node_category="CT",
                message=f"CT-IR not found in registry: {ct_code}. Compiler must pre-load and validate all CT-IR.",
            )

        try:
            # CT returns dict with outputs (e.g., {"value": "..."})
            # Add result_status to CT outputs, don't double-wrap
            logger.debug("_execute_ct: ct=%r inputs=%s", ct_code, inputs)
            ct_result = execute_ct(ct_ir=ct_ir, inputs=inputs)
            ct_result["result_status"] = on_success
            logger.debug("_execute_ct: ct=%r result=%s", ct_code, ct_result)
            return ct_result
        except CTExecutionError as e:
            # Expected CT constraint violation (e.g., quota exhausted, validation failed)
            logger.debug("CT protocol violation  ct=%s  type=%s  error=%s", ct_code, type(e).__name__, e)
            return {"value": None, "result_status": on_failure}
        except Exception as e:
            # UNEXPECTED ERROR - This indicates a runtime bug, not a protocol violation
            logger.error("Unexpected error in CT execution  ct=%s  inputs=%s", ct_code, inputs, exc_info=True)
            return {"value": None, "result_status": on_failure}

    def _execute_cs(
        self,
        cs_code: str,
        op: str,
        inputs: dict[str, Any],
        store: str | None = None,
    ) -> dict[str, Any]:
        """Execute a capability side effect."""
        # Add store metadata to payload for entity-based path resolution
        payload = dict(inputs)
        if store:
            payload["__pgs_store_entity__"] = store

        return self._router.execute(
            capability_code=cs_code,
            op=op,
            payload=payload,
        )

    @staticmethod
    def _build_result(
        cc_code: str,
        status: str,
        outputs: dict[str, Any],
        exited_at: str | None = None,
    ) -> dict[str, Any]:
        """Build final CC result."""
        result = {
            "node_code": cc_code,
            "result_status": status,
        }
        if exited_at:
            result["exited_at"] = exited_at

        # Remove result_status from outputs if present
        outputs = dict(outputs)
        outputs.pop("result_status", None)

        result.update(outputs)
        return result

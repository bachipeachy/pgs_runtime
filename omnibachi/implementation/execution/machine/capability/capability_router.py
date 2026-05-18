"""
capability_router.py — Routes capability execution to runtimes.

Governed by: CONSTITUTION_EXECUTION_V0

Routes capability side-effect operations to registered runtimes.
"""

from typing import Any

from omnibachi.implementation.execution.machine.errors import StructuredError


class CapabilityRouter:
    """
    Routes capability execution to registered runtimes.

    Responsibilities:
    - Route execution requests to registered runtimes
    - Enforce execution invariants

    Non-responsibilities:
    - Protocol interpretation
    - Result status invention
    - Payload mutation
    """

    def __init__(self, runtimes: dict[str, Any]):
        self._runtimes = runtimes

    def get_registered_capabilities(self) -> dict[str, dict]:
        """
        Get all registered capabilities for registry introspection.

        Returns dict mapping capability_code -> metadata.
        This is TRUTH - what's actually registered, not what we think should be.
        """
        capabilities = {}
        for code, runtime in self._runtimes.items():
            runtime_type = "transform" if code.startswith("CT_") or "::CT_" in code else "side_effect"
            capabilities[code] = {
                "type": runtime_type,
                "registered": True,
                "runtime_class": runtime.__class__.__name__ if runtime else None,
            }
        return capabilities

    def execute(
        self,
        capability_code: str,
        op: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Execute a capability operation.

        Raises:
            StructuredError: If no host registered or dispatch protocol violated
        """
        runtime = self._runtimes.get(capability_code)
        if runtime is None:
            raise StructuredError(
                error_code="CAPABILITY_DISPATCH_FAILED",
                node_category="CS",
                message=f"No host for capability: {capability_code}",
            )

        result = runtime.execute(op=op, payload=payload)

        if not isinstance(result, dict):
            raise StructuredError(
                error_code="CAPABILITY_DISPATCH_FAILED",
                node_category="CS",
                message=f"Runtime {capability_code} returned non-dict",
            )

        if "result_status" not in result:
            raise StructuredError(
                error_code="CAPABILITY_DISPATCH_FAILED",
                node_category="CS",
                message=f"Runtime {capability_code} missing result_status",
            )

        return result

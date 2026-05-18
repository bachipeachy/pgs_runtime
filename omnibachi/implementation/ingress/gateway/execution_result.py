"""
execution_result.py — Canonical response envelope for all transports.

Governed by: CONSTITUTION_EXECUTION_V0

No dependencies on execution layer. Pure data.
"""

from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class ExecutionResult:
    """
    Canonical response shape shared by CLI and HTTP.

    result_payload is sanitized:
    - No trace plumbing fields (node_code, result_status)
    - No internal execution metadata
    - No side-effect artifact paths
    """
    status: str                          # "SUCCESS" | "FAILED"
    exit_reason_code: str                # "COMPLETED" | "NO_TRANSITION" | ...
    trace_id: str                        # "T_20260217_..."
    duration_ms: int
    result_payload: dict                 # sanitized execution output
    workflow_code: str
    module: str
    domain: Optional[str] = None         # domain extracted from workflow source path
    error_code: Optional[str] = None     # from StructuredError if present
    message: Optional[str] = None        # human-readable

    def to_dict(self) -> dict:
        return asdict(self)

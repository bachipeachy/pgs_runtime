"""
execution_identity.py — Execution identity generation.

Governed by: CONSTITUTION_EXECUTION_V0

Trace identity is execution policy, not CLI behavior.
This module provides deterministic execution ID generation.
"""

import uuid
from datetime import datetime, timezone


def generate_execution_id() -> str:
    """
    Generate unique execution ID with timestamp prefix for sorting.

    Format: T_YYYYMMDD_HHMMSS_mmm_{UNIQUE}

    Includes milliseconds for proper sorting when multiple workflows
    run within the same second.
    """
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    millis = f"{now.microsecond // 1000:03d}"
    unique = uuid.uuid4().hex[:8].upper()
    return f"T_{timestamp}_{millis}_{unique}"

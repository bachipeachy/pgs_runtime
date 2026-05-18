"""
execution_policy_loader.py — Policy loader from governance.

Governed by: CONSTITUTION_EXECUTION_POLICY_V0

Loads policy profiles from governance. No policy logic in Python.
"""

from dataclasses import dataclass
from typing import Any
from enum import Enum


class TraceDepth(Enum):
    """Trace emission depth."""
    MINIMAL = "minimal"
    FULL = "full"


@dataclass(frozen=True)
class ExecutionPolicy:
    """
    Immutable execution policy profile.

    Loaded from governance, not constructed in code.
    """
    policy_profile: str
    trace_depth: TraceDepth
    replay_guarantee: bool
    audit_trail: bool
    encrypted_execution: bool
    aot_compilation: bool

    @property
    def is_advanced(self) -> bool:
        return self.policy_profile == "ADVANCED"

    @property
    def is_basic(self) -> bool:
        return self.policy_profile == "BASIC"


# Canonical policy profiles from CONSTITUTION_EXECUTION_POLICY_V0
BASIC_POLICY = ExecutionPolicy(
    policy_profile="BASIC",
    trace_depth=TraceDepth.MINIMAL,
    replay_guarantee=False,
    audit_trail=False,
    encrypted_execution=False,
    aot_compilation=False,
)

ADVANCED_POLICY = ExecutionPolicy(
    policy_profile="ADVANCED",
    trace_depth=TraceDepth.FULL,
    replay_guarantee=True,
    audit_trail=True,
    encrypted_execution=False,
    aot_compilation=False,
)


def load_policy(
    runtime_binding: dict[str, Any] | None = None,
    workflow: dict[str, Any] | None = None,
) -> ExecutionPolicy:
    """
    Load execution policy per CONSTITUTION_EXECUTION_POLICY_V0 §4.

    Resolution order:
    1. Explicit host binding (rb_code.policy_profile)
    2. Workflow declaration (wf_code.default_policy)
    3. System default (BASIC)
    """
    profile_name = None

    # 1. Check host binding
    if runtime_binding:
        profile_name = runtime_binding.get("policy_profile")

    # 2. Check workflow declaration
    if not profile_name and workflow:
        profile_name = workflow.get("default_policy")

    # 3. System default
    if not profile_name:
        profile_name = "BASIC"

    # Resolve profile
    if profile_name == "ADVANCED":
        return ADVANCED_POLICY
    else:
        return BASIC_POLICY

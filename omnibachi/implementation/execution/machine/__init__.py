"""
machine — Protocol-governed execution machine.

Governed by: CONSTITUTION_EXECUTION_V0

This module implements a protocol interpreter, not a behavior author.
All execution semantics derive from governance artifacts.
"""

from omnibachi.implementation.execution.machine.execution_context import ExecutionContext, ExitCondition
from omnibachi.implementation.execution.machine.dag_model import DAG, DAGNode, DAGEdge, build_dag_from_workflow
from omnibachi.implementation.execution.machine.execution_policy_loader import (
    load_policy,
    ExecutionPolicy,
    BASIC_POLICY,
    ADVANCED_POLICY,
    TraceDepth,
)
from omnibachi.implementation.execution.machine.trace_emitter import TraceEmitter, JsonlTraceSink
from omnibachi.implementation.execution.machine.execution_identity import generate_execution_id
from omnibachi.implementation.execution.machine.capability import (
    CapabilityRouter,
    CapabilityPipeline,
)

__all__ = [
    "ExecutionContext",
    "ExitCondition",
    "DAG",
    "DAGNode",
    "DAGEdge",
    "build_dag_from_workflow",
    "load_policy",
    "ExecutionPolicy",
    "BASIC_POLICY",
    "ADVANCED_POLICY",
    "TraceDepth",
    "TraceEmitter",
    "JsonlTraceSink",
    "generate_execution_id",
    "CapabilityRouter",
    "CapabilityPipeline",
]

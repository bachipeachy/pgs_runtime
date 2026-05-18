"""
execution — Protocol-governed execution engine.

Governed by: CONSTITUTION_EXECUTION_V0

This package implements a protocol interpreter.
All execution semantics derive from governance artifacts.
"""

from omnibachi.implementation.execution.machine import (
    ExecutionContext,
    ExitCondition,
    DAG,
    DAGNode,
    DAGEdge,
    build_dag_from_workflow,
    load_policy,
    ExecutionPolicy,
    BASIC_POLICY,
    ADVANCED_POLICY,
    TraceDepth,
    TraceEmitter,
    JsonlTraceSink,
    generate_execution_id,
    CapabilityRouter,
    CapabilityPipeline,
)
from omnibachi.implementation.execution.host import WorkflowRunner, RuntimeLoader

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
    "WorkflowRunner",
    "RuntimeLoader",
]

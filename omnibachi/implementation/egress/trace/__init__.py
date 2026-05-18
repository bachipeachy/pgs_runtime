"""
egress.trace — Trace materialization layer.

Governed by: CONSTITUTION_TRACE_EXECUTION_V0

Sole authority for writing trace events to persistent storage.
Execution is pure; egress owns all trace I/O.
"""

from omnibachi.implementation.egress.trace.trace_egress_adapter import TraceEgressAdapter

__all__ = ["TraceEgressAdapter"]

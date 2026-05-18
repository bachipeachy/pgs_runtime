"""
trace_egress_adapter.py — Trace materialization and Markdown projection.

Governed by: CONSTITUTION_TRACE_EXECUTION_V0

This is the SOLE authority for writing trace events to disk and for
rendering the Markdown projection (trace-only, no protocol artifacts).

PNG projection requires wf_artifact + trace_events — callers with that
context (e.g. workflow_gateway) invoke trace_png_renderer directly.

Principle: Trace is the contract. Rendering is a projection.

Contract:
    flush(events, path) → None
    - path MUST be absolute (STRUCTURE-resolved by caller)
    - Writes one JSON line per event (JSONL)
    - Renders .md projection post-write
    - Fails hard on any I/O error
"""

import json
from pathlib import Path

from omnibachi.implementation.execution.machine.trace_emitter import TraceEvent
from omnibachi.implementation.egress.trace.trace_md_renderer import render_md


class TraceEgressAdapter:
    """
    Materializes trace events to JSONL and renders Markdown projection.

    Governed by CONSTITUTION_TRACE_EXECUTION_V0 §4.
    """

    def flush(self, events: list[TraceEvent], path: Path) -> None:
        """
        Write trace events to JSONL, then render .md projection.

        PNG rendering (requires wf_artifact) is the caller's responsibility
        — see trace_png_renderer.render_png().

        Args:
            events: Ordered list of trace events from execution.
            path:   Absolute output path for the .jsonl trace file.

        Raises:
            ValueError: If path is not absolute (STRUCTURE violation).
            OSError:    If write fails (no silent failures).
        """
        if not path.is_absolute():
            raise ValueError(
                f"STRUCTURE VIOLATION: trace path must be absolute, got: {path}"
            )

        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

        render_md(path)

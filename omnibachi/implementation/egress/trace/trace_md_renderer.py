"""
trace_md_renderer.py — Markdown projection from trace JSONL.

Governed by: CONSTITUTION_TRACE_EXECUTION_V0

Principle: Trace is the contract. Rendering is a projection.
This renderer consumes only the written trace file — no protocol
artifacts, no DAG, no authoring dependencies.

Contract:
    render_md(trace_path: Path) -> Path
    - Reads completed .jsonl trace
    - Writes {execution_id}.md alongside the .jsonl
    - Returns output path
    - Fails hard on missing or unreadable trace
"""

import json
from pathlib import Path


def render_md(trace_path: Path) -> Path:
    """
    Project trace JSONL to human-readable Markdown.

    Args:
        trace_path: Absolute path to completed .jsonl trace file.

    Returns:
        Path to the written .md file (same directory as trace_path).

    Raises:
        ValueError:       If trace_path is not absolute.
        FileNotFoundError: If trace file does not exist.
    """
    if not trace_path.is_absolute():
        raise ValueError(f"trace_path must be absolute, got: {trace_path}")
    if not trace_path.exists():
        raise FileNotFoundError(f"Trace file not found: {trace_path}")

    events = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    exec_start    = next((e for e in events if e.get("event_type") == "execution_start"), None)
    exec_complete = next((e for e in events if e.get("event_type") == "workflow_complete"), None)
    violations    = [e for e in events if e.get("event_type") == "violation"]
    errors        = [e for e in events if e.get("event_type") == "error"]
    node_ends     = [e for e in events if e.get("event_type") == "node_end"]

    workflow_code = exec_start["payload"].get("workflow_code", "UNKNOWN") if exec_start else "UNKNOWN"
    status        = exec_complete["payload"].get("status", "UNKNOWN")     if exec_complete else "UNKNOWN"
    duration_ms   = exec_complete["payload"].get("duration_ms", 0)        if exec_complete else 0
    exit_reason   = exec_complete["payload"].get("exit_reason_code", "")  if exec_complete else ""

    if violations:
        first_v_node = violations[0].get("payload", {}).get("node_id", "")
        exec_phase   = "ADMISSION" if first_v_node == "ADMISSION" else "MID-WORKFLOW"
    else:
        exec_phase = "FULL DAG"

    nodes_executed = len([n for n in node_ends if n.get("payload", {}).get("status") == "SUCCESS"])

    md = [
        "# Execution Trace",
        "",
        f"**Workflow**: `{workflow_code}`",
        f"**Execution ID**: `{trace_path.stem}`",
        "",
        "## Summary",
        "",
        f"- **Status**: {status}",
        f"- **Duration**: {duration_ms} ms",
        f"- **{'Failure' if violations else 'Execution'} Phase**: {exec_phase}",
    ]
    if nodes_executed > 0:
        md.append(f"- **Nodes Executed**: {nodes_executed}")
    if exit_reason:
        md.append(f"- **Exit Reason**: {exit_reason}")
    md.append("")

    if violations:
        first_v   = violations[0]
        v_payload = first_v.get("payload", {})
        failure_node   = v_payload.get("node_id", "UNKNOWN")
        failure_reason = v_payload.get("constraint") or v_payload.get("message", "")
        impact = (
            "Workflow did not enter execution DAG"
            if exec_phase == "ADMISSION"
            else f"Workflow halted at {failure_node}"
        )
        md += [
            "## Failure Point",
            "",
            f"- **Stage**: {failure_node}",
            f"- **Reason**: {failure_reason}",
            f"- **Impact**: {impact}",
            "",
        ]

    if violations:
        md += ["## Protocol Violations", ""]
        for v in violations:
            p = v.get("payload", {})
            md += [
                f"### {p.get('node_id')}",
                "",
                f"- **Code**: `{p.get('violation_code')}`",
                f"- **Message**: {p.get('message')}",
            ]
            if p.get("field"):
                md.append(f"- **Field**: `{p.get('field')}`")
            if p.get("constraint"):
                md.append(f"- **Constraint**: {p.get('constraint')}")
            if p.get("actual_value") is not None:
                md.append(f"- **Actual**: `{p.get('actual_value')}`")
            if p.get("expected_value") is not None:
                md.append(f"- **Expected**: `{p.get('expected_value')}`")
            md.append("")

    if errors:
        md += ["## Errors", ""]
        for err in errors:
            p = err.get("payload", {})
            md += [
                f"- **Type**: `{p.get('error_type')}`",
                f"- **Origin**: `{p.get('component')}`",
            ]
            if p.get("traceback"):
                md += ["", "```", p.get("traceback"), "```"]
            md.append("")

    md += ["## Execution Flow", ""]
    seq = 0
    for event in events:
        et = event.get("event_type")
        p  = event.get("payload", {})
        if et == "node_start":
            continue
        seq += 1
        if et == "execution_start":
            md.append(f"{seq}. START → {p.get('workflow_code')}")
        elif et == "node_end":
            md.append(f"{seq}. {p.get('node_id')} → {p.get('status')} ({p.get('duration_ms')}ms)")
        elif et == "capability_dispatch":
            md.append(f"{seq}. DISPATCH → {p.get('cc_code')}")
        elif et == "violation":
            md.append(f"{seq}. VIOLATION → {p.get('violation_code')}")
        elif et == "error":
            md.append(f"{seq}. ERROR → {p.get('error_type')}")
        elif et == "workflow_complete":
            md.append(f"{seq}. EXIT → {p.get('status')}")
        else:
            md.append(f"{seq}. {et}")

    output_path = trace_path.with_suffix(".md")
    output_path.write_text("\n".join(md), encoding="utf-8")
    return output_path

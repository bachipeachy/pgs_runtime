"""
trace_png_renderer.py — Workflow DAG + trace overlay PNG projection.

Governed by: CONSTITUTION_TRACE_EXECUTION_V0

Principle: Trace is the contract. Rendering is a projection.
Renders the full protocol DAG (all possible paths) with the executed
path highlighted in red.

Contract:
    render_png(wf_artifact, trace_events, png_path) -> Path
    - wf_artifact: loaded workflow JSON artifact (frontmatter + namespace)
    - trace_events: list of event dicts from the completed execution
    - png_path:     absolute path for the output .png
    - Fails hard if graphviz not installed or wf_artifact is malformed
"""

import subprocess
from pathlib import Path
from typing import Any

from omnibachi.implementation.execution.machine.dag_model import DAG, build_dag_from_workflow


# ── DOT generation ───────────────────────────────────────────────────────────


def _workflow_to_dot(dag: DAG, trace_events: list[dict[str, Any]] | None = None) -> str:
    """
    Generate DOT representation of workflow DAG.

    Visited nodes and traversed edges (from trace) are highlighted red.
    All protocol-declared paths are shown regardless of execution.
    """
    visited_nodes: set[str] = set()
    traversed_edges: set[tuple[str, str, str]] = set()

    if trace_events:
        last_node_id = None
        last_status  = None

        for event in trace_events:
            et      = event.get("event_type") or event.get("event")
            payload = event.get("payload", event)
            node_id = payload.get("node_id", "")

            if et == "node_start":
                visited_nodes.add(node_id)
                if last_node_id and last_status:
                    traversed_edges.add((last_node_id, node_id, last_status))
                last_node_id = None
                last_status  = None

            elif et == "node_end":
                last_node_id = node_id
                last_status  = payload.get("status", "")

    lines = [
        f'digraph "{dag.dag_id}" {{',
        "  rankdir=LR;",
        '  node [fontname="Helvetica"];',
        '  edge [fontname="Helvetica", fontsize=10];',
    ]

    for node in dag.nodes.values():
        label = f"{node.node_id}\\n[{node.node_type}]"
        if node.capability_code and node.capability_code != node.node_id:
            label += f"\\n{node.capability_code}"

        shape = "box"
        if node.node_id in dag.terminal_nodes:
            shape = "doublecircle"
        elif node.node_type == "intent":
            shape = "hexagon"

        style, color, fillcolor = "", "black", "white"
        if node.node_id in visited_nodes:
            style, color, fillcolor = "filled", "red", "#ffcccc"

        lines.append(
            f'  "{node.node_id}" [label="{label}", shape={shape}, '
            f'style="{style}", color="{color}", fillcolor="{fillcolor}"];'
        )

    for edge in dag.edges:
        condition    = edge.condition or ""
        is_traversed = (edge.from_node, edge.to_node, condition) in traversed_edges
        color    = "red" if is_traversed else "black"
        penwidth = 3     if is_traversed else 1

        lines.append(
            f'  "{edge.from_node}" -> "{edge.to_node}" '
            f'[label="{condition}", color="{color}", penwidth={penwidth}];'
        )

    lines.append("}")
    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────


def render_png(
    wf_artifact:   dict[str, Any],
    trace_events:  list[dict[str, Any]],
    png_path:      Path,
) -> Path:
    """
    Render workflow DAG with trace overlay to PNG.

    Args:
        wf_artifact:  Loaded workflow JSON artifact (must contain frontmatter.core).
        trace_events: Ordered event dicts from the completed execution.
        png_path:     Absolute output path for the .png file.

    Returns:
        png_path (written).

    Raises:
        ValueError:    If png_path is not absolute.
        RuntimeError:  If graphviz 'dot' is not installed or rendering fails.
    """
    if not png_path.is_absolute():
        raise ValueError(f"png_path must be absolute, got: {png_path}")

    dag = build_dag_from_workflow(wf_artifact)
    dot_content = _workflow_to_dot(dag, trace_events)

    png_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        subprocess.run(
            ["dot", "-Tpng", "-o", str(png_path)],
            input=dot_content.encode("utf-8"),
            check=True,
            capture_output=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "Graphviz 'dot' not found. Install: https://graphviz.org/download/"
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Graphviz rendering failed: {e.stderr.decode()}")

    return png_path

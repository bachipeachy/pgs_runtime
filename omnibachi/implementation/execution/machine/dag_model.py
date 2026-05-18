"""
dag_model.py — Pure DAG data structure.

Governed by: SCHEMA_EXECUTION_DAG_V0

This module contains only data structures. No behavior.
Schema validation happens at construction time.
"""

from dataclasses import dataclass, field
from typing import Any

from omnibachi.implementation.execution.machine.errors import StructuredError


@dataclass(frozen=True)
class DAGNode:
    """Immutable node in execution DAG."""
    node_id: str
    node_type: str  # "intent", "capability_contract", or "exit"
    capability_code: str
    input_bindings: dict[str, Any] = field(default_factory=dict)
    output_bindings: dict[str, str] = field(default_factory=dict)
    exit_reason: str | None = None  # Only for exit nodes: "COMPLETED", "EXITED", "FAILED"


@dataclass(frozen=True)
class DAGEdge:
    """Immutable edge in execution DAG."""
    from_node: str
    to_node: str
    condition: str | None = None  # result_status that triggers this edge


@dataclass(frozen=True)
class DAG:
    """
    Immutable DAG structure for workflow execution.

    Constructed from workflow artifact via SCHEMA_EXECUTION_DAG_V0.
    """
    dag_id: str
    workflow_code: str
    nodes: dict[str, DAGNode]
    edges: list[DAGEdge]
    entry_nodes: list[str]
    terminal_nodes: list[str]

    def get_node(self, node_id: str) -> DAGNode | None:
        return self.nodes.get(node_id)

    def get_successors(self, node_id: str) -> list[str]:
        return [e.to_node for e in self.edges if e.from_node == node_id]

    def get_predecessors(self, node_id: str) -> list[str]:
        return [e.from_node for e in self.edges if e.to_node == node_id]

    def is_terminal(self, node_id: str) -> bool:
        return node_id in self.terminal_nodes

    def get_next_node(self, node_id: str, result_status: str) -> str | None:
        for edge in self.edges:
            if edge.from_node == node_id and edge.condition == result_status:
                return edge.to_node
        return None


def build_dag_from_workflow(workflow: dict[str, Any]) -> DAG:
    """
    Build DAG from workflow artifact.

    PROTOCOL: Expects compiled artifact format:
    - frontmatter.wf_code: workflow identifier
    - frontmatter.core.nodes: dict of node_id -> {type, code, next, inputs}
    - frontmatter.core.start_node: entry point

    No fallbacks - artifact must be compiled.
    """
    # PROTOCOL: Compiled artifacts have protocol data in frontmatter
    frontmatter = workflow.get("frontmatter", {})
    wf_code = frontmatter.get("wf_code", "")
    core = frontmatter.get("core", {})

    nodes_dict = core.get("nodes", {})
    start_node = core.get("start_node", "")

    # Build nodes
    nodes: dict[str, DAGNode] = {}
    edges: list[DAGEdge] = []
    terminal_nodes: list[str] = []

    for node_id, node_spec in nodes_dict.items():
        node_type_raw = node_spec.get("type", "CC")
        code = node_spec.get("fqdn_id") or node_spec.get("code", "")
        inputs = node_spec.get("inputs", {})

        # Map type to node_type
        if node_type_raw == "IN":
            node_type = "intent"
        elif node_type_raw == "TI":
            node_type = "transport_ingress"
        elif node_type_raw == "CC":
            node_type = "capability_contract"
        elif node_type_raw == "EXIT":
            node_type = "exit"
            terminal_nodes.append(node_id)
        else:
            raise StructuredError(
                error_code="SCHEMA_VIOLATION",
                node_category="WF",
                message=f"Unknown node type '{node_type_raw}' for node '{node_id}'",
            )

        # Extract exit_reason for EXIT nodes
        exit_reason = node_spec.get("reason") if node_type == "exit" else None

        node = DAGNode(
            node_id=node_id,
            node_type=node_type,
            capability_code=code,
            input_bindings=inputs,
            exit_reason=exit_reason,
        )
        nodes[node.node_id] = node

        # Build edges from 'next' transitions
        next_map = node_spec.get("next", {})
        for result_status, target_node in next_map.items():
            edges.append(DAGEdge(
                from_node=node_id,
                to_node=target_node,
                condition=result_status,
            ))

    # Entry nodes
    entry_nodes = [start_node] if start_node else []

    return DAG(
        dag_id=f"DAG_{wf_code}",
        workflow_code=wf_code,
        nodes=nodes,
        edges=edges,
        entry_nodes=entry_nodes,
        terminal_nodes=terminal_nodes,
    )

"""
scheduler.py — WF-level topology driver for the token-native runtime.

Traverses the compiled execution topology for a single workflow invocation.
Drives CC execution in declared order, resolves WF-level input bindings from
the ExecutionContext, routes between nodes on result status, and emits
WF-level trace events.

The scheduler is a blind executor:
    - All routing is read from dispatch.routing (compiled by S2/S3)
    - All CC input bindings are read from dispatch.bindings (compiled by S6)
    - Condition resolution uses the vocab (transition:: / outcome:: addresses)
    - No domain logic, no semantic inference, no path construction

Topology traversal rules:
    - Entry point is dispatch.entry[wf_addr]["start"]
    - Each CC produces a result_status; that status resolves to a condition address
    - The condition address is looked up in dispatch.routing[wf_addr][cc_addr] → next node
    - Traversal ends when no routing entry exists for the current (cc_addr, condition)

Boundary nodes (IN_, EXIT_):
    Nodes without a pipeline entry (not in dispatch.pipeline) are boundary nodes.
    IN_ nodes perform admission gating; for v0.3.0, prior to admission_snapshot
    integration, they pass through as ACK. The routing table routes ACK forward.
    EXIT_ nodes (no routing) terminate the loop naturally.

Bindings path grammar (WF-level, compiler-emitted):
    $.payload.<field>          — from the original payload
    $.inputs.<field>           — alias for $.payload.<field>
    $.results.<cc_addr>.<field>— from a prior CC's result surface (int cc_addr)
    <literal>                  — returned as-is

Result:
    (result_status, surface) from the last CC executed.
    result_status: the WF terminal outcome string (e.g. "SUCCESS", "VIOLATION")
    surface: the last CC's output dict (for transport/egress use)
"""

from __future__ import annotations

from typing import Any

from pgs_runtime.dispatcher import execute_cc
from pgs_runtime.evidence import TraceWriter
from pgs_runtime.loader import RuntimePackage
from pgs_runtime.memory import ExecutionContext

# Guard against pathological graphs (cycles, runaway traversal)
_MAX_HOPS = 64


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_wf(
    wf_fqdn:   str,
    payload:   dict[str, Any],
    pkg:       RuntimePackage,
    writer:    TraceWriter,
    data_root: str,
) -> tuple[str, dict[str, Any]]:
    """
    Execute a workflow topology and return (result_status, surface).

    Args:
        wf_fqdn:   Fully-qualified name of the workflow (e.g. "blockchain::WF_...").
        payload:   Inbound payload dict (already normalized by transport layer).
        pkg:       Frozen RuntimePackage (loader output for this domain).
        writer:    TraceWriter for this execution trace.
        data_root: Absolute data directory root for CS path expansion.

    Returns:
        (result_status, surface) where:
            result_status — terminal WF outcome (e.g. "SUCCESS", "VIOLATION")
            surface       — last CC output dict (passed to transport egress)

    Raises:
        KeyError:    WF FQDN not in vocab or entry table.
        RuntimeError: Hop limit exceeded (indicates a compiler-emitted cycle).
    """
    wf_addr = pkg.vocab.addr(wf_fqdn)

    entry = pkg.dispatch.entry.get(wf_addr)
    if entry is None:
        raise RuntimeError(
            f"No entry point for WF {wf_fqdn!r} (addr {wf_addr}) — "
            f"snapshot may be stale or domain mismatch"
        )

    rb_addr = entry["rb"]
    current_addr: int | None = entry["start"]

    ctx = ExecutionContext(payload)
    writer.wf_start(payload)

    result_status = "SUCCESS"
    surface: dict[str, Any] = {}
    hops = 0

    while current_addr is not None:
        if hops >= _MAX_HOPS:
            raise RuntimeError(
                f"WF {wf_fqdn!r} exceeded {_MAX_HOPS} topology hops — "
                f"possible cycle in compiled routing"
            )
        hops += 1

        if current_addr in pkg.dispatch.pipeline:
            # CC node — resolve WF-level bindings and execute
            wf_bindings = (
                pkg.dispatch.bindings
                .get(wf_addr, {})
                .get(current_addr, {})
            )
            cc_inputs = ctx.resolve_inputs(wf_bindings)

            result_status, surface = execute_cc(
                current_addr, rb_addr, cc_inputs, pkg, writer, data_root
            )
            ctx.record_result(current_addr, surface)

        else:
            # Boundary node (IN_, EXIT_) — no pipeline
            # v0.3.0: admission_snapshot not yet integrated; IN_ nodes pass as ACK
            result_status = "ACK"

        # Resolve result_status → condition address and route to next node
        condition_addr = _condition_addr(result_status, pkg)
        routing = pkg.dispatch.routing.get(wf_addr, {}).get(current_addr, {})
        current_addr = routing.get(condition_addr)  # None → terminal

    writer.wf_complete(result_status)
    return result_status, surface


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _condition_addr(result_status: str, pkg: RuntimePackage) -> int:
    """
    Resolve a result_status string to its transition address integer.

    Lookup order:
        1. transition::<result_status>  (primary — WF routing namespace)
        2. outcome::<result_status>     (fallback — CC outcome namespace)

    Returns -1 if the status has no registered address (no routing will match).
    """
    try:
        return pkg.vocab.addr(f"transition::{result_status}")
    except KeyError:
        pass
    try:
        return pkg.vocab.addr(f"outcome::{result_status}")
    except KeyError:
        pass
    return -1

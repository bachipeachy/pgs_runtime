"""
cli_adapter.py — CLI entry point for workflow execution.

Governed by: CONSTITUTION_EXECUTION_V0

This adapter maps CLI arguments to the workflow gateway.
It is responsible for:
1. Parsing CLI arguments
2. Loading the initial payload
3. Invoking the gateway (execute_workflow)
4. Printing the result summary

Invariant: CLI logic must not implement workflow behavior.
All execution logic resides in the gateway and runner.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from omnibachi.implementation.ingress.gateway.workflow_gateway import execute_workflow
from omnibachi.implementation.ingress.gateway.execution_result import ExecutionResult


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PGS Workflow CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── Command: run ──────────────────────────────────────────
    run_parser = subparsers.add_parser("run", help="Execute a workflow or intent")

    target_group = run_parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--wf", help="Workflow FQDN (e.g. blockchain::WF_CREATE_WALLET_V0)")
    target_group.add_argument("--intent", help="Intent FQDN (e.g. blockchain::IN_WALLET_CREATED_V0)")

    run_parser.add_argument("--payload", help="Path to payload JSON file", required=False)
    run_parser.add_argument("--rb", help="Runtime binding FQDN (optional override)")
    run_parser.add_argument(
        "--mode",
        choices=["runtime", "authoring"],
        default="authoring",
        help="Execution mode (default: authoring)"
    )
    run_parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable DEBUG logging for execution tracing",
    )
    run_parser.add_argument(
        "--data-root",
        dest="data_root",
        default=None,
        help="Absolute path for CS domain data (or set PGS_DATA_ROOT env var)",
    )
    run_parser.add_argument(
        "--workspace",
        dest="workspace",
        default=None,
        help="Absolute path to pgs_workspace root (or set PGS_WORKSPACE env var); traces go to {workspace}/traces/",
    )

    # ── Command: examine ──────────────────────────────────────
    examine_parser = subparsers.add_parser(
        "examine",
        help="Analyze a completed trace file and print a diagnostic report",
    )
    examine_parser.add_argument(
        "trace_file",
        help="Path to a completed .jsonl trace file",
    )

    return parser.parse_args()


def load_payload(payload_path: str | None) -> dict:
    if not payload_path:
        return {}
    path = Path(payload_path)
    if not path.exists():
        fatal(f"Payload file not found: {payload_path}")
    return json.loads(path.read_text())


def fatal(message: str) -> None:
    print(f"[cli] Error: {message}", file=sys.stderr)
    sys.exit(1)


def print_summary(result: ExecutionResult) -> None:
    print("=" * 60)
    print("[cli] PGS :: Workflow Complete")
    print("=" * 60)
    print(f"Workflow:        {result.workflow_code}")
    print(f"Status:          {result.status}")
    print(f"Exit Reason:     {result.exit_reason_code}")
    print(f"Duration:        {result.duration_ms}ms")
    print(f"Trace ID:        {result.trace_id}")
    if result.message:
        print(f"Message:         {result.message}")
    print("=" * 60 + "\n")


def handle_examine(trace_file: str) -> None:
    """
    Examine command handler.

    Reads a completed trace file, runs the trace examiner, and prints the
    diagnostic report. Imports omnibachi.evidence only here — zero dependency
    from the execution path.

    Exits 1 on structural failure (bug in protocol wiring).
    Exits 0 on clean execution or business violation (expected denial path).
    """
    try:
        from omnibachi.implementation.evidence import analyze, TraceParseError
    except ImportError:
        fatal(
            "Trace examiner not found — omnibachi may not be installed correctly.\n"
            "  Install it with: pip install -e /path/to/pgs_runtime"
        )

    trace_path = Path(trace_file)
    if not trace_path.exists():
        fatal(f"Trace file not found: {trace_file}")

    try:
        report = analyze(trace_path)
    except TraceParseError as e:
        fatal(f"Trace parse error: {e}")

    print(report.format())

    if report.has_structural_failure:
        sys.exit(1)


def main() -> None:
    args = parse_args()

    if args.command == "examine":
        handle_examine(args.trace_file)
        return

    if args.command != "run":
        return

    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(name)s [%(levelname)s] %(message)s",
        )

    workflow_code = args.wf
    intent_code = args.intent

    print(f"[cli] Target: {workflow_code or intent_code}")

    rb_code = args.rb if args.rb else None
    if rb_code:
        print(f"[cli] Runtime binding override: {rb_code}")

    # Resolve data_root (CS domain state) — per CONSTITUTION_TRACE_EXECUTION_V0 §5
    data_root_str = args.data_root or os.environ.get("PGS_DATA_ROOT")
    if not data_root_str:
        fatal("data_root is required: pass --data-root PATH or set PGS_DATA_ROOT env var")
    data_root = Path(data_root_str)
    if not data_root.is_absolute():
        fatal(f"data_root must be an absolute path, got: {data_root_str}")

    # Resolve workspace — snapshot, traces all live under {workspace}/
    workspace_str = args.workspace or os.environ.get("PGS_WORKSPACE")
    if not workspace_str:
        fatal("workspace is required: pass --workspace PATH or set PGS_WORKSPACE env var")
    workspace = Path(workspace_str)
    if not workspace.is_absolute():
        fatal(f"workspace must be an absolute path, got: {workspace_str}")

    snapshot_root = workspace / "protocol_snapshot"
    trace_root    = workspace / "traces"

    # Pre-flight: snapshot must be marked VALID by pgs build before execution
    status_file = workspace / "snapshot_status.json"
    if not status_file.exists():
        fatal(
            "Snapshot has not been validated.\n"
            "  Run: python scripts/pgs_build.py --workspace <path>"
        )
    try:
        snapshot_status = json.loads(status_file.read_text())
    except json.JSONDecodeError as e:
        fatal(f"snapshot_status.json is malformed: {e}")
    if snapshot_status.get("status") != "VALID":
        fatal(
            f"Snapshot status is '{snapshot_status.get('status')}' — expected VALID.\n"
            "  Re-run: python scripts/pgs_build.py --workspace <path>"
        )

    payload = load_payload(args.payload)

    print(f"[cli] Mode:          {args.mode}")
    print(f"[cli] Data root:     {data_root}")
    print(f"[cli] Snapshot root: {snapshot_root}")
    print(f"[cli] Trace root:    {trace_root}")
    print("\n[cli] Executing workflow...\n")

    result, _ = execute_workflow(
        workflow_code=workflow_code,
        intent_code=intent_code,
        payload=payload,
        runtime_binding=rb_code,
        snapshot_root=snapshot_root,
        data_root=data_root,
        trace_root=trace_root,
        mode=args.mode,
    )

    print_summary(result)

    if result.status != "SUCCESS":
        sys.exit(1)


if __name__ == "__main__":
    main()

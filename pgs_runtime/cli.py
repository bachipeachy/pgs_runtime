"""
cli.py — Token-native CLI entry point for the pgs_runtime.

Commands:
    run     — Execute a workflow against the tokenized snapshot.
    examine — Analyze a completed trace file and print a diagnostic report.

Execution path (run):
    1. Load tokenized snapshot for the domain via loader.load_domain()
       — verifies topology hash against trust attestation; fails hard on mismatch
    2. Generate deterministic trace ID from (domain, wf_fqdn, payload)
    3. Open TraceWriter at traces/<domain>/<trace_id>/
    4. Drive workflow topology via scheduler.run_wf()
    5. Print result summary; exit 1 on non-SUCCESS

All runtime behavior comes from the compiled tokenized_snapshot.
The CLI does not implement any domain logic.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from pgs_runtime.evidence import TraceWriter, make_trace_id
from pgs_runtime.loader import load_domain
from pgs_runtime.scheduler import run_wf


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pgs_runtime",
        description="PGS token-native workflow runtime",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    # ── run ──────────────────────────────────────────────────────
    run_p = subs.add_parser("run", help="Execute a workflow")

    run_p.add_argument(
        "--wf",
        required=True,
        metavar="FQDN",
        help="Workflow FQDN (e.g. blockchain::WF_REGISTER_ACTOR_UNVERIFIED_V0)",
    )
    run_p.add_argument(
        "--payload",
        metavar="FILE",
        help="Path to JSON payload file (omit for empty payload)",
    )
    run_p.add_argument(
        "--data-root",
        dest="data_root",
        metavar="PATH",
        help="Absolute path for CS domain data root (or set PGS_DATA_ROOT)",
    )
    run_p.add_argument(
        "--workspace",
        metavar="PATH",
        help="Absolute path to pgs_workspace root (or set PGS_WORKSPACE); "
             "tokenized_snapshot/ and traces/ live here",
    )

    # ── examine ───────────────────────────────────────────────────
    ex_p = subs.add_parser("examine", help="Analyze a completed trace file")
    ex_p.add_argument(
        "trace_file",
        metavar="FILE",
        help="Path to a completed .jsonl trace file",
    )

    return parser


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _handle_run(args: argparse.Namespace) -> None:
    wf_fqdn = args.wf

    # Extract domain from FQDN (part before ::)
    if "::" not in wf_fqdn:
        _fatal(f"Invalid WF FQDN (expected <domain>::<CODE>): {wf_fqdn!r}")
    domain = wf_fqdn.split("::")[0]

    # Resolve paths from args or environment
    data_root_str = args.data_root or os.environ.get("PGS_DATA_ROOT")
    if not data_root_str:
        _fatal("--data-root PATH or PGS_DATA_ROOT is required")
    data_root = Path(data_root_str)
    if not data_root.is_absolute():
        _fatal(f"--data-root must be an absolute path, got: {data_root_str}")

    workspace_str = args.workspace or os.environ.get("PGS_WORKSPACE")
    if not workspace_str:
        _fatal("--workspace PATH or PGS_WORKSPACE is required")
    workspace = Path(workspace_str)
    if not workspace.is_absolute():
        _fatal(f"--workspace must be an absolute path, got: {workspace_str}")

    # Load payload
    payload = _load_payload(args.payload)

    # Load tokenized snapshot (verifies hash; raises on mismatch or missing files)
    print(f"[pgs_runtime] Loading {domain} snapshot...")
    try:
        pkg = load_domain(workspace, domain)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        _fatal(str(exc))

    # Generate trace ID and resolve trace directory
    # Structure: traces/<domain>/<WF_CODE>/<trace_id>/
    trace_id = make_trace_id(domain, wf_fqdn, payload)
    wf_code = wf_fqdn.split("::")[-1]  # e.g. "WF_CREATE_WALLET_V0"
    trace_dir = workspace / "traces" / domain / wf_code / trace_id
    trace_dir.mkdir(parents=True, exist_ok=True)

    print(f"[pgs_runtime] Workflow:  {wf_fqdn}")
    print(f"[pgs_runtime] Trace ID:  {trace_id}")
    print(f"[pgs_runtime] Trace dir: {trace_dir}")
    print()

    # Resolve WF address for TraceWriter
    try:
        wf_addr = pkg.vocab.addr(wf_fqdn)
    except KeyError:
        _fatal(f"WF FQDN not in vocab: {wf_fqdn!r}")

    writer = TraceWriter(
        trace_dir = trace_dir,
        trace_id  = trace_id,
        domain    = domain,
        wf_addr   = wf_addr,
        wf_fqdn   = wf_fqdn,
    )

    t0 = time.monotonic()
    try:
        result_status, surface = run_wf(
            wf_fqdn   = wf_fqdn,
            payload   = payload,
            pkg       = pkg,
            writer    = writer,
            data_root = str(data_root),
        )
    except Exception as exc:
        writer.error(str(exc))
        writer.close()
        _fatal(f"Runtime error: {exc}")
    finally:
        writer.close()

    duration_ms = int((time.monotonic() - t0) * 1000)

    print("=" * 60)
    print("[pgs_runtime] Workflow Complete")
    print("=" * 60)
    print(f"Workflow:   {wf_fqdn}")
    print(f"Status:     {result_status}")
    print(f"Trace ID:   {trace_id}")
    print(f"Duration:   {duration_ms}ms")
    if surface:
        print(f"Output:     {json.dumps(surface, separators=(',', ':'))}")
    print("=" * 60)

    if result_status not in ("SUCCESS", "ALREADY_EXISTS"):
        sys.exit(1)


def _handle_examine(args: argparse.Namespace) -> None:
    trace_path = Path(args.trace_file)
    if not trace_path.exists():
        _fatal(f"Trace file not found: {args.trace_file}")

    # Delegate to the examine module (reads JSONL trace format)
    try:
        from pgs_runtime.examine import analyze, TraceParseError
    except ImportError:
        _fatal(
            "Trace examiner unavailable — pgs_runtime may not be fully installed.\n"
            "  Re-install with: pip install -e /path/to/pgs_runtime"
        )

    try:
        report = analyze(trace_path)
    except Exception as exc:
        _fatal(f"Trace parse error: {exc}")

    print(report.format())

    if report.has_structural_failure:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _load_payload(payload_path: str | None) -> dict:
    if not payload_path:
        return {}
    path = Path(payload_path)
    if not path.exists():
        _fatal(f"Payload file not found: {payload_path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fatal(f"Payload file is not valid JSON: {exc}")


def _fatal(message: str) -> None:
    print(f"[pgs_runtime] Error: {message}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "run":
        _handle_run(args)
    elif args.command == "examine":
        _handle_examine(args)


if __name__ == "__main__":
    main()

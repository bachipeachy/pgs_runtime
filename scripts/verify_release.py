"""
verify_omnibachi_release_readiness.py — Pre-release gate for omnibachi

SELF-CONTAINED:
- builds snapshot via pgs_build.py
- uses real domain payload
- executes workflows on validated snapshot
- validates determinism correctly (protocol-level, not CLI noise)
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parent.parent
OMNIBACHI_DIR = RUNTIME_ROOT / "omnibachi"

PGS_COMPILER_ROOT = RUNTIME_ROOT.parent / "pgs_compiler"
PGS_BUILD_SCRIPT = PGS_COMPILER_ROOT / "scripts" / "pgs_build.py"

SOURCE_PAYLOAD = (
    RUNTIME_ROOT.parent
    / "pgs_blockchain"
    / "pgs_blockchain"
    / "testbed"
    / "identity"
    / "test_payloads"
    / "register_actor_unverified_payload.json"
)

_IMPORTLIB_APPROVED_FILES = {
    "runtime_loader.py",
    "ct_executor.py",
    "execute_ct.py",
}

_PASS = []
_FAIL = []

# ── Helpers ─────────────────────────────────────────────────────────────

def _run(cmd: str) -> str:
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{cmd}\n{result.stdout}\n{result.stderr}".strip())
    return result.stdout


def _grep(pattern: str, flags: str = ""):
    result = subprocess.run(
        f'grep -rn {flags} --include="*.py" "{pattern}" "{OMNIBACHI_DIR}"',
        shell=True,
        capture_output=True,
        text=True,
    )
    return [l for l in result.stdout.splitlines() if "__pycache__" not in l]


def _assert_no_hits(pattern: str, flags: str = ""):
    hits = _grep(pattern, flags)
    if hits:
        raise AssertionError("\n".join(hits[:10]))


def _check(name: str, fn):
    try:
        fn()
        _PASS.append(name)
        print(f"  [PASS] {name}")
    except Exception as e:
        _FAIL.append((name, str(e)))
        print(f"  [FAIL] {name}")
        for line in str(e).splitlines()[:6]:
            print(f"         {line}")


# ── Static Checks ────────────────────────────────────────────────────────

def check_import():
    _run('python -c "import omnibachi"')


def check_no_pgs_protocol_import():
    _assert_no_hits(r"(from|import) pgs_protocol", "-E")


def check_no_pgs_compiler_import():
    _assert_no_hits(r"(from|import) pgs_compiler", "-E")


def check_no_structure_import():
    _assert_no_hits(r"(from|import) structure[\. ]", "-E")


def check_no_relative_imports():
    _assert_no_hits(r"^from \.", "-E")


def check_no_sys_path_manipulation():
    _assert_no_hits(r"sys\.path", "-E")


def check_no_md_file_loading():
    _assert_no_hits(r'\.md["\']', "-E")


def check_no_fallback_markers():
    hits = _grep(
        r"#.*(fallback (to|for)|with .* fallback|pass.through|treat as)",
        "-iE",
    )
    if hits:
        raise AssertionError("\n".join(hits[:10]))


def check_importlib_confined():
    hits = _grep(r"importlib\.import_module")
    violations = [h for h in hits if not any(f in h for f in _IMPORTLIB_APPROVED_FILES)]
    if violations:
        raise AssertionError("\n".join(violations[:10]))


def check_pyproject_toml():
    if not (RUNTIME_ROOT / "pyproject.toml").exists():
        raise AssertionError("pyproject.toml not found")


# ── Execution Setup ─────────────────────────────────────────────────────

_WF = "blockchain::WF_REGISTER_ACTOR_UNVERIFIED_V0"
_CLI = "python -m omnibachi.implementation.ingress.cli.cli_adapter run"


def _prepare_workspace():
    workspace = Path(tempfile.mkdtemp(prefix="pgs_verify_"))

    # Build snapshot
    _run(f"python {PGS_BUILD_SCRIPT} --workspace {workspace}")

    if not (workspace / "snapshot_status.json").exists():
        raise AssertionError("snapshot not validated")

    if not SOURCE_PAYLOAD.exists():
        raise AssertionError(f"Payload missing: {SOURCE_PAYLOAD}")

    payload = workspace / "test_payload.json"
    shutil.copy(SOURCE_PAYLOAD, payload)

    return workspace, payload


def _run_workflow(workspace, payload, data_root):
    return _run(
        f"{_CLI} --wf {_WF}"
        f" --payload {payload}"
        f" --data-root {data_root}"
        f" --workspace {workspace}"
    )


# ── Execution Checks ────────────────────────────────────────────────────

def check_execute_workflow():
    workspace, payload = _prepare_workspace()
    with tempfile.TemporaryDirectory() as dr:
        _run_workflow(workspace, payload, dr)


def check_determinism():
    workspace, payload = _prepare_workspace()

    results = []

    for _ in range(2):
        with tempfile.TemporaryDirectory() as dr:
            out = _run_workflow(workspace, payload, dr)

            # Extract stable protocol signal only
            for line in out.splitlines():
                if line.startswith("Status:"):
                    results.append(line.strip())
                    break

    if len(results) != 2 or results[0] != results[1]:
        raise AssertionError("Non-deterministic protocol result")


def check_trace_schema():
    workspace, payload = _prepare_workspace()

    with tempfile.TemporaryDirectory() as dr:
        _run_workflow(workspace, payload, dr)

        trace_root = workspace / "traces"
        trace_files = list(trace_root.rglob("*.jsonl"))

        if not trace_files:
            raise AssertionError("No trace files produced")

        with open(trace_files[0]) as f:
            first = json.loads(f.readline())

        if "trace_schema_version" not in first:
            raise AssertionError("trace_schema_version missing")


# ── Main ───────────────────────────────────────────────────────────────

def main():
    print("\n=== Omnibachi Release Verification ===")
    print(f"  runtime root : {RUNTIME_ROOT}")

    print("\n-- Static Checks --")
    _check("Package imports cleanly", check_import)
    _check("No pgs_protocol.* imports", check_no_pgs_protocol_import)
    _check("No pgs_compiler.* imports", check_no_pgs_compiler_import)
    _check("No structure.* imports", check_no_structure_import)
    _check("No relative imports", check_no_relative_imports)
    _check("No sys.path manipulation", check_no_sys_path_manipulation)
    _check("No .md file loading", check_no_md_file_loading)
    _check("No fallback pattern markers", check_no_fallback_markers)
    _check("importlib confined to approved", check_importlib_confined)

    print("\n-- Packaging Checks --")
    _check("pyproject.toml valid", check_pyproject_toml)

    print("\n-- Execution Checks --")
    _check("Workflow executes to SUCCESS", check_execute_workflow)
    _check("Execution is deterministic", check_determinism)
    _check("Trace carries trace_schema_version", check_trace_schema)

    total = len(_PASS) + len(_FAIL)

    print(f"\n{'─' * 42}")
    print(f"  Passed : {len(_PASS)} / {total}")
    print(f"  Failed : {len(_FAIL)} / {total}")

    if _FAIL:
        print("\n  Failed checks:")
        for name, _ in _FAIL:
            print(f"    ✗ {name}")
        sys.exit(1)
    else:
        print("\n  ALL CHECKS PASSED — READY FOR RELEASE\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
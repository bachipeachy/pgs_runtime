"""
Test Suite 4: Workflow Execution Tests

Integration tests for end-to-end workflow execution using the v0.3.0 runtime.
These tests require a compiled tokenized snapshot at PGS_WORKSPACE.
Tests are skipped if PGS_WORKSPACE is not set or the workspace is not valid.

v0.3.0 execution path:
    load_domain(workspace, domain) → RuntimePackage
    make_trace_id(domain, wf_fqdn, payload) → trace_id
    TraceWriter(trace_dir, trace_id, domain, wf_addr, wf_fqdn)
    run_wf(wf_fqdn, payload, pkg, writer, data_root) → (status, surface)
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from pgs_runtime.evidence import TraceWriter, make_trace_id
from pgs_runtime.loader import load_domain
from pgs_runtime.scheduler import run_wf


def _get_workspace() -> Path | None:
    """Return PGS_WORKSPACE path if valid, else None."""
    ws = os.environ.get("PGS_WORKSPACE")
    if not ws:
        return None
    p = Path(ws)
    if not p.exists():
        return None
    return p


_WORKSPACE = _get_workspace()
_DATA_ROOT = os.environ.get("PGS_DATA_ROOT", "")

_SKIP_REASON = (
    "Integration test requires PGS_WORKSPACE and PGS_DATA_ROOT env vars "
    "pointing to a compiled tokenized snapshot."
)


@unittest.skipUnless(_WORKSPACE, _SKIP_REASON)
class TestWorkflowExecution(unittest.TestCase):
    """Integration tests for end-to-end workflow execution."""

    _WF_FQDN = "blockchain::WF_REGISTER_ACTOR_UNVERIFIED_V0"
    _DOMAIN = "blockchain"

    def setUp(self):
        self.workspace = _WORKSPACE
        self.data_root = _DATA_ROOT or str(self.workspace / "data")
        self.pkg = load_domain(self.workspace, self._DOMAIN)

    def _run_wf(self, wf_fqdn: str, payload: dict) -> tuple[str, dict, Path]:
        """Execute workflow; return (status, surface, trace_file)."""
        tmp_traces = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(tmp_traces), True)

        trace_id = make_trace_id(self._DOMAIN, wf_fqdn, payload)
        trace_dir = tmp_traces / trace_id
        trace_dir.mkdir(parents=True)

        wf_addr = self.pkg.vocab.addr(wf_fqdn)
        writer = TraceWriter(
            trace_dir=trace_dir,
            trace_id=trace_id,
            domain=self._DOMAIN,
            wf_addr=wf_addr,
            wf_fqdn=wf_fqdn,
        )

        try:
            status, surface = run_wf(
                wf_fqdn=wf_fqdn,
                payload=payload,
                pkg=self.pkg,
                writer=writer,
                data_root=self.data_root,
            )
        finally:
            writer.close()

        trace_file = trace_dir / f"{trace_id}.jsonl"
        return status, surface, trace_file

    def test_load_domain_succeeds(self):
        """load_domain MUST succeed with real workspace."""
        self.assertEqual(self.pkg.domain, self._DOMAIN)
        self.assertIsNotNone(self.pkg.dispatch)
        self.assertIsNotNone(self.pkg.handlers)
        self.assertIsNotNone(self.pkg.vocab)

    def test_wf_in_vocab(self):
        """Target WF FQDN MUST be present in the vocab index."""
        addr = self.pkg.vocab.addr(self._WF_FQDN)
        self.assertIsInstance(addr, int)
        self.assertGreater(addr, 0)

    def test_make_trace_id_format(self):
        """make_trace_id MUST return a non-empty string embedding the WF code."""
        payload = {"actor_name": "Alice", "email": "alice@example.com"}
        trace_id = make_trace_id(self._DOMAIN, self._WF_FQDN, payload)
        self.assertIsInstance(trace_id, str)
        self.assertGreater(len(trace_id), 0)
        wf_code = self._WF_FQDN.split("::")[-1]
        self.assertIn(wf_code, trace_id)

    def test_workflow_execution_returns_status(self):
        """run_wf MUST return a non-empty status string."""
        payload = {"actor_name": "TestActor", "email": "test@example.com"}
        status, surface, _ = self._run_wf(self._WF_FQDN, payload)
        self.assertIsInstance(status, str)
        self.assertIn(status, {"SUCCESS", "ALREADY_EXISTS", "VIOLATION", "NACK"})

    def test_trace_file_written(self):
        """run_wf MUST write a non-empty JSONL trace file."""
        import json
        payload = {"actor_name": "TraceActor", "email": "trace@example.com"}
        status, _, trace_file = self._run_wf(self._WF_FQDN, payload)
        self.assertTrue(trace_file.exists(), f"Trace file not written: {trace_file}")
        lines = [json.loads(line) for line in trace_file.read_text().splitlines() if line.strip()]
        self.assertGreater(len(lines), 0)

    def test_same_payload_same_hash_suffix(self):
        """Identical payload MUST produce the same hash suffix in the trace ID."""
        payload = {"actor_name": "DeterministicActor", "email": "det@example.com"}
        id1 = make_trace_id(self._DOMAIN, self._WF_FQDN, payload)
        id2 = make_trace_id(self._DOMAIN, self._WF_FQDN, payload)
        suffix1 = id1.rsplit("__", 1)[-1]
        suffix2 = id2.rsplit("__", 1)[-1]
        self.assertEqual(suffix1, suffix2)

    def test_different_payload_different_hash_suffix(self):
        """Different payloads MUST produce different hash suffixes in the trace ID."""
        id1 = make_trace_id(self._DOMAIN, self._WF_FQDN, {"actor_name": "Alice"})
        id2 = make_trace_id(self._DOMAIN, self._WF_FQDN, {"actor_name": "Bob"})
        suffix1 = id1.rsplit("__", 1)[-1]
        suffix2 = id2.rsplit("__", 1)[-1]
        self.assertNotEqual(suffix1, suffix2)


if __name__ == '__main__':
    unittest.main()

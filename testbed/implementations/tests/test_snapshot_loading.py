"""
Test Suite 1: Snapshot Loading

Tests that runtime correctly loads artifacts from protocol_snapshot/ directory.
"""

import json
import unittest

from testbed.implementations.tests.test_helpers import RuntimeTestCase


class TestSnapshotLoading(RuntimeTestCase):
    """Tests for snapshot artifact loading."""

    def test_snapshot_root_must_exist(self):
        """Runtime MUST fail if snapshot_root doesn't exist."""
        from omnibachi.implementation.ingress.gateway.workflow_gateway import execute_workflow

        non_existent = self.temp_path / "does_not_exist"

        result, _ = execute_workflow(
            workflow_code="WF_ANY_V0",
            payload={},
            runtime_binding="RB_ANY_V0",
            snapshot_root=non_existent,
            data_root=self.module_data_root,
            trace_root=self.trace_root,
        )

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.exit_reason_code, "SNAPSHOT_NOT_FOUND")
        self.assertIn("not found", result.message.lower())

    def test_workflow_artifact_must_exist_in_snapshot(self):
        """Runtime MUST fail if workflow artifact not in snapshot."""
        from omnibachi.implementation.ingress.gateway.workflow_gateway import execute_workflow

        result, _ = execute_workflow(
            workflow_code="WF_DOES_NOT_EXIST_V0",
            payload={},
            runtime_binding="TEST::RB_MINIMAL_V0",
            snapshot_root=self.snapshot_root,
            data_root=self.module_data_root,
            trace_root=self.trace_root,
        )

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.exit_reason_code, "WORKFLOW_NOT_FOUND")

    def test_rb_artifact_must_exist_in_snapshot(self):
        """Runtime MUST fail if RB artifact not in snapshot."""
        from omnibachi.implementation.ingress.gateway.workflow_gateway import execute_workflow

        # Create workflow artifact but NOT RB artifact
        wf_artifact = self.get_minimal_workflow_artifact()
        self.create_artifact_file("workflows", wf_artifact)

        result, _ = execute_workflow(
            workflow_code="TEST::WF_MINIMAL_V0",
            payload={},
            runtime_binding="RB_DOES_NOT_EXIST_V0",
            snapshot_root=self.snapshot_root,
            data_root=self.module_data_root,
            trace_root=self.trace_root,
        )

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.exit_reason_code, "RB_NOT_FOUND")

    def test_load_workflow_artifact_from_snapshot(self):
        """Runtime MUST successfully load workflow artifact from snapshot."""
        wf_artifact = self.get_minimal_workflow_artifact()
        wf_file = self.create_artifact_file("workflows", wf_artifact)

        self.assertTrue(wf_file.exists())

        with open(wf_file) as f:
            loaded = json.load(f)

        self.assertEqual(loaded["artifact_code"], "WF_MINIMAL_V0")
        self.assertEqual(loaded["fqdn_id"], "TEST::WF_MINIMAL_V0")
        self.assertIn("core", loaded["frontmatter"])
        self.assertIn("start_node", loaded["frontmatter"]["core"])

    def test_load_rb_artifact_from_snapshot(self):
        """Runtime MUST successfully load RB artifact from snapshot."""
        rb_artifact = self.get_minimal_rb_artifact()
        rb_file = self.create_artifact_file("runtime_bindings", rb_artifact)

        self.assertTrue(rb_file.exists())

        with open(rb_file) as f:
            loaded = json.load(f)

        self.assertEqual(loaded["artifact_code"], "RB_MINIMAL_V0")
        self.assertEqual(loaded["fqdn_id"], "TEST::RB_MINIMAL_V0")
        self.assertIn("frontmatter", loaded)
        self.assertIn("bindings", loaded["frontmatter"]["core"])

    def test_snapshot_artifacts_immutable_during_execution(self):
        """Runtime MUST NOT modify snapshot artifacts during execution."""
        wf_artifact = self.get_minimal_workflow_artifact()
        rb_artifact = self.get_minimal_rb_artifact()

        wf_file = self.create_artifact_file("workflows", wf_artifact)
        rb_file = self.create_artifact_file("runtime_bindings", rb_artifact)

        wf_content_before = wf_file.read_text()
        rb_content_before = rb_file.read_text()

        from omnibachi.implementation.ingress.gateway.workflow_gateway import execute_workflow

        try:
            execute_workflow(
                workflow_code="TEST::WF_MINIMAL_V0",
                payload={},
                runtime_binding="TEST::RB_MINIMAL_V0",
                snapshot_root=self.snapshot_root,
                data_root=self.module_data_root,
                trace_root=self.trace_root,
            )
        except Exception:
            pass  # Execution may fail, but snapshot must remain unchanged

        self.assertEqual(wf_file.read_text(), wf_content_before)
        self.assertEqual(rb_file.read_text(), rb_content_before)


if __name__ == '__main__':
    unittest.main()

"""
Test Suite 3: Failure Mode Tests

Tests that runtime fails fast and explicitly on errors.
"""

import json
import unittest
from pathlib import Path

from testbed.implementations.tests.test_helpers import RuntimeTestCase
from omnibachi.implementation.execution.host import RuntimeBindingError, RuntimeLoader


class TestFailureModes(RuntimeTestCase):
    """Tests for error handling and fail-fast behavior."""

    def test_fail_on_missing_snapshot(self):
        """MUST fail immediately if snapshot_root doesn't exist."""
        from omnibachi.implementation.ingress.gateway.workflow_gateway import execute_workflow

        non_existent = Path("/tmp/does_not_exist_snapshot")

        result, _ = execute_workflow(
            workflow_code="WF_ANY_V0",
            payload={},
            snapshot_root=non_existent,
            data_root=self.module_data_root,
            trace_root=self.trace_root,
        )

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.exit_reason_code, "SNAPSHOT_NOT_FOUND")
        self.assertEqual(result.error_code, "SNAPSHOT_NOT_FOUND")

    def test_fail_on_missing_workflow_artifact(self):
        """MUST fail immediately if workflow artifact not in snapshot."""
        from omnibachi.implementation.ingress.gateway.workflow_gateway import execute_workflow

        result, _ = execute_workflow(
            workflow_code="WF_DOES_NOT_EXIST_V0",
            payload={},
            runtime_binding="RB_ANY_V0",
            snapshot_root=self.snapshot_root,
            data_root=self.module_data_root,
            trace_root=self.trace_root,
        )

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.exit_reason_code, "WORKFLOW_NOT_FOUND")

    def test_fail_on_unknown_cs_capability(self):
        """MUST fail immediately if CS artifact not found in snapshot."""
        rb_artifact = {
            "frontmatter": {
                "core": {
                    "bindings": {
                        "capability_side_effects::CS_UNKNOWN_RUNTIME_V0": {
                            "policy": {}
                        }
                    }
                }
            }
        }

        rb_path = self.temp_path / "rb_unknown.json"
        with open(rb_path, 'w') as f:
            json.dump(rb_artifact, f)

        loader = RuntimeLoader(
            rb_path=rb_path,
            snapshot_root=self.snapshot_root,
            module_data_root=str(self.module_data_root),
        )

        with self.assertRaisesRegex(RuntimeBindingError, "CS artifact not found"):
            loader.load()

    def test_fail_on_malformed_rb_artifact(self):
        """MUST fail immediately if RB artifact has invalid structure (no frontmatter.core)."""
        rb_artifact = {
            "fqdn_id": "TEST::RB_INVALID_V0",
            # "frontmatter": {}  # MISSING
        }

        rb_path = self.temp_path / "rb_invalid.json"
        with open(rb_path, 'w') as f:
            json.dump(rb_artifact, f)

        loader = RuntimeLoader(
            rb_path=rb_path,
            module_data_root=str(self.module_data_root),
        )

        with self.assertRaisesRegex(RuntimeBindingError, "missing frontmatter"):
            loader.load()

    def test_fail_on_empty_rb_bindings(self):
        """MUST fail if RB artifact has no bindings."""
        rb_artifact = {
            "frontmatter": {
                "core": {
                    "bindings": {}  # Empty bindings
                }
            }
        }

        rb_path = self.temp_path / "rb_empty.json"
        with open(rb_path, 'w') as f:
            json.dump(rb_artifact, f)

        loader = RuntimeLoader(
            rb_path=rb_path,
            module_data_root=str(self.module_data_root),
        )

        with self.assertRaisesRegex(RuntimeBindingError, "no bindings"):
            loader.load()


if __name__ == '__main__':
    unittest.main()

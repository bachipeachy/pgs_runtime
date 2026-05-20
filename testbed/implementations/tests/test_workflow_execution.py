"""
Test Suite 4: Workflow Execution Tests

End-to-end workflow execution tests using WF → CC → CS pattern
via the testbed TestAppendRuntime CS implementation.
"""

import copy
import json
import unittest

from testbed.implementations.tests.test_helpers import RuntimeTestCase


class TestWorkflowExecution(RuntimeTestCase):
    """Tests for end-to-end workflow execution."""

    def _setup_append_workflow(self, wf_code: str) -> str:
        """
        Set up WF/CC/CS/RB artifacts for a simple APPEND workflow.

        Creates:
        - WF artifact: IN → CC → EXIT
        - IN artifact: accepts any payload (no required fields)
        - CC artifact: pipeline step, side_effect=testbed::CS_TEST_APPEND_V0, op=APPEND
        - CS artifact: wired to testbed.implementations.test_cs_runtime.TestAppendRuntime
        - RB artifact: binds CS_TEST_APPEND_V0
        """
        wf_fqdn = wf_code if "::" in wf_code else f"TEST::{wf_code}"
        in_fqdn = f"TEST::IN_APPEND_V0"
        cc_fqdn = "TEST::CC_TEST_APPEND_V0"
        rb_fqdn = "TEST::RB_TEST_APPEND_V0"

        wf_artifact = {
            "fqdn_id": wf_fqdn,
            "artifact_code": wf_fqdn,
            "namespace": "TEST",
            "frontmatter": {
                "wf_code": wf_fqdn,
                "runtime_binding": rb_fqdn,
                "core": {
                    "start_node": "validate",
                    "nodes": {
                        "validate": {
                            "type": "IN",
                            "fqdn_id": in_fqdn,
                            "next": {
                                "ACK": "append",
                                "NACK": "exit"
                            }
                        },
                        "append": {
                            "type": "CC",
                            "fqdn_id": cc_fqdn,
                            "inputs": {
                                "record": "$.payload.record"
                            },
                            "next": {
                                "SUCCESS": "exit",
                                "VIOLATION": "exit",
                                "BACKEND_ERROR": "exit"
                            }
                        },
                        "exit": {
                            "type": "EXIT",
                            "reason": "COMPLETED"
                        }
                    }
                }
            }
        }

        in_artifact = {
            "fqdn_id": in_fqdn,
            "artifact_code": "IN_APPEND_V0",
            "frontmatter": {
                "core": {
                    "inputs": {}  # No required fields — any payload accepted
                }
            }
        }

        cc_artifact = {
            "fqdn_id": cc_fqdn,
            "artifact_code": "CC_TEST_APPEND_V0",
            "frontmatter": {
                "cc_code": cc_fqdn,
                "core": {
                    "result_status_contract": {
                        "on_input_failure": "VIOLATION"
                    },
                    "pipeline": [
                        {
                            "step": "append",
                            "side_effect": "testbed::CS_TEST_APPEND_V0",
                            "op": "APPEND",
                            "inputs": {
                                "record": "$.inputs.record"
                            },
                            "outputs": {
                                "record_id": "$.capability_result.record_id"
                            },
                            "on_result": {
                                "SUCCESS": "continue",
                                "VIOLATION": "exit",
                                "BACKEND_ERROR": "exit"
                            }
                        }
                    ]
                }
            }
        }

        cs_artifact = self.get_test_cs_artifact()

        rb_artifact = {
            "fqdn_id": rb_fqdn,
            "artifact_code": "RB_TEST_APPEND_V0",
            "frontmatter": {
                "core": {
                    "bindings": {
                        "testbed::CS_TEST_APPEND_V0": {
                            "policy": {}
                        }
                    }
                }
            }
        }

        self.create_artifact_file("workflows", wf_artifact)
        self.create_artifact_file("intents", in_artifact)
        self.create_artifact_file("capability_contracts", cc_artifact)
        self.create_artifact_file("capability_side_effects", cs_artifact)
        self.create_artifact_file("runtime_bindings", rb_artifact)

        return wf_fqdn

    def test_minimal_workflow_execution(self):
        """Execute minimal WF → IN → CC → CS workflow, verify success."""
        from omnibachi.implementation.ingress.gateway.workflow_gateway import execute_workflow

        wf_fqdn = self._setup_append_workflow("TEST::WF_APPEND_MINIMAL_V0")

        result, trace_events = execute_workflow(
            workflow_code=wf_fqdn,
            payload={"record": {"name": "test-entry"}},
            snapshot_root=self.snapshot_root,
            data_root=self.module_data_root,
            trace_root=self.trace_root,
        )

        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.exit_reason_code, "COMPLETED")
        self.assertEqual(result.workflow_code, wf_fqdn)
        self.assertNotEqual(result.trace_id, "")
        self.assertGreaterEqual(result.duration_ms, 0)
        self.assertIsInstance(trace_events, list)

    def test_workflow_trace_generation(self):
        """Workflow execution MUST generate execution trace."""
        from omnibachi.implementation.ingress.gateway.workflow_gateway import execute_workflow

        wf_fqdn = self._setup_append_workflow("TEST::WF_APPEND_TRACE_V0")

        result, _ = execute_workflow(
            workflow_code=wf_fqdn,
            payload={"record": {"name": "trace-test"}},
            snapshot_root=self.snapshot_root,
            data_root=self.module_data_root,
            trace_root=self.trace_root,
        )

        self.assertEqual(result.status, "SUCCESS")

        # Verify trace file exists at expected path (domain-routed)
        trace_dir = self.trace_root / "TEST" / result.trace_id
        trace_file = trace_dir / f"{result.trace_id}.jsonl"

        self.assertTrue(trace_file.exists(), f"Trace file not found: {trace_file}")

        with open(trace_file) as f:
            trace_lines = [json.loads(line) for line in f if line.strip()]

        self.assertGreater(len(trace_lines), 0)

    def test_workflow_exit_condition_success(self):
        """Workflow MUST exit with SUCCESS when reaching exit node with reason COMPLETED."""
        from omnibachi.implementation.ingress.gateway.workflow_gateway import execute_workflow

        wf_fqdn = self._setup_append_workflow("TEST::WF_APPEND_EXIT_V0")

        result, _ = execute_workflow(
            workflow_code=wf_fqdn,
            payload={"record": {"data": "exit-test"}},
            snapshot_root=self.snapshot_root,
            data_root=self.module_data_root,
            trace_root=self.trace_root,
        )

        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.exit_reason_code, "COMPLETED")
        self.assertIsNone(result.error_code)
        self.assertIsNone(result.message)

    def test_workflow_payload_not_mutated(self):
        """Workflow execution MUST NOT mutate input payload."""
        from omnibachi.implementation.ingress.gateway.workflow_gateway import execute_workflow

        wf_fqdn = self._setup_append_workflow("TEST::WF_APPEND_IMMUTABLE_V0")

        original_payload = {
            "record": {"key": "original_value"},
            "nested": {"key": "value"}
        }
        payload_before = copy.deepcopy(original_payload)

        execute_workflow(
            workflow_code=wf_fqdn,
            payload=original_payload,
            snapshot_root=self.snapshot_root,
            data_root=self.module_data_root,
            trace_root=self.trace_root,
        )

        self.assertEqual(original_payload, payload_before)

    def test_workflow_execution_result_structure(self):
        """ExecutionResult MUST have all required fields."""
        from omnibachi.implementation.ingress.gateway.workflow_gateway import execute_workflow

        wf_fqdn = self._setup_append_workflow("TEST::WF_APPEND_RESULT_V0")

        result, trace_events = execute_workflow(
            workflow_code=wf_fqdn,
            payload={"record": {}},
            snapshot_root=self.snapshot_root,
            data_root=self.module_data_root,
            trace_root=self.trace_root,
        )

        self.assertTrue(hasattr(result, 'status'))
        self.assertTrue(hasattr(result, 'exit_reason_code'))
        self.assertTrue(hasattr(result, 'trace_id'))
        self.assertTrue(hasattr(result, 'duration_ms'))
        self.assertTrue(hasattr(result, 'result_payload'))
        self.assertTrue(hasattr(result, 'workflow_code'))
        self.assertTrue(hasattr(result, 'module'))
        self.assertTrue(hasattr(result, 'error_code'))
        self.assertTrue(hasattr(result, 'message'))

        self.assertIsInstance(trace_events, list)


if __name__ == '__main__':
    unittest.main()

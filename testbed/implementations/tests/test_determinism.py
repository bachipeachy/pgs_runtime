"""
Test Suite 5: Determinism Tests

Tests that runtime execution is deterministic.
"""

import json
import hashlib
import unittest

from testbed.implementations.tests.test_helpers import RuntimeTestCase


def generate_deterministic_id(prefix: str, content: dict) -> str:
    """
    Generate deterministic ID from content using SHA256 hash.
    Used for testing deterministic ID generation.
    """
    # Serialize content deterministically (sorted keys)
    content_json = json.dumps(content, sort_keys=True, separators=(',', ':'))
    # Hash with SHA256
    content_hash = hashlib.sha256(content_json.encode('utf-8')).hexdigest()
    # Return prefix + first 16 chars of hash
    return f"{prefix}_{content_hash[:16]}"


class TestDeterminism(RuntimeTestCase):
    """Tests for execution determinism."""

    def test_same_input_produces_same_result(self):
        """Executing same workflow with same payload MUST produce identical results."""
        from omnibachi.implementation.ingress.gateway.workflow_gateway import execute_workflow

        workflow_artifact = {
            "fqdn_id": "TEST::WF_DETERMINISTIC_V0",
            "artifact_code": "WF_DETERMINISTIC_V0",
            "namespace": "TEST",
            "frontmatter": {
                "runtime_binding": "TEST::RB_DETERMINISTIC_V0"
            },
            "core": {
                "entry_node": "read",
                "nodes": {
                    "read": {
                        "node_code": "read",
                        "capability": "CS_REGISTRY_V0",
                        "operation": "READ",
                        "inputs": {
                            "key": "test_key"
                        },
                        "transitions": {
                            "SUCCESS": "exit",
                            "NOT_FOUND": "exit"
                        }
                    },
                    "exit": {
                        "node_code": "exit",
                        "exit": True
                    }
                }
            }
        }

        registry_path = self.module_data_root / "registry.json"
        rb_artifact = {
            "fqdn_id": "TEST::RB_DETERMINISTIC_V0",
            "artifact_code": "RB_DETERMINISTIC_V0",
            "core": {
                "bindings": {
                    "CS_REGISTRY_V0": {
                        "type": "CS",
                        "host": "RegistryRuntime",
                        "operation": "READ",
                        "policy": {
                            "path": str(registry_path)
                        }
                    }
                }
            }
        }

        # Write test data
        registry_path.write_text(json.dumps({"test_key": "deterministic_value"}))

        self.create_artifact_file("workflows", workflow_artifact)
        self.create_artifact_file("capability_side_effects", rb_artifact)

        # Execute workflow multiple times with same payload
        payload = {"input_field": "test_value"}

        results = []
        for _ in range(3):
            result, _ = execute_workflow(
                workflow_code="TEST::WF_DETERMINISTIC_V0",
                payload=payload.copy(),
                snapshot_root=self.snapshot_root,
                data_root=self.module_data_root,
                trace_root=self.trace_root,
            )
            results.append(result)

        # All executions should produce same status
        self.assertTrue(all(r.status == results[0].status for r in results))
        self.assertTrue(all(r.exit_reason_code == results[0].exit_reason_code for r in results))
        self.assertTrue(all(r.workflow_code == results[0].workflow_code for r in results))

    def test_deterministic_id_generation(self):
        """Deterministic ID generation MUST produce same ID for same content."""
        content1 = {"field1": "value1", "field2": "value2"}
        content2 = {"field1": "value1", "field2": "value2"}
        content3 = {"field2": "value2", "field1": "value1"}  # Different order

        id1 = generate_deterministic_id("AC", content1)
        id2 = generate_deterministic_id("AC", content2)
        id3 = generate_deterministic_id("AC", content3)

        # Same content → same ID (key order doesn't matter)
        self.assertEqual(id1, id2)
        self.assertEqual(id1, id3)

        # Different content → different ID
        content_different = {"field1": "different"}
        id_different = generate_deterministic_id("AC", content_different)
        self.assertNotEqual(id_different, id1)

    def test_snapshot_based_execution_is_reproducible(self):
        """Snapshot-based execution MUST be reproducible."""
        from omnibachi.implementation.ingress.gateway.workflow_gateway import execute_workflow

        workflow_artifact = {
            "fqdn_id": "TEST::WF_REPRODUCIBLE_V0",
            "artifact_code": "WF_REPRODUCIBLE_V0",
            "namespace": "TEST",
            "frontmatter": {
                "runtime_binding": "TEST::RB_REPRODUCIBLE_V0"
            },
            "core": {
                "entry_node": "write",
                "nodes": {
                    "write": {
                        "node_code": "write",
                        "capability": "CS_MUTABLE_JSON_V0",
                        "operation": "WRITE",
                        "inputs": {
                            "key": "counter",
                            "value": {"count": 1}
                        },
                        "transitions": {
                            "SUCCESS": "exit"
                        }
                    },
                    "exit": {
                        "node_code": "exit",
                        "exit": True
                    }
                }
            }
        }

        storage_path = self.module_data_root / "storage.json"
        rb_artifact = {
            "fqdn_id": "TEST::RB_REPRODUCIBLE_V0",
            "artifact_code": "RB_REPRODUCIBLE_V0",
            "core": {
                "bindings": {
                    "CS_MUTABLE_JSON_V0": {
                        "type": "CS",
                        "host": "MutableJsonRuntime",
                        "operation": "WRITE",
                        "policy": {
                            "path": str(storage_path)
                        }
                    }
                }
            }
        }

        self.create_artifact_file("workflows", workflow_artifact)
        self.create_artifact_file("capability_side_effects", rb_artifact)

        # Execute twice
        result1, _ = execute_workflow(
            workflow_code="TEST::WF_REPRODUCIBLE_V0",
            payload={},
            snapshot_root=self.snapshot_root,
            data_root=self.module_data_root,
            trace_root=self.trace_root,
        )

        # Clear storage for second run
        if storage_path.exists():
            storage_path.unlink()

        result2, _ = execute_workflow(
            workflow_code="TEST::WF_REPRODUCIBLE_V0",
            payload={},
            snapshot_root=self.snapshot_root,
            data_root=self.module_data_root,
            trace_root=self.trace_root,
        )

        # Same workflow, same payload → same execution path
        self.assertEqual(result1.status, result2.status)
        self.assertEqual(result1.exit_reason_code, result2.exit_reason_code)

    def test_trace_ids_are_unique_but_execution_deterministic(self):
        """Trace IDs MUST be unique per execution, but execution logic deterministic."""
        from omnibachi.implementation.ingress.gateway.workflow_gateway import execute_workflow

        wf_code = "TEST::WF_TRACE_UNIQUE_V0"
        name_registry_path = self.module_data_root / "name_registry_unique.json"

        wf_artifact = {
            "fqdn_id": wf_code,
            "artifact_code": wf_code,
            "namespace": "TEST",
            "frontmatter": {
                "wf_code": wf_code,
                "runtime_binding": "TEST::RB_TRACE_UNIQUE_V0",
                "core": {
                    "start_node": "register",
                    "nodes": {
                        "register": {
                            "type": "CC",
                            "code": "CC_TEST_REGISTER_UNIQUE_V0",
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

        cc_artifact = {
            "fqdn_id": "CC_TEST_REGISTER_UNIQUE_V0",
            "artifact_code": "CC_TEST_REGISTER_UNIQUE_V0",
            "frontmatter": {
                "cc_code": "CC_TEST_REGISTER_UNIQUE_V0",
                "core": {
                    "pipeline": [
                        {
                            "step": "register",
                            "side_effect": "CS_NAME_REGISTRY_V0",
                            "op": "WRITE",
                            "inputs": {
                                "name": "bob@example.com",
                                "resource_addresses": ["0xDEF456"]
                            },
                            "outputs": {
                                "success": "$.capability_result.success"
                            },
                            "on_result": {
                                "SUCCESS": "exit",
                                "VIOLATION": "exit",
                                "BACKEND_ERROR": "exit"
                            }
                        }
                    ]
                }
            }
        }

        rb_artifact = {
            "fqdn_id": "TEST::RB_TRACE_UNIQUE_V0",
            "artifact_code": "RB_TRACE_UNIQUE_V0",
            "core": {
                "bindings": {
                    "CS_NAME_REGISTRY_V0": {
                        "type": "CS",
                        "host": "NameRegistryRuntime",
                        "operation": "WRITE",
                        "policy": {
                            "path": str(name_registry_path)
                        }
                    }
                }
            }
        }

        self.create_artifact_file("workflows", wf_artifact)
        self.create_artifact_file("capability_contracts", cc_artifact)
        self.create_artifact_file("capability_side_effects", rb_artifact)

        # Execute multiple times
        results = []
        for _ in range(3):
            result, _ = execute_workflow(
                workflow_code=wf_code,
                payload={},
                snapshot_root=self.snapshot_root,
                data_root=self.module_data_root,
                trace_root=self.trace_root,
            )
            results.append(result)

        # Trace IDs should be unique
        trace_ids = [r.trace_id for r in results]
        self.assertEqual(len(set(trace_ids)), len(trace_ids))  # All unique

        # But execution results should be identical
        self.assertTrue(all(r.status == results[0].status for r in results))
        self.assertTrue(all(r.exit_reason_code == results[0].exit_reason_code for r in results))

    def test_json_serialization_deterministic(self):
        """JSON serialization for ID generation MUST be deterministic."""
        # Different key orders
        content_a = {"z": 1, "a": 2, "m": 3}
        content_b = {"a": 2, "m": 3, "z": 1}
        content_c = {"m": 3, "z": 1, "a": 2}

        id_a = generate_deterministic_id("TEST", content_a)
        id_b = generate_deterministic_id("TEST", content_b)
        id_c = generate_deterministic_id("TEST", content_c)

        # Must all be identical (sorted keys)
        self.assertEqual(id_a, id_b)
        self.assertEqual(id_b, id_c)


if __name__ == '__main__':
    unittest.main()

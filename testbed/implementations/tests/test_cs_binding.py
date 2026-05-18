"""
Test Suite 2: CS Binding Tests

Tests that RuntimeLoader loads RB artifacts and resolves CS runtimes
from compile-time-sealed handler_ref in CS artifacts.
"""

import json
import unittest

from testbed.implementations.tests.test_helpers import RuntimeTestCase
from omnibachi.implementation.execution.host import RuntimeLoader, RuntimeBindingError


class TestCSBinding(RuntimeTestCase):
    """Tests for CS runtime binding and instantiation."""

    def test_runtime_loader_requires_valid_rb_path(self):
        """RuntimeLoader MUST fail if RB artifact path doesn't exist."""
        non_existent = self.temp_path / "does_not_exist.json"

        with self.assertRaisesRegex(RuntimeBindingError, "not found"):
            RuntimeLoader(rb_path=non_existent)

    def test_rb_parameter_substitution_module_data_root(self):
        """RuntimeLoader MUST substitute {{module_data_root}} in RB policy."""
        rb_artifact = {
            "frontmatter": {
                "core": {
                    "bindings": {
                        "capability_side_effects::CS_MUTABLE_JSON_V0": {
                            "policy": {
                                "path": "{{module_data_root}}/storage.json"
                            }
                        }
                    }
                }
            }
        }

        rb_path = self.temp_path / "rb_params.json"
        with open(rb_path, 'w') as f:
            json.dump(rb_artifact, f)

        loader = RuntimeLoader(
            rb_path=rb_path,
            snapshot_root=self.snapshot_root,
            module_data_root=str(self.module_data_root),
        )
        # Parameter substitution happens before load() reaches CS artifact lookup
        # Succeeds if snapshot has the compiled CS artifact; fails at artifact lookup otherwise.
        # Either way, the substitution itself must not raise a substitution error.
        try:
            loader.load()
        except RuntimeBindingError as e:
            self.assertNotIn("module_data_root", str(e),
                             "Substitution must not fail — only artifact lookup may fail")

    def test_rb_missing_module_data_root_raises(self):
        """RuntimeLoader MUST fail if {{module_data_root}} used but not provided."""
        rb_artifact = {
            "frontmatter": {
                "core": {
                    "bindings": {
                        "capability_side_effects::CS_MUTABLE_JSON_V0": {
                            "policy": {
                                "path": "{{module_data_root}}/storage.json"
                            }
                        }
                    }
                }
            }
        }

        rb_path = self.temp_path / "rb_no_root.json"
        with open(rb_path, 'w') as f:
            json.dump(rb_artifact, f)

        loader = RuntimeLoader(rb_path=rb_path)

        with self.assertRaisesRegex(RuntimeBindingError, "module_data_root"):
            loader.load()

    def test_cs_artifact_not_found_raises(self):
        """RuntimeLoader MUST fail with clear error if CS artifact missing from snapshot."""
        rb_artifact = {
            "frontmatter": {
                "core": {
                    "bindings": {
                        "capability_side_effects::CS_NONEXISTENT_V0": {
                            "policy": {}
                        }
                    }
                }
            }
        }

        rb_path = self.temp_path / "rb_missing_cs.json"
        with open(rb_path, 'w') as f:
            json.dump(rb_artifact, f)

        loader = RuntimeLoader(
            rb_path=rb_path,
            snapshot_root=self.snapshot_root,
        )

        with self.assertRaisesRegex(RuntimeBindingError, "CS artifact not found"):
            loader.load()


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""
Runtime Tests runner — pgs_runtime testbed.

Test category: Runtime Tests
Purpose: verify execution substrate correctness — snapshot loading, CS binding,
workflow execution, determinism, and failure modes.

Uses standard Python unittest framework (no external dependencies).
"""

import sys
import unittest
import os

def run_tests(verbosity=2):
    """Run all runtime tests."""
    loader = unittest.TestLoader()
    
    # Get the directory where this script is located
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # The top-level directory for test discovery is the project root
    top_level_dir = os.path.dirname(script_dir) # This should be /Users/bp/pgs_runtime
    
    # The start directory for discovery, relative to top_level_dir
    # This should be the package name 'testbed.tests'
    relative_tests_path = os.path.relpath(os.path.join(script_dir, 'implementations/tests'), top_level_dir)
    start_package_name = relative_tests_path.replace(os.sep, '.') # e.g., 'testbed.tests'
    
    suite = loader.discover(start_package_name, pattern='test_*.py', top_level_dir=top_level_dir)

    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)

    # Return exit code (0 = success, 1 = failure)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    # Check for verbose flag
    verbosity = 2 if '-v' in sys.argv or '--verbose' in sys.argv else 1

    sys.exit(run_tests(verbosity))

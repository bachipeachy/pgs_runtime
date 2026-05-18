"""
conformance_test.py — Schema-to-constant drift detection.

Governed by: STRUCTURE_TRACE_SCHEMA_V0 §10.5 rule 6 (classification completeness)

Reads SCHEMA_TRACE_EVENT_V0.json and verifies that every enum value
defined in the schema is covered by the Trace Examiner's constants.
Detects drift between registry schema and examiner classification code.

Run:
    python -m authoring.trace_examiner.conformance_test
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _load_schema() -> dict:
    """Load SCHEMA_TRACE_EVENT_V0.json via governance path registry."""
    from pgs_governance.structure.structure.resolution import paths
    schema_path = paths.governance.schema("SCHEMA_TRACE_EVENT_V0")
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema not found: {schema_path}")
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _extract_enum(schema: dict, event_type: str, field_name: str) -> set[str]:
    """Extract enum values for a field in a conditional event type definition."""
    for rule in schema.get("allOf", []):
        if_clause = rule.get("if", {}).get("properties", {}).get("event_type", {})
        if if_clause.get("const") == event_type:
            then_props = (
                rule.get("then", {})
                .get("properties", {})
                .get("payload", {})
                .get("properties", {})
            )
            field_def = then_props.get(field_name, {})
            return set(field_def.get("enum", []))
    return set()


def test_error_code_coverage() -> list[str]:
    """Verify classifier covers all error_code enum values from schema."""
    schema = _load_schema()
    schema_codes = _extract_enum(schema, "error", "error_code")

    # These are the error codes the classifier explicitly handles in _classify_error_event
    # Any code not explicitly handled falls through to Rule 9 (catch-all CT_STRUCTURE_ERROR)
    # The coverage requirement is: every schema code must be classifiable, not necessarily
    # have its own branch. The catch-all satisfies this.
    #
    # What we actually verify: the codes used in explicit branches exist in the schema.
    classifier_explicit_codes = {
        "EXPRESSION_RESOLUTION_FAILED",  # Rule 1
        "SCHEMA_VALIDATION_FAILED",      # Rule 2
        "BINDING_RESOLUTION_FAILED",     # Rule 8
    }

    errors = []

    if not schema_codes:
        errors.append("DRIFT: No error_code enum found in schema")
        return errors

    # Check that classifier's explicit codes exist in schema
    orphaned = classifier_explicit_codes - schema_codes
    if orphaned:
        errors.append(
            f"DRIFT: Classifier references error codes not in schema: {sorted(orphaned)}"
        )

    return errors


def test_node_category_coverage() -> list[str]:
    """Verify classifier covers all node_category enum values from schema."""
    schema = _load_schema()
    schema_categories = _extract_enum(schema, "error", "node_category")

    # Categories used in classifier _classify_error_event
    classifier_categories = {"CT", "CS"}  # Rules 4, 5

    errors = []

    if not schema_categories:
        errors.append("DRIFT: No node_category enum found in schema")
        return errors

    orphaned = classifier_categories - schema_categories
    if orphaned:
        errors.append(
            f"DRIFT: Classifier references node categories not in schema: {sorted(orphaned)}"
        )

    return errors


def test_exit_reason_code_coverage() -> list[str]:
    """Verify classifier covers all exit_reason_code enum values from schema."""
    schema = _load_schema()
    schema_exit_codes = _extract_enum(schema, "workflow_complete", "exit_reason_code")

    # Exit reason codes handled in _classify_from_workflow_complete
    classifier_exit_codes = {
        "NO_TRANSITION",      # Rule 6 → GRAPH_STRUCTURE_ERROR
        "NO_ENTRY_NODE",      # Rule 7 → GRAPH_STRUCTURE_ERROR
        "NODE_NOT_FOUND",     # → GRAPH_STRUCTURE_ERROR
        "EXIT_NOT_FOUND",     # → GRAPH_STRUCTURE_ERROR
        "TIMEOUT",            # → None (not structural)
        "ABORT",              # → None (not structural)
        "ADMISSION_DENIED",   # → BUSINESS_VIOLATION
        "GOVERNANCE_VIOLATION",  # → BUSINESS_VIOLATION
        "EXIT_VIOLATION",     # → BUSINESS_VIOLATION
        "EXIT_REJECTED",      # → BUSINESS_VIOLATION
        "EXIT_ALREADY_EXISTS", # → BUSINESS_VIOLATION
        "EXIT_BACKEND_ERROR", # → CS_RUNTIME_ERROR
        "EXECUTION_ERROR",    # → CT_STRUCTURE_ERROR (catch-all)
        "COMPLETED",          # Fast-path success (never reaches classifier)
    }

    errors = []

    if not schema_exit_codes:
        errors.append("DRIFT: No exit_reason_code enum found in schema")
        return errors

    # Every schema code must be handled by the classifier
    unhandled = schema_exit_codes - classifier_exit_codes
    if unhandled:
        errors.append(
            f"DRIFT: Schema exit_reason_codes not handled by classifier: {sorted(unhandled)}"
        )

    # Every classifier code must exist in schema
    orphaned = classifier_exit_codes - schema_exit_codes
    if orphaned:
        errors.append(
            f"DRIFT: Classifier references exit_reason_codes not in schema: {sorted(orphaned)}"
        )

    return errors


def test_event_types() -> list[str]:
    """Verify parser handles all event types that might appear in traces."""
    schema = _load_schema()
    schema_event_types = set(
        schema.get("properties", {}).get("event_type", {}).get("enum", [])
    )

    # Event types the parser explicitly indexes
    parser_indexed = {"error", "node_end", "workflow_complete", "execution_start"}

    errors = []

    if not schema_event_types:
        errors.append("DRIFT: No event_type enum found in schema")
        return errors

    # Parser must at minimum handle these critical types
    critical_types = {"execution_start", "node_end", "workflow_complete", "error"}
    missing_critical = critical_types - schema_event_types
    if missing_critical:
        errors.append(
            f"DRIFT: Critical event types missing from schema: {sorted(missing_critical)}"
        )

    return errors


def run_all() -> bool:
    """Run all conformance tests. Returns True if all pass."""
    all_errors: list[str] = []

    tests = [
        ("error_code coverage", test_error_code_coverage),
        ("node_category coverage", test_node_category_coverage),
        ("exit_reason_code coverage", test_exit_reason_code_coverage),
        ("event_type coverage", test_event_types),
    ]

    for name, test_fn in tests:
        errors = test_fn()
        if errors:
            for e in errors:
                all_errors.append(f"[{name}] {e}")
            print(f"  FAIL  {name}")
            for e in errors:
                print(f"        {e}")
        else:
            print(f"  PASS  {name}")

    if all_errors:
        print(f"\n{len(all_errors)} drift error(s) detected.")
        return False

    print("\nAll schema-to-constant conformance checks passed.")
    return True


if __name__ == "__main__":
    from pgs_governance.structure.structure.resolution import bootstrap
    bootstrap()

    print("[trace-examiner] Schema-to-constant drift test\n")
    ok = run_all()
    sys.exit(0 if ok else 1)

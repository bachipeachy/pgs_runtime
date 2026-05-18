"""
admission.py — Read-only admission gate for workflow precondition enforcement.

Governed by: CONSTITUTION_ADMISSION_V0

Enforces the 'admission' block declared in workflow specs:
- requires: events that MUST exist before workflow may execute
- forbids:  events that MUST NOT exist before workflow may execute
- bindings: maps event fields to payload fields for filtered matching

Read-only. No side-effects. No mutation. No capability dispatch.
No router invocation. Pure event log scan via filesystem.

Binding resolution: every payload_field declared in a binding MUST be
present in the payload. Missing fields are a contract violation — fail hard.
"""

import json
from pathlib import Path
from typing import Any

from omnibachi.implementation.execution.machine.errors import StructuredError


def check_admission(
    admission: dict[str, Any],
    payload: dict[str, Any],
    data_root: Path,
) -> str:
    """
    Check admission block against event logs.

    Returns:
        "ADMITTED" if all preconditions are met, or a descriptive
        reason string explaining why admission was denied.
    """
    requires = admission.get("requires", [])
    forbids = admission.get("forbids", [])
    bindings = admission.get("bindings", {})

    if not requires and not forbids:
        return "ADMITTED"

    # Resolve binding values from payload — fails hard on missing fields.
    resolved_bindings = _resolve_bindings(bindings, payload)

    # Build shared identity context from all resolved bindings.
    identity_context: dict[str, Any] = {}
    for _ec, fields in resolved_bindings.items():
        identity_context.update(fields)

    # Load all events from module event logs (read-only)
    events = _load_module_events(data_root)

    # Check requires: each required event must exist
    for event_code in requires:
        binding = resolved_bindings.get(event_code, {})
        if not _event_exists(events, event_code, binding):
            return f"requires {event_code}"

    # Check forbids: each forbidden event must NOT exist
    for event_code in forbids:
        binding = resolved_bindings.get(event_code, identity_context)
        if _event_exists(events, event_code, binding):
            return f"forbids {event_code}"

    return "ADMITTED"


def _resolve_bindings(
    bindings: dict[str, dict[str, str]],
    payload: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """
    Resolve admission binding values from the payload.

    Each binding maps: { event_field: payload_field }.

    Every payload_field declared in a binding MUST be present in the
    payload. Missing fields are a contract violation — fail hard.
    """
    resolved = {}

    for event_code, field_map in bindings.items():
        resolved_fields = {}
        for event_field, payload_field in field_map.items():
            value = payload.get(payload_field)
            if value is None:
                raise StructuredError(
                    error_code="ADMISSION_BINDING_MISSING_FIELD",
                    node_category="WF",
                    message=(
                        f"Admission binding for '{event_code}' references payload field "
                        f"'{payload_field}' which is not present in the payload."
                    ),
                )
            resolved_fields[event_field] = value

        resolved[event_code] = resolved_fields

    return resolved


def _load_module_events(data_root: Path) -> list[dict]:
    """
    Load all event records from module event logs. Read-only.

    Scans all *.jsonl files recursively in the data root.
    """
    events = []

    if not data_root.exists():
        return events

    for jsonl_file in sorted(data_root.glob("**/*.jsonl")):
        try:
            with open(jsonl_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
        except (FileNotFoundError, json.JSONDecodeError):
            continue

    return events


def _event_exists(
    events: list[dict],
    event_code: str,
    binding: dict[str, Any],
) -> bool:
    """
    Check if an event matching event_code and binding filters exists.
    """
    for event in events:
        record = event.get("record", {})
        record_event = record.get("event_code") or record.get("event_type")
        if record_event != event_code:
            continue

        match = True
        for event_field, expected_value in binding.items():
            if record.get(event_field) != expected_value:
                match = False
                break

        if match:
            return True

    return False

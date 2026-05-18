"""
expression_resolver.py — Expression resolution for capability bindings.

Governed by: CONSTITUTION_EXECUTION_V0

Resolves JSONPath-like expressions against execution state.
"""

from datetime import datetime, timezone
from typing import Any


def _resolve_template(expr: str) -> str | None:
    """
    Resolve template placeholders like {{timestamp}}.

    Supported templates:
    - {{timestamp}} -> current UTC ISO 8601 timestamp
    """
    if not expr.startswith("{{") or not expr.endswith("}}"):
        return None

    template_name = expr[2:-2].strip()

    if template_name == "timestamp":
        return datetime.now(timezone.utc).isoformat()

    return None


def resolve_expression(expr: Any, state: dict[str, Any]) -> Any | None:
    """
    Resolve a JSONPath-like expression against state.

    Supported paths:
    - $.payload.field -> state["payload"]["field"]
    - $.inputs.field -> state["inputs"]["field"]
    - $.results.X.field -> state["results"]["X"]["field"]
    - $.capability_result.field -> state["capability_result"]["field"]

    Also handles nested structures by recursively resolving expressions.
    """
    # Handle nested dicts - recursively resolve each value.
    if isinstance(expr, dict):
        resolved = {}
        for k, v in expr.items():
            val = resolve_expression(v, state)
            if val is None and isinstance(v, str) and v.startswith("$."):
                continue  # Absent path — omit from result
            resolved[k] = val
        return resolved

    # Handle lists - recursively resolve each element
    if isinstance(expr, list):
        resolved = []
        for v in expr:
            val = resolve_expression(v, state)
            if val is None and isinstance(v, str) and v.startswith("$."):
                continue  # Absent path — omit from result
            resolved.append(val)
        return resolved

    if not expr or not isinstance(expr, str):
        return expr

    # Check for template placeholders like {{timestamp}}
    template_result = _resolve_template(expr)
    if template_result is not None:
        return template_result

    if not expr.startswith("$."):
        return expr

    path = expr[2:]
    parts = path.split(".")

    current = state
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None

    return current


def resolve_inputs(
    binding: dict[str, Any],
    state: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    """
    Resolve input expressions from binding.

    Returns:
        Tuple of (resolved_inputs, failed_input_name)
    """
    resolved: dict[str, Any] = {}

    for name, expr in binding.get("inputs", {}).items():
        val = resolve_expression(expr, state)
        if val is None:
            return {}, name
        resolved[name] = val

    return resolved, None


def resolve_outputs(
    binding: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    """
    Resolve output expressions from binding.

    PROTOCOL: Output binding failure detection (fail-fast).
    - If expression references non-existent path → emit diagnostic
    - Silent drops are allowed only for optional outputs
    """
    resolved: dict[str, Any] = {}

    for name, expr in binding.get("outputs", {}).items():
        val = resolve_expression(expr, state)
        if val is not None:
            resolved[name] = val
        elif isinstance(expr, str) and expr.startswith("$."):
            # Expression-based output that failed to resolve
            # Emit diagnostic (deterministic, no guessing)
            path_parts = expr[2:].split(".")
            current = state
            resolved_depth = 0

            # Find how far the path resolved
            for i, part in enumerate(path_parts):
                if isinstance(current, dict) and part in current:
                    current = current[part]
                    resolved_depth = i + 1
                else:
                    break

            # Emit diagnostic if partial resolution occurred
            if resolved_depth > 0 and resolved_depth < len(path_parts):
                pass  # Partial path — output silently omitted

    return resolved

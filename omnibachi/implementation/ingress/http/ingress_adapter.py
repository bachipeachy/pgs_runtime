"""
ingress_adapter.py — Thin HTTP request adapter.

Governed by: CONSTITUTION_TRANSPORT_V0

Parses HTTP request, calls gateway, returns response.
No business logic. No module resolution. No protocol interpretation.
All admission validation governed by CC_VALIDATE_HTTP_REQUEST_V0.
"""

import hashlib
import json
from pathlib import Path

from omnibachi.implementation.ingress.gateway.workflow_gateway import execute_workflow
from omnibachi.implementation.egress.http.response_adapter import send_json_response, send_error_response


_execution_cache: dict[tuple[str, str], dict] = {}


def handle_request(request: dict, snapshot_root: Path, data_root: Path, trace_root: Path) -> dict:
    result, _ = execute_workflow(
        workflow_code=request.get("workflow_code"),
        intent_code=request.get("intent_code"),
        payload=request["payload"],
        runtime_binding=request.get("runtime_binding"),
        snapshot_root=snapshot_root,
        data_root=data_root,
        trace_root=trace_root,
        mode=request.get("mode", "runtime"),
    )
    return result.to_dict()


def _payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def handle_run(handler, snapshot_root: Path, data_root: Path, trace_root: Path) -> None:
    """Handle POST /api/run. Duplicate submissions return cached result."""
    try:
        body = _read_json_body(handler)

        workflow_code = body.get("workflow_code")
        intent_code = body.get("intent_code")
        payload = body.get("payload", {})

        if not workflow_code and not intent_code:
            raise ValueError("Request must include 'workflow_code' or 'intent_code'")

        cache_key = (workflow_code or intent_code, _payload_hash(payload))
        if cache_key in _execution_cache:
            response = dict(_execution_cache[cache_key])
            response["already_submitted"] = True
            send_json_response(handler, response)
            return

        result, _ = execute_workflow(
            workflow_code=workflow_code,
            intent_code=intent_code,
            payload=payload,
            snapshot_root=snapshot_root,
            data_root=data_root,
            trace_root=trace_root,
            mode="runtime",
        )

        result_dict = result.to_dict()
        if result.status == "SUCCESS":
            _execution_cache[cache_key] = result_dict

        send_json_response(handler, result_dict)

    except ValueError as e:
        send_error_response(handler, 400, str(e))
    except Exception as e:
        send_error_response(handler, 500, f"Internal Execution Error: {str(e)}")


def handle_direct_route(handler, workflow_fqdn: str, snapshot_root: Path, data_root: Path, trace_root: Path) -> None:
    """Handle a direct TI-registered route."""
    try:
        body = _read_json_body(handler)

        result, _ = execute_workflow(
            workflow_code=workflow_fqdn,
            payload=body,
            snapshot_root=snapshot_root,
            data_root=data_root,
            trace_root=trace_root,
            mode="runtime",
        )

        send_json_response(handler, result.to_dict())

    except ValueError as e:
        send_error_response(handler, 400, str(e))
    except Exception as e:
        send_error_response(handler, 500, f"Internal Execution Error: {str(e)}")


def _read_json_body(handler) -> dict:
    content_length = int(handler.headers.get("Content-Length", 0))
    if content_length == 0:
        raise ValueError("Empty request body")
    return json.loads(handler.rfile.read(content_length))

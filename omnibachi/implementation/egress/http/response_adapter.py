"""
response_adapter.py — HTTP response formatting.

Governed by: CONSTITUTION_EXECUTION_V0

Pure response formatting. No business logic.
"""

import json


def send_json_response(handler, data: dict, status: int = 200):
    """Send JSON response with proper headers."""
    body = json.dumps(data, indent=2).encode("utf-8")

    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def send_error_response(handler, status: int, message: str):
    """Send structured error JSON response."""
    send_json_response(handler, {
        "status": "FAILED",
        "error_code": "TRANSPORT_ERROR",
        "message": message,
    }, status=status)

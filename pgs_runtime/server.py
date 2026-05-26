"""
server.py — Minimal HTTP gateway for the PGS token-native runtime.

Serves static demo UIs and executes workflow requests via POST /api/run.
Uses only Python stdlib (no external HTTP framework dependencies).

Usage:
    python -m pgs_runtime.server \
        --port 8000 \
        --workspace /abs/path/to/pgs_workspace \
        --data-root  /abs/path/to/pgs_workspace/data \
        --domain "blockchain=/path/to/blockchain/static" \
        --domain "agent_governance=/path/to/agent_governance/static" \
        --domain "collatz_conjecture=/path/to/collatz_conjecture/static"

URL layout:
    GET  /                      — landing page listing registered domains
    GET  /<domain_label>/*      — static files from that domain's static dir
    POST /api/run               — execute a workflow; returns JSON envelope

POST /api/run request body (JSON):
    {
        "workflow_code": "blockchain::WF_REGISTER_ACTOR_UNVERIFIED_V0",
        "payload": { ... }
    }

POST /api/run response envelope (JSON):
    {
        "status":       "SUCCESS" | "VIOLATION" | ...,
        "trace_id":     "20260524T...",
        "duration_ms":  42,
        "workflow_code": "...",
        "result_payload": { ... }   # CC surface outputs
    }
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Lock
from typing import Any

from pgs_runtime.evidence import TraceWriter, make_trace_id
from pgs_runtime.loader import load_domain
from pgs_runtime.scheduler import run_wf


# ---------------------------------------------------------------------------
# Server state (module-level singletons, set before server starts)
# ---------------------------------------------------------------------------

_workspace:    Path = Path(".")
_data_root:    str  = ""
_domain_dirs:  dict[str, Path] = {}         # label → static dir Path
_pkg_cache:    dict[str, Any]  = {}         # domain → RuntimePackage
_pkg_lock:     Lock = Lock()


def _get_pkg(domain: str) -> Any:
    """Load RuntimePackage for domain, cached after first load."""
    with _pkg_lock:
        if domain not in _pkg_cache:
            _pkg_cache[domain] = load_domain(_workspace, domain)
        return _pkg_cache[domain]


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class _PGSHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt: str, *args: Any) -> None:  # type: ignore[override]
        sys.stderr.write(f"[pgs_server] {fmt % args}\n")

    # ── GET ──────────────────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]

        if path in ("/", ""):
            self._serve_landing()
            return

        # Try to match /<domain_label>/rest-of-path
        parts = path.lstrip("/").split("/", 1)
        label = parts[0]
        rel   = parts[1] if len(parts) > 1 else "index.html"

        if label in _domain_dirs:
            self._serve_static(_domain_dirs[label], rel or "index.html")
        else:
            self._respond(404, "text/plain", b"Not found")

    def _serve_landing(self) -> None:
        domain_links = "\n".join(
            f'<a href="/{label}/" class="domain-card">'
            f'<span class="domain-icon">&#9654;</span>'
            f'<span class="domain-name">{label}</span>'
            f'</a>'
            for label in sorted(_domain_dirs)
        )
        body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PGS Runtime Gateway</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
      background: #0a0e1a;
      color: #c9d1e0;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 2rem;
    }}
    .container {{
      max-width: 720px;
      width: 100%;
      text-align: center;
    }}
    .badge {{
      display: inline-block;
      background: linear-gradient(135deg, #1e3a5f, #0d2137);
      border: 1px solid #2a5a8c;
      border-radius: 6px;
      padding: 0.3rem 0.8rem;
      font-size: 0.75rem;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: #5ba3d9;
      margin-bottom: 1.5rem;
    }}
    h1 {{
      font-size: 2.4rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      color: #e8edf5;
      line-height: 1.15;
      margin-bottom: 0.5rem;
    }}
    h1 span {{
      background: linear-gradient(90deg, #4a9edd, #7ec8e3);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }}
    .subtitle {{
      font-size: 1rem;
      color: #7a8ba0;
      margin-bottom: 2.5rem;
      line-height: 1.6;
    }}
    .domains-label {{
      font-size: 0.7rem;
      text-transform: uppercase;
      letter-spacing: 0.15em;
      color: #4a6080;
      margin-bottom: 1rem;
    }}
    .domains {{
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      margin-bottom: 2.5rem;
    }}
    .domain-card {{
      display: flex;
      align-items: center;
      gap: 1rem;
      background: #0f1829;
      border: 1px solid #1e3050;
      border-radius: 8px;
      padding: 1rem 1.4rem;
      text-decoration: none;
      color: #c9d1e0;
      transition: border-color 0.18s, background 0.18s;
      text-align: left;
    }}
    .domain-card:hover {{
      border-color: #4a9edd;
      background: #121e30;
      color: #e8edf5;
    }}
    .domain-icon {{
      color: #4a9edd;
      font-size: 0.65rem;
      flex-shrink: 0;
    }}
    .domain-name {{
      font-size: 0.95rem;
      font-weight: 500;
      letter-spacing: 0.02em;
    }}
    .api-section {{
      background: #0f1829;
      border: 1px solid #1e3050;
      border-radius: 8px;
      padding: 1.2rem 1.4rem;
      text-align: left;
    }}
    .api-label {{
      font-size: 0.7rem;
      text-transform: uppercase;
      letter-spacing: 0.15em;
      color: #4a6080;
      margin-bottom: 0.5rem;
    }}
    .api-endpoint {{
      font-family: 'Consolas', 'SF Mono', 'Fira Code', monospace;
      font-size: 0.85rem;
      color: #7ec8e3;
    }}
    .api-desc {{
      font-size: 0.8rem;
      color: #5a6b80;
      margin-top: 0.25rem;
    }}
    .footer {{
      margin-top: 2rem;
      font-size: 0.72rem;
      color: #2a3a50;
      letter-spacing: 0.05em;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="badge">Protocol-Governed Systems</div>
    <h1>PGS <span>Runtime</span> Gateway</h1>
    <p class="subtitle">Token-native execution substrate &mdash; snapshot-driven, trace-producing, zero-inference.</p>
    <div class="domains-label">Registered Domains</div>
    <div class="domains">
      {domain_links}
    </div>
    <div class="api-section">
      <div class="api-label">Workflow API</div>
      <div class="api-endpoint">POST /api/run</div>
      <div class="api-desc">Execute any registered workflow. Body: {{"workflow_code": "domain::WF_CODE_V0", "payload": {{...}}}}</div>
    </div>
    <div class="footer">pgs_runtime &nbsp;&bull;&nbsp; v0.3.0</div>
  </div>
</body>
</html>""".encode()
        self._respond(200, "text/html; charset=utf-8", body)

    def _serve_static(self, static_dir: Path, rel_path: str) -> None:
        # Prevent path traversal
        try:
            target = (static_dir / rel_path).resolve()
            static_dir.resolve()
        except Exception:
            self._respond(400, "text/plain", b"Bad path")
            return

        if not str(target).startswith(str(static_dir.resolve())):
            self._respond(403, "text/plain", b"Forbidden")
            return

        if target.is_dir():
            target = target / "index.html"

        if not target.exists():
            self._respond(404, "text/plain", b"Not found")
            return

        mime, _ = mimetypes.guess_type(str(target))
        mime = mime or "application/octet-stream"
        self._respond(200, mime, target.read_bytes())

    # ── POST ─────────────────────────────────────────────────────────────────

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/api/run":
            self._handle_run()
        else:
            self._respond(404, "text/plain", b"Not found")

    def _handle_run(self) -> None:
        # Read body
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self._respond_json(400, {"error": "Request body must be valid JSON"})
            return

        wf_fqdn = body.get("workflow_code", "")
        payload = body.get("payload", {})

        if not wf_fqdn or "::" not in wf_fqdn:
            self._respond_json(400, {"error": f"Invalid workflow_code: {wf_fqdn!r}"})
            return
        if not isinstance(payload, dict):
            self._respond_json(400, {"error": "payload must be a JSON object"})
            return

        domain = wf_fqdn.split("::")[0]

        # Load snapshot (cached)
        try:
            pkg = _get_pkg(domain)
        except Exception as exc:
            self._respond_json(500, {
                "status": "ERROR",
                "error": f"Snapshot load failed for domain {domain!r}: {exc}",
            })
            return

        # Resolve WF address
        try:
            wf_addr = pkg.vocab.addr(wf_fqdn)
        except KeyError:
            self._respond_json(400, {"error": f"WF not found in vocab: {wf_fqdn!r}"})
            return

        # Build trace writer
        trace_id  = make_trace_id(domain, wf_fqdn, payload)
        wf_code   = wf_fqdn.split("::")[-1]
        trace_dir = _workspace / "traces" / domain / wf_code / trace_id
        trace_dir.mkdir(parents=True, exist_ok=True)

        writer = TraceWriter(
            trace_dir=trace_dir,
            trace_id=trace_id,
            domain=domain,
            wf_addr=wf_addr,
            wf_fqdn=wf_fqdn,
        )

        # Execute
        t0 = time.monotonic()
        try:
            result_status, surface = run_wf(
                wf_fqdn=wf_fqdn,
                payload=payload,
                pkg=pkg,
                writer=writer,
                data_root=_data_root,
            )
        except Exception as exc:
            writer.error(str(exc))
            writer.close()
            self._respond_json(500, {
                "status": "ERROR",
                "error": str(exc),
                "trace_id": trace_id,
                "workflow_code": wf_fqdn,
            })
            return
        finally:
            writer.close()

        duration_ms = int((time.monotonic() - t0) * 1000)

        envelope = {
            "status":         result_status,
            "trace_id":       trace_id,
            "duration_ms":    duration_ms,
            "workflow_code":  wf_fqdn,
            "result_payload": surface,
        }
        self._respond_json(200, envelope)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _respond(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _respond_json(self, code: int, data: dict) -> None:
        body = json.dumps(data, separators=(",", ":")).encode()
        self._respond(code, "application/json; charset=utf-8", body)


# ---------------------------------------------------------------------------
# CLI entry point (python -m pgs_runtime.server)
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="pgs_runtime.server",
        description="PGS HTTP gateway — serves static UIs and executes workflows",
    )
    p.add_argument("--port",       type=int, default=8000)
    p.add_argument("--workspace",  required=True, metavar="PATH")
    p.add_argument("--data-root",  dest="data_root", required=True, metavar="PATH")
    p.add_argument(
        "--domain",
        action="append",
        default=[],
        metavar="LABEL=PATH",
        help='Register a domain UI: e.g. --domain "blockchain=/abs/path/to/static"',
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    global _workspace, _data_root, _domain_dirs

    _workspace = Path(args.workspace)
    if not _workspace.is_absolute():
        print(f"[pgs_server] --workspace must be absolute: {args.workspace}", file=sys.stderr)
        sys.exit(1)

    _data_root = args.data_root
    if not Path(_data_root).is_absolute():
        print(f"[pgs_server] --data-root must be absolute: {args.data_root}", file=sys.stderr)
        sys.exit(1)

    for entry in args.domain:
        if "=" not in entry:
            print(f"[pgs_server] Bad --domain entry (expected LABEL=PATH): {entry!r}", file=sys.stderr)
            sys.exit(1)
        label, _, path = entry.partition("=")
        static_dir = Path(path)
        if not static_dir.is_dir():
            print(f"[pgs_server] Static dir not found for {label!r}: {path}", file=sys.stderr)
            sys.exit(1)
        _domain_dirs[label.strip()] = static_dir

    print(f"[pgs_server] Workspace : {_workspace}")
    print(f"[pgs_server] Data root : {_data_root}")
    for label, d in sorted(_domain_dirs.items()):
        print(f"[pgs_server] Domain    : {label} → {d}")
    print(f"[pgs_server] Listening on http://0.0.0.0:{args.port}")
    print()

    server = HTTPServer(("0.0.0.0", args.port), _PGSHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[pgs_server] Stopped.")


if __name__ == "__main__":
    main()

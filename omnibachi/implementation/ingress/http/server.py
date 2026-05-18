"""
server.py — Multi-Domain HTTP REST Transport Server

Governed by: CONSTITUTION_TRANSPORT_V0

Routes:
  GET  /              -> domain selector page
  GET  /{domain}/     -> domain's index.html
  GET  /{domain}/*    -> domain's static files
  POST /api/run       -> universal workflow execution
  POST /api/v0/...    -> direct TI-registered routes

Run:
  python -m omnibachi.implementation.ingress.http.server \
    --port 8000 \
    --workspace /Users/bp/pgs_workspace \
    --data-root /Users/bp/pgs_data \
    --domain blockchain=/abs/path/testbed/static
"""

import http.server
import socketserver
import sys
import argparse
from pathlib import Path
from urllib.parse import unquote

from omnibachi.implementation.ingress.http.ingress_adapter import handle_run, handle_direct_route
from omnibachi.implementation.ingress.http.route_registry import load_routes_from_snapshot, get_workflow_for_route

PORT = 8000

_STATIC_DIRS: dict[str, Path] = {}
_SNAPSHOT_ROOT: Path | None = None
_DATA_ROOT: Path | None = None
_TRACE_ROOT: Path | None = None
_ROUTES: dict[tuple[str, str], str] = {}


def _build_domain_selector_html() -> str:
    cards = ""
    for i, name in enumerate(sorted(_STATIC_DIRS.keys()), 1):
        label = name.replace("_", " ").title()
        cards += f'''
        <a href="/{name}/" class="card">
            <span class="step-number">{i}</span>
            <h3>{label}</h3>
            <p>Open the {label} testbed application.</p>
        </a>'''

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OmniBachi — Domain Selector</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: Arial, Helvetica, sans-serif; background: #f4f6f9; color: #333; min-height: 100vh; }}
        .app-header {{ background: #0b1e3c; padding: 16px 0; border-bottom: 3px solid #d4af37; }}
        .header-inner {{ display: flex; align-items: center; justify-content: center; max-width: 560px; margin: 0 auto; padding: 0 20px; }}
        .app-title {{ color: white; font-size: 24px; font-weight: bold; letter-spacing: 1px; }}
        .card-grid {{ display: flex; flex-direction: column; gap: 20px; max-width: 560px; margin: 48px auto; padding: 0 20px; }}
        .card {{ background: white; border-radius: 8px; padding: 28px 32px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); text-decoration: none; color: inherit; transition: box-shadow 0.2s, transform 0.15s; display: block; }}
        .card:hover {{ box-shadow: 0 6px 20px rgba(0,0,0,0.12); transform: translateY(-2px); }}
        .card .step-number {{ display: inline-block; background: #0b1e3c; color: #d4af37; width: 32px; height: 32px; border-radius: 50%; text-align: center; line-height: 32px; font-weight: bold; font-size: 14px; margin-right: 12px; }}
        .card h3 {{ display: inline; font-size: 18px; color: #0b1e3c; }}
        .card p {{ margin-top: 8px; color: #64748b; font-size: 14px; line-height: 1.5; }}
        .app-footer {{ text-align: center; padding: 24px; color: #94a3b8; font-size: 13px; }}
    </style>
</head>
<body>
    <header class="app-header">
        <div class="header-inner">
            <h1 class="app-title">OmniBachi — Domain Selector</h1>
        </div>
    </header>
    <div class="card-grid">{cards}
    </div>
    <footer class="app-footer">
        OmniBachi Thin Client Transport v0.2 — Protocol-Governed Systems
    </footer>
</body>
</html>"""


class OmniBachiRequestHandler(http.server.SimpleHTTPRequestHandler):

    def translate_path(self, path):
        path = path.split("?", 1)[0].split("#", 1)[0]
        path = unquote(path)
        parts = [p for p in path.split("/") if p and p != ".."]

        if parts and parts[0] in _STATIC_DIRS:
            result = _STATIC_DIRS[parts[0]]
            for part in parts[1:]:
                result = result / part
            return str(result)

        return super().translate_path(path)

    def do_GET(self):
        if self.path == "/" or self.path == "":
            html = _build_domain_selector_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html.encode())))
            self.end_headers()
            self.wfile.write(html.encode())
            return

        stripped = self.path.strip("/")
        if stripped in _STATIC_DIRS and not self.path.endswith("/"):
            self.send_response(301)
            self.send_header("Location", f"/{stripped}/")
            self.end_headers()
            return

        parts = [p for p in self.path.split("/") if p]
        if len(parts) == 1 and parts[0] in _STATIC_DIRS:
            self.path = f"/{parts[0]}/index.html"

        if self.path.startswith("/api/"):
            self.send_error(405, "Method Not Allowed", "Use POST for API execution")
            return

        return super().do_GET()

    def do_POST(self):
        if self.path == "/api/run":
            handle_run(self, _SNAPSHOT_ROOT, _DATA_ROOT, _TRACE_ROOT)
            return

        workflow_fqdn = get_workflow_for_route(self.command, self.path, _ROUTES)
        if workflow_fqdn:
            handle_direct_route(self, workflow_fqdn, _SNAPSHOT_ROOT, _DATA_ROOT, _TRACE_ROOT)
            return

        self.send_error(404, "Not Found")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        sys.stderr.write("%s - - [%s] %s\n" %
                         (self.client_address[0],
                          self.log_date_time_string(),
                          format % args))


def run(port: int, domains: dict[str, str], snapshot_root: Path, data_root: Path, trace_root: Path) -> None:
    global _STATIC_DIRS, _SNAPSHOT_ROOT, _DATA_ROOT, _TRACE_ROOT, _ROUTES

    _SNAPSHOT_ROOT = snapshot_root
    _DATA_ROOT = data_root
    _TRACE_ROOT = trace_root
    _STATIC_DIRS.update({name: Path(path) for name, path in domains.items()})
    _ROUTES = load_routes_from_snapshot(snapshot_root)

    with socketserver.ThreadingTCPServer(("", port), OmniBachiRequestHandler) as httpd:
        print(f"OmniBachi HTTP Transport serving at http://localhost:{port}")
        for name, path in _STATIC_DIRS.items():
            print(f"  Domain {name} → {path.resolve()}")
        print(f"  Snapshot root: {_SNAPSHOT_ROOT}")
        print(f"  Data root:     {_DATA_ROOT}")
        print(f"  Trace root:    {_TRACE_ROOT}")
        print(f"Direct routes ({len(_ROUTES)} from snapshot):")
        for (method, path), wf_fqdn in sorted(_ROUTES.items()):
            print(f"  {method} {path} -> {wf_fqdn}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down...")


def main() -> None:
    import os

    parser = argparse.ArgumentParser(description="OmniBachi HTTP Transport")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--domain", type=str, action="append", dest="domains", metavar="NAME=PATH")
    parser.add_argument("--workspace", dest="workspace", default=None,
                        help="pgs_workspace root (or PGS_WORKSPACE). "
                             "snapshot={workspace}/protocol_snapshot, traces={workspace}/traces")
    parser.add_argument("--data-root", dest="data_root", default=None,
                        help="CS domain state root (or PGS_DATA_ROOT)")
    args = parser.parse_args()

    workspace_str = args.workspace or os.environ.get("PGS_WORKSPACE")
    if not workspace_str:
        parser.error("--workspace or PGS_WORKSPACE required")
    workspace = Path(workspace_str).expanduser().resolve()

    data_root_str = args.data_root or os.environ.get("PGS_DATA_ROOT")
    if not data_root_str:
        parser.error("--data-root or PGS_DATA_ROOT required")

    domain_map: dict[str, str] = {}
    for d in (args.domains or []):
        if "=" not in d:
            parser.error(f"Invalid --domain format: {d!r}. Expected NAME=PATH")
        name, path = d.split("=", 1)
        domain_map[name.strip()] = str(Path(path.strip()).expanduser().resolve())

    run(
        port=args.port,
        domains=domain_map,
        snapshot_root=workspace / "protocol_snapshot",
        data_root=Path(data_root_str).expanduser().resolve(),
        trace_root=workspace / "traces",
    )


if __name__ == "__main__":
    main()

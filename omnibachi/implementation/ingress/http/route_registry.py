"""
route_registry.py — Snapshot-driven HTTP route loader.

Governed by: CONSTITUTION_TRANSPORT_V0

Routes are declared as TI_ artifacts in the protocol snapshot.
This module loads them at server startup — no hardcoded routes.

TI_ artifacts live in: {snapshot_root}/artifacts/ingress_intents/*.json
Each TI_ artifact declares:
  frontmatter.core.route.method  — HTTP method
  frontmatter.core.route.path    — HTTP path
  frontmatter.core.workflow      — target workflow FQDN
"""

import json
from pathlib import Path


def load_routes_from_snapshot(snapshot_root: Path) -> dict[tuple[str, str], str]:
    """
    Build HTTP route → workflow FQDN map from compiled TI_ artifacts.

    Reads all *.json files in {snapshot_root}/artifacts/ingress_intents/.
    Extracts route.method, route.path, and workflow from each TI_ artifact.

    Returns:
        {(METHOD, path): workflow_fqdn}
    """
    ingress_dir = snapshot_root / "artifacts" / "ingress_intents"
    routes: dict[tuple[str, str], str] = {}

    if not ingress_dir.exists():
        return routes

    for artifact_path in sorted(ingress_dir.glob("*.json")):
        try:
            with open(artifact_path) as f:
                artifact = json.load(f)

            core = artifact.get("frontmatter", {}).get("core", {})
            route = core.get("route", {})
            method = route.get("method", "").upper()
            path = route.get("path", "")
            workflow = core.get("workflow", "")

            if method and path and workflow:
                routes[(method, path)] = workflow

        except (json.JSONDecodeError, KeyError, TypeError):
            # Malformed artifact — skip; build-time validation is authoritative
            continue

    return routes


def get_workflow_for_route(
    method: str,
    path: str,
    routes: dict[tuple[str, str], str],
) -> str | None:
    """Look up workflow FQDN for the given HTTP method and path."""
    return routes.get((method.upper(), path))

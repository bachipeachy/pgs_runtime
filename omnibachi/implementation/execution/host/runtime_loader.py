"""
runtime_loader.py — Runtime Binding Loader.

Governed by: CONSTITUTION_EXECUTION_V0

Loads RB artifacts, instantiates CS runtimes from compile-time-sealed
handler_ref in cs_ir, and returns a CapabilityRouter.
"""

import importlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from omnibachi.implementation.execution.machine.capability.capability_router import CapabilityRouter


class RuntimeBindingError(Exception):
    """Raised when host bindings cannot be resolved."""


class RuntimeLoader:
    """Loads RB artifact and instantiates capability router."""

    def __init__(
        self,
        rb_path: Path,
        snapshot_root: Path | None = None,
        module_name: str | None = None,
        module_data_root: str | None = None
    ):
        if not rb_path.exists():
            raise RuntimeBindingError(f"RB artifact not found: {rb_path}")
        self.rb_path = rb_path
        self.snapshot_root = snapshot_root
        self.module_name = module_name
        self.module_data_root = module_data_root

    def load(self) -> CapabilityRouter:
        rb = self._load_rb_json()

        frontmatter = rb.get("frontmatter")
        if not frontmatter or "core" not in frontmatter:
            raise RuntimeBindingError("RB artifact missing frontmatter.core")
        core = frontmatter["core"]
        bindings = core.get("bindings")
        if not bindings:
            raise RuntimeBindingError("RB artifact defines no bindings")

        storage_structure_artifact = None
        storage_structure_fqdn = core.get("storage_structure")
        if storage_structure_fqdn:
            storage_structure_artifact = self._load_structure_artifact(storage_structure_fqdn)

        runtimes = {}
        for cs_fqdn, binding in bindings.items():
            runtimes[cs_fqdn] = self._instantiate_cs_binding(cs_fqdn, binding, storage_structure_artifact)

        return CapabilityRouter(runtimes)

    def _load_rb_json(self) -> dict[str, Any]:
        content = self.rb_path.read_text(encoding="utf-8")

        def replace_param(match):
            param_name = match.group(1)
            if param_name in ("runtime_data_root", "module_data_root"):
                if not self.module_data_root:
                    raise RuntimeBindingError(
                        f"RuntimeLoader requires module_data_root to resolve {param_name}"
                    )
                return self.module_data_root
            if param_name in ("smtp_host", "smtp_user", "smtp_pass", "smtp_sender"):
                return os.environ.get(param_name.upper(), "UNSET")
            if param_name == "execution_engine_ref":
                return "internal_workflow_engine"
            raise RuntimeBindingError(f"Unsupported parameter in RB: {param_name}")

        content = re.sub(r"\{\{([^}]+)\}\}", replace_param, content)
        return json.loads(content)

    def _load_structure_artifact(self, fqdn: str) -> dict[str, Any]:
        if not self.snapshot_root:
            self.snapshot_root = self.rb_path.parent.parent.parent

        encoded = fqdn.replace("::", "__")
        artifact_path = self.snapshot_root / "artifacts" / "structures" / f"{encoded}.json"

        if not artifact_path.exists():
            raise RuntimeBindingError(
                f"STRUCTURE artifact not found for '{fqdn}': {artifact_path}"
            )

        with artifact_path.open(encoding="utf-8") as f:
            return json.load(f)

    def _load_cs_artifact(self, cs_fqdn: str) -> dict[str, Any]:
        if not self.snapshot_root:
            self.snapshot_root = self.rb_path.parent.parent.parent

        encoded = cs_fqdn.replace("::", "__")
        artifact_path = self.snapshot_root / "artifacts" / "capability_side_effects" / f"{encoded}.json"

        if not artifact_path.exists():
            raise RuntimeBindingError(
                f"CS artifact not found for '{cs_fqdn}': {artifact_path}"
            )

        with artifact_path.open(encoding="utf-8") as f:
            return json.load(f)

    def _instantiate_cs_binding(
        self,
        cs_fqdn: str,
        binding: dict[str, Any],
        storage_structure_artifact: dict[str, Any] | None = None,
    ):
        policy = dict(binding.get("policy") or {})

        if storage_structure_artifact is not None:
            policy["storage_structure_artifact"] = storage_structure_artifact
        if self.module_data_root is not None:
            policy["module_data_root"] = self.module_data_root

        cs_artifact = self._load_cs_artifact(cs_fqdn)
        cs_ir = cs_artifact.get("cs_ir")
        if not cs_ir:
            raise RuntimeBindingError(
                f"CS artifact '{cs_fqdn}' missing cs_ir — rebuild required"
            )

        handler_ref = cs_ir.get("handler_ref")
        if not handler_ref or not handler_ref.get("module") or not handler_ref.get("callable"):
            raise RuntimeBindingError(
                f"CS artifact '{cs_fqdn}' has incomplete handler_ref: {handler_ref}"
            )

        cs_metadata = cs_ir.get("cs_metadata", {})
        cs_code = cs_fqdn

        mod = importlib.import_module(handler_ref["module"])
        cls = getattr(mod, handler_ref["callable"])
        return cls(config=policy, metadata=cs_metadata, capability_code=cs_code)

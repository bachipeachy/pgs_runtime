"""
loader.py — Token-native snapshot loader for the pgs_runtime.

Reads the tokenized_snapshot package for a given domain/structure, verifies
the topology hash against the trust attestation, and returns a frozen
RuntimePackage dataclass.

The loader is the only file that touches the filesystem. Everything above
it (dispatcher, scheduler, memory, evidence) works only against the frozen
RuntimePackage.

Consumed snapshot roots:
    tokenized_snapshot/<domain>/dispatch.json     — routing (per-WF), pipeline, entry, bindings
    tokenized_snapshot/<domain>/handlers.json     — ct, cs, rb_policy
    tokenized_snapshot/<domain>/metadata.json     — projection_hash for trust verification
    trust_snapshot/<domain>/structure_attestation.json — tokenized_projection_hash
    vocabulary_snapshot/<domain>/forward.json     — int_addr_hex → FQDN
    vocabulary_snapshot/<domain>/reverse.json     — FQDN → int_addr_hex

Verification contract:
    metadata.json[projection_hash] == structure_attestation.json[tokenized_projection_hash]
    Mismatch → hard failure. No silent skip, no fallback.

Address conversion:
    Vocabulary files use hex strings ("0x0035"). The runtime works with ints.
    forward: {int → FQDN}  (converted on load)
    reverse: {FQDN → int}  (converted on load)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Frozen dataclasses — the RuntimePackage and its sub-tables
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DispatchTable:
    """
    Integer-keyed routing substrate from dispatch.json.

    routing:  {wf_addr: {cc_addr: {condition_addr: next_cc_addr}}}
    pipeline: {cc_addr: [step, ...]}
    entry:    {wf_addr: {"start": cc_addr, "rb": rb_addr, "in": in_addr}}
    bindings: {wf_addr: {cc_addr: {input_name: path_or_literal}}}

    Each pipeline step is a named-field execution instruction record:
        {
            "addr":      int,        # CT or CS integer address
            "op":        str|None,   # null for CT, operation name for CS
            "inputs":    dict|None,  # resolved input bindings ($.inputs.X, $.results.step_id.X, literals)
            "outputs":   dict|None,  # surface mapping for CT steps ({surface_name: "$.capability_result.field"})
            "on_result": dict|None,  # continuation semantics ({"SUCCESS": "continue", "VIOLATION": "exit"})
            "step_id":   str,        # symbolic step name for $.results.<step_id>.<field> references
        }

    All semantics are compiler-materialized. The dispatcher is a blind executor.
    All dict keys are ints. All address values are ints.
    The nested dicts are plain Python dicts (not frozen) — callers must not mutate.
    """
    routing:  dict[int, dict[int, dict[int, int]]]
    pipeline: dict[int, list[dict]]
    entry:    dict[int, dict[str, int]]
    bindings: dict[int, dict[int, dict[str, Any]]]


@dataclass(frozen=True)
class HandlersTable:
    """
    Implementation dispatch table from handlers.json.

    ct:        {ct_addr: {"ct_ir": {...}}}
    cs:        {cs_addr: {"handler_ref": {...}, "cs_metadata": {...}}}
    rb_policy: {rb_addr: {cs_addr: policy_config}}

    All dict keys are ints.
    """
    ct:        dict[int, dict[str, Any]]
    cs:        dict[int, dict[str, Any]]
    rb_policy: dict[int, dict[int, Any]]


@dataclass(frozen=True)
class VocabIndex:
    """
    Bidirectional FQDN ↔ integer address index from vocabulary_snapshot.

    forward: {int_addr → FQDN}
    reverse: {FQDN → int_addr}
    """
    forward: dict[int, str]
    reverse: dict[str, int]

    def fqdn(self, addr: int) -> str:
        """Resolve integer address to FQDN. Returns hex string if not found."""
        return self.forward.get(addr, f"0x{addr:04X}")

    def addr(self, fqdn: str) -> int:
        """Resolve FQDN to integer address. Raises KeyError if not found."""
        if fqdn not in self.reverse:
            raise KeyError(f"FQDN not in vocab: {fqdn!r}")
        return self.reverse[fqdn]


@dataclass(frozen=True)
class RuntimePackage:
    """
    Complete frozen runtime substrate for one domain/structure.

    Produced by load_domain(). All fields are read-only after construction.
    The runtime (scheduler, dispatcher, evidence) operates exclusively against
    this object — no further filesystem access.
    """
    domain:    str
    dispatch:  DispatchTable
    handlers:  HandlersTable
    vocab:     VocabIndex


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_domain(workspace_root: str | Path, domain: str) -> RuntimePackage:
    """
    Load and verify the tokenized snapshot for a domain.

    Args:
        workspace_root: Absolute path to pgs_workspace root.
        domain:         Domain/structure identifier (e.g. "blockchain").

    Returns:
        Frozen RuntimePackage ready for execution.

    Raises:
        FileNotFoundError: Required snapshot file is missing.
        RuntimeError:      Hash verification fails (tampered or stale snapshot).
        ValueError:        Malformed snapshot content.
    """
    root = Path(workspace_root)

    tok_dir   = root / "tokenized_snapshot" / domain
    trust_dir = root / "trust_snapshot"     / domain
    vocab_dir = root / "vocabulary_snapshot" / domain

    # --- Load raw JSON files ---
    dispatch_raw  = _load_json(tok_dir   / "dispatch.json")
    handlers_raw  = _load_json(tok_dir   / "handlers.json")
    metadata_raw  = _load_json(tok_dir   / "metadata.json")
    attestation   = _load_json(trust_dir / "structure_attestation.json")
    forward_raw   = _load_json(vocab_dir / "forward.json")
    reverse_raw   = _load_json(vocab_dir / "reverse.json")

    # --- Trust verification ---
    _verify_hash(
        actual   = metadata_raw.get("projection_hash", ""),
        expected = attestation.get("tokenized_projection_hash", ""),
        domain   = domain,
    )

    # --- Build DispatchTable ---
    dispatch = _build_dispatch(dispatch_raw)

    # --- Build HandlersTable ---
    handlers = _build_handlers(handlers_raw)

    # --- Build VocabIndex ---
    vocab = _build_vocab(forward_raw, reverse_raw)

    return RuntimePackage(
        domain   = domain,
        dispatch = dispatch,
        handlers = handlers,
        vocab    = vocab,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Required snapshot file missing: {path}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _verify_hash(actual: str, expected: str, domain: str) -> None:
    if not actual or not expected:
        raise RuntimeError(
            f"[{domain}] Trust verification failed: "
            f"projection_hash or tokenized_projection_hash is empty."
        )
    if actual != expected:
        raise RuntimeError(
            f"[{domain}] Snapshot integrity failure: "
            f"metadata.projection_hash={actual!r} "
            f"!= attestation.tokenized_projection_hash={expected!r}"
        )


def _build_dispatch(raw: dict) -> DispatchTable:
    """
    Parse dispatch.json into integer-keyed DispatchTable.

    JSON keys are strings (JSON spec). Addresses are hex-string or int values.
    All converted to int on load.
    """
    routing: dict[int, dict[int, dict[int, int]]] = {}
    for wf_key, cc_map in raw.get("routing", {}).items():
        wf_addr = int(wf_key)
        routing[wf_addr] = {
            int(cc_key): {int(cond): int(tgt) for cond, tgt in cond_map.items()}
            for cc_key, cond_map in cc_map.items()
        }

    pipeline: dict[int, list[dict]] = {}
    for cc_key, steps in raw.get("pipeline", {}).items():
        cc_addr = int(cc_key)
        pipeline[cc_addr] = steps  # list of named-field step dicts

    entry: dict[int, dict[str, int]] = {}
    for wf_key, e in raw.get("entry", {}).items():
        wf_addr = int(wf_key)
        entry[wf_addr] = {k: int(v) for k, v in e.items()}

    bindings: dict[int, dict[int, dict[str, Any]]] = {}
    for wf_key, cc_map in raw.get("bindings", {}).items():
        wf_addr = int(wf_key)
        bindings[wf_addr] = {int(cc_key): inp for cc_key, inp in cc_map.items()}

    return DispatchTable(
        routing  = routing,
        pipeline = pipeline,
        entry    = entry,
        bindings = bindings,
    )


def _build_handlers(raw: dict) -> HandlersTable:
    """
    Parse handlers.json into integer-keyed HandlersTable.
    """
    ct: dict[int, dict[str, Any]] = {
        int(k): v for k, v in raw.get("ct", {}).items()
    }
    cs: dict[int, dict[str, Any]] = {
        int(k): v for k, v in raw.get("cs", {}).items()
    }
    rb_policy: dict[int, dict[int, Any]] = {}
    for rb_key, cs_map in raw.get("rb_policy", {}).items():
        rb_addr = int(rb_key)
        rb_policy[rb_addr] = {int(cs_key): policy for cs_key, policy in cs_map.items()}

    return HandlersTable(ct=ct, cs=cs, rb_policy=rb_policy)


def _build_vocab(forward_raw: dict, reverse_raw: dict) -> VocabIndex:
    """
    Build bidirectional vocab index.

    forward.json keys are hex strings like "0x0035" → FQDN string.
    reverse.json keys are FQDN strings → hex string like "0x0035".

    Both are converted to int ↔ FQDN mappings.
    """
    forward: dict[int, str] = {}
    for hex_key, fqdn in forward_raw.items():
        forward[int(hex_key, 16)] = fqdn

    reverse: dict[str, int] = {}
    for fqdn, hex_val in reverse_raw.items():
        reverse[fqdn] = int(hex_val, 16)

    return VocabIndex(forward=forward, reverse=reverse)

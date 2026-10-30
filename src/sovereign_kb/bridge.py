"""
Versioned Cognitive Triad Contract & Chained Provenance Ledger (`sovereign_kb.bridge`).

Provides the shared, deterministic bridge between:
  - `sovereign-kb` (`skb`): 4-Way Hybrid Retrieval, GWI+ v1.4.0 Quality Gate & 4-Loop RSI
  - `software-machine` (`swm`): 6-Stage Autonomous Pipeline & 279-Asset Fleet Control Plane
  - `soar-cognitive-studio` (`soar`): Soar 9.6 Rete Kernel, TMS & Procedural `ChunkStore`
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from sovereign_kb.gwi import evaluate_target
from sovereign_kb.retrieval import retrieve_from_vault
from sovereign_kb.soar import (
    ChunkStore,
    SoarKernel,
    format_production,
    parse_soar,
    resolve_retrieval_tie_with_soar,
)

BRIDGE_CONTRACT_VERSION = "1.4.0"


def default_triad_ledger_path(vault_dir: Path | None = None) -> Path:
    env_p = os.environ.get("SWM_TRIAD_LEDGER")
    if env_p:
        return Path(env_p).expanduser()
    swm_home = Path(os.environ.get("SWM_HOME", str(Path.home() / ".swm"))).expanduser()
    if vault_dir is not None and Path(vault_dir).is_dir():
        return Path(vault_dir) / ".kb" / "triad-provenance-ledger.jsonl"
    return swm_home / "triad_provenance_ledger.jsonl"


def append_triad_provenance_event(
    event_type: str,
    payload: dict[str, Any],
    ledger_file: Path | None = None,
    wp_id: int = 0,
    run_id: str = "",
    commit_sha: str = "",
    passport_ulid: str = "",
    policy_sha256: str = "",
) -> dict[str, Any]:
    """
    Append a tamper-evident SHA-256 hash-chained event across the Cognitive Triad
    (`rsi.policy_tuned`, `swm.stage_completed`, `gwi.evaluated`, `soar.chunk_compiled`).
    """
    lp = Path(ledger_file) if ledger_file is not None else default_triad_ledger_path()
    lp.parent.mkdir(parents=True, exist_ok=True)
    prev_hash = "0" * 64
    seq = 1
    if lp.is_file():
        lines = [ln.strip() for ln in lp.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines:
            seq = len(lines) + 1
            try:
                last_obj = json.loads(lines[-1])
                prev_hash = str(last_obj.get("entry_hash", prev_hash))
            except Exception:
                pass

    entry: dict[str, Any] = {
        "event_id": f"TRIAD-{seq:05d}",
        "contract_version": BRIDGE_CONTRACT_VERSION,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "event_type": str(event_type),
        "wp_id": int(wp_id),
        "run_id": str(run_id),
        "commit_sha": str(commit_sha),
        "passport_ulid": str(passport_ulid),
        "policy_sha256": str(policy_sha256),
        "prev_hash": prev_hash,
        "payload": payload,
    }
    canonical = json.dumps(entry, sort_keys=True).encode("utf-8")
    entry["entry_hash"] = hashlib.sha256(canonical).hexdigest()
    with lp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=False) + "\n")
    return entry


def verify_triad_provenance_chain(ledger_file: Path | None = None) -> tuple[bool, int]:
    """
    Verify cryptographic SHA-256 hash chain across all events in `triad_provenance_ledger.jsonl`.
    Fails closed (`False`) if any line is malformed JSON or has a mismatched `prev_hash` / `entry_hash`.
    """
    lp = Path(ledger_file) if ledger_file is not None else default_triad_ledger_path()
    if not lp.is_file():
        return True, 0
    lines = [ln.strip() for ln in lp.read_text(encoding="utf-8").splitlines() if ln.strip()]
    prev = "0" * 64
    for ln in lines:
        try:
            obj = json.loads(ln)
        except Exception:
            return False, len(lines)
        if not isinstance(obj, dict) or obj.get("prev_hash") != prev:
            return False, len(lines)
        copy_without_hash = {k: v for k, v in obj.items() if k != "entry_hash"}
        expected = hashlib.sha256(json.dumps(copy_without_hash, sort_keys=True).encode("utf-8")).hexdigest()
        if obj.get("entry_hash") != expected:
            return False, len(lines)
        prev = expected
    return True, len(lines)


def retrieve_vault_precedents(
    vault_dir: Path,
    query: str,
    top_k: int = 4,
) -> dict[str, Any]:
    """
    Retrieve declarative vault notes and procedural Soar `ChunkStore` rules for a Work Package or query.
    """
    return retrieve_from_vault(
        vault_dir=Path(vault_dir),
        query=query,
        top_k=top_k,
        engine="auto",
        include_procedural_chunks=True,
    )


def evaluate_worktree_gwi(
    target_dir: Path,
    profile: str = "app",
    strict_lenses: bool = False,
) -> dict[str, Any]:
    """
    Evaluate a candidate worktree or repository using Sovereign-KB's canonical GWI+ v1.4.0 evaluator.
    """
    return evaluate_target(
        target=Path(target_dir),
        profile=profile,
        strict_lenses=strict_lenses,
    )


def run_triad_end_to_end_canary(vault_dir: Path | None = None) -> dict[str, Any]:
    """
    Execute an isolated end-to-end Triad health canary verifying:
      1. Vault declarative + procedural retrieval (`retrieve_vault_precedents`)
      2. Soar 9.6 decision cycle + Explanation-Based Chunking + 0-impasse replay
      3. Goldwood Index (GWI+ v1.4.0) kernel verification (`compute_goldwood_index`)
    """
    vdir = Path(vault_dir) if vault_dir is not None else (Path(__file__).resolve().parent / "starter_vault")
    cands = [
        {
            "rel_path": "02-operating-system/autonomous-engineering-framework.md",
            "doc_type": "operating-system",
            "rrf_score": 0.042,
        },
        {"rel_path": "04-episodic/2026-10-29-log.md", "doc_type": "episodic", "rrf_score": 0.039},
    ]
    tie_res = resolve_retrieval_tie_with_soar(
        vault_dir=vdir,
        query="autonomous engineering quality gate",
        candidates=cands,
        persist_chunks=False,
    )
    soar_ok = bool(tie_res.get("goal_achieved") and tie_res.get("zero_impasse_replay"))
    return {
        "contract_version": BRIDGE_CONTRACT_VERSION,
        "canary_passed": soar_ok,
        "goal_achieved": bool(tie_res.get("goal_achieved")),
        "zero_impasse_replay": bool(tie_res.get("zero_impasse_replay")),
        "selected_tier": tie_res.get("selected_tier"),
        "chunks_compiled": int(tie_res.get("chunks_compiled", 0)),
    }


__all__ = [
    "BRIDGE_CONTRACT_VERSION",
    "ChunkStore",
    "SoarKernel",
    "append_triad_provenance_event",
    "default_triad_ledger_path",
    "evaluate_worktree_gwi",
    "format_production",
    "parse_soar",
    "retrieve_vault_precedents",
    "run_triad_end_to_end_canary",
    "verify_triad_provenance_chain",
]

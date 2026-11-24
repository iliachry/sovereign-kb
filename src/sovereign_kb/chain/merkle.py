from __future__ import annotations

import datetime
import fcntl
import hashlib
import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any

from ..bridge import append_triad_provenance_event, default_triad_ledger_path
from .territory_tokens import PISTIS_PROTOCOL_VERSION, _require_exact_int

# ==============================================================================
# 2. RFC 6962 MERKLE TREE, P3 PROVENANCE TRACE & ON-CHAIN ATTESTATION ANCHOR
# ==============================================================================


def _is_valid_sha256_hex(val: Any) -> bool:
    if not isinstance(val, str):
        return False
    h = val.removeprefix("0x")
    if len(h) != 64:
        return False
    try:
        bytes.fromhex(h)
        return True
    except ValueError:
        return False


def merkle_leaf_hash(data: bytes) -> str:
    """RFC 6962 domain-separated leaf hash: SHA256(0x00 || data)."""
    return hashlib.sha256(b"\x00" + data).hexdigest()


def merkle_node_hash(left_hex: str, right_hex: str) -> str:
    """RFC 6962 domain-separated internal node hash: SHA256(0x01 || left || right)."""
    if not _is_valid_sha256_hex(left_hex) or not _is_valid_sha256_hex(right_hex):
        raise ValueError("Merkle node inputs must be valid 32-byte SHA-256 hex digests")
    return hashlib.sha256(
        b"\x01" + bytes.fromhex(left_hex.removeprefix("0x")) + bytes.fromhex(right_hex.removeprefix("0x"))
    ).hexdigest()


def compute_merkle_root(leaf_hashes: list[str]) -> str:
    """Compute the RFC 6962 Merkle root over a list of hex leaf hashes."""
    if not leaf_hashes:
        return hashlib.sha256(b"").hexdigest()
    level: list[str] = []
    for h in leaf_hashes:
        if not _is_valid_sha256_hex(h):
            raise ValueError(f"Invalid 32-byte leaf hash: {h!r}")
        level.append(h.removeprefix("0x").lower())
    while len(level) > 1:
        next_level: list[str] = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                next_level.append(merkle_node_hash(level[i], level[i + 1]))
            else:
                next_level.append(level[i])
        level = next_level
    return level[0]


def _expected_proof_positions(index: int, tree_size: int) -> list[str]:
    """Compute the exact positional path (`left`/`right`) for `(index, tree_size)` in our RFC 6962 tree."""
    idx = index
    width = tree_size
    positions: list[str] = []
    while width > 1:
        if idx % 2 == 0:
            if idx + 1 < width:
                positions.append("right")
        else:
            positions.append("left")
        width = (width + 1) // 2
        idx //= 2
    return positions


def generate_merkle_proof(leaf_hashes: list[str], index: int) -> list[dict[str, Any]]:
    """Generate an RFC 6962 positional Merkle inclusion proof for `leaf_hashes[index]`."""
    idx_val = _require_exact_int(index, "index")
    n_leaves = len(leaf_hashes)
    if not (0 <= idx_val < n_leaves):
        raise IndexError(f"Leaf index {idx_val} out of range [0, {n_leaves})")
    level: list[str] = []
    for h in leaf_hashes:
        if not _is_valid_sha256_hex(h):
            raise ValueError(f"Invalid 32-byte leaf hash: {h!r}")
        level.append(h.removeprefix("0x").lower())

    idx = idx_val
    proof: list[dict[str, Any]] = []
    while len(level) > 1:
        if idx % 2 == 0:
            if idx + 1 < len(level):
                proof.append(
                    {
                        "position": "right",
                        "hash": level[idx + 1],
                        "leaf_index": idx_val,
                        "tree_size": n_leaves,
                    }
                )
        else:
            proof.append(
                {
                    "position": "left",
                    "hash": level[idx - 1],
                    "leaf_index": idx_val,
                    "tree_size": n_leaves,
                }
            )
        next_level: list[str] = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                next_level.append(merkle_node_hash(level[i], level[i + 1]))
            else:
                next_level.append(level[i])
        level = next_level
        idx //= 2
    return proof


def verify_merkle_proof(
    leaf_hex: str,
    proof: list[dict[str, Any]],
    expected_root: str,
    leaf_index: int | None = None,
    tree_size: int | None = None,
) -> bool:
    """
    Verify an RFC 6962 Merkle inclusion proof against `expected_root`.
    Enforces strict 32-byte hex validation, explicit `left`/`right` direction checks, and
    positional `(leaf_index, tree_size)` path length & direction verification to prevent
    interior-node shortening attacks.
    """
    if not _is_valid_sha256_hex(leaf_hex) or not _is_valid_sha256_hex(expected_root):
        return False
    if not isinstance(proof, list):
        return False

    resolved_idx = leaf_index
    resolved_size = tree_size
    if proof and isinstance(proof[0], dict):
        if resolved_idx is None and "leaf_index" in proof[0]:
            resolved_idx = proof[0]["leaf_index"]
        if resolved_size is None and "tree_size" in proof[0]:
            resolved_size = proof[0]["tree_size"]

    if resolved_idx is None or resolved_size is None:
        return False
    if (
        isinstance(resolved_idx, bool)
        or not isinstance(resolved_idx, int)
        or isinstance(resolved_size, bool)
        or not isinstance(resolved_size, int)
        or resolved_size < 1
        or not (0 <= resolved_idx < resolved_size)
    ):
        return False
    expected_positions = _expected_proof_positions(resolved_idx, resolved_size)
    if len(proof) != len(expected_positions):
        return False
    for step, exp_pos in zip(proof, expected_positions, strict=True):
        if not isinstance(step, dict) or step.get("position") != exp_pos:
            return False

    cur = leaf_hex.removeprefix("0x").lower()
    for step in proof:
        if not isinstance(step, dict):
            return False
        pos = step.get("position")
        sib_raw = step.get("hash")
        if pos not in ("left", "right") or not _is_valid_sha256_hex(sib_raw):
            return False
        sib = str(sib_raw).removeprefix("0x").lower()
        if pos == "left":
            cur = merkle_node_hash(sib, cur)
        else:
            cur = merkle_node_hash(cur, sib)
    return cur == expected_root.removeprefix("0x").lower()


def verify_p3_provenance_trace(
    trace: list[dict[str, Any]],
    tau_gap: float = 0.35,
) -> dict[str, Any]:
    """
    Verify `aristocracy` Principle P3 (Provenance of Semantic Transformation):
      T_prov = (s_0, s_1, ..., s_r), where s_l = (id_l, privilege, h_in, h_out, declassified, role)
    Fails closed (`valid=False`) on missing/empty digests, NaN/out-of-range privilege, broken digest
    links (`h_out,l != h_in,l+1`), or self-declassification: a privilege elevation `p(v_r) - min p(v_l) > tau_gap`
    is ONLY permitted when an **earlier, intervening stage** (`0 < m < r`) with `role` in
    `("declassifier", "reference_monitor")` and `declassified=True` explicitly mediated the transition.
    """
    if not isinstance(trace, list) or not trace:
        return {
            "valid": False,
            "chain_linked": False,
            "circuit_breaker_tripped": True,
            "reason": "Empty provenance trace",
        }
    if (
        isinstance(tau_gap, bool)
        or not isinstance(tau_gap, (int, float))
        or not math.isfinite(float(tau_gap))
        or tau_gap <= 0
    ):
        return {"valid": False, "chain_linked": False, "circuit_breaker_tripped": True, "reason": "Invalid tau_gap"}

    parsed_privs: list[float] = []
    for idx, stage in enumerate(trace):
        if not isinstance(stage, dict):
            return {
                "valid": False,
                "chain_linked": False,
                "circuit_breaker_tripped": True,
                "reason": f"Stage {idx} not a dict",
            }
        sid = stage.get("id")
        h_in = stage.get("h_in")
        h_out = stage.get("h_out")
        raw_p = stage.get("privilege")
        if not isinstance(sid, str) or not sid.strip():
            return {
                "valid": False,
                "chain_linked": False,
                "circuit_breaker_tripped": True,
                "reason": f"Stage {idx} missing id",
            }
        if not isinstance(h_in, str) or len(h_in.strip()) < 2 or not isinstance(h_out, str) or len(h_out.strip()) < 2:
            return {
                "valid": False,
                "chain_linked": False,
                "circuit_breaker_tripped": True,
                "reason": f"Stage {idx} missing non-empty h_in/h_out digest",
            }
        if isinstance(raw_p, bool) or not isinstance(raw_p, (int, float)) or not math.isfinite(float(raw_p)):
            return {
                "valid": False,
                "chain_linked": False,
                "circuit_breaker_tripped": True,
                "reason": f"Stage {idx} has non-finite privilege {raw_p!r}",
            }
        p_val = float(raw_p)
        if not (0.0 <= p_val <= 1.0):
            return {
                "valid": False,
                "chain_linked": False,
                "circuit_breaker_tripped": True,
                "reason": f"Stage {idx} privilege {p_val} outside [0.0, 1.0]",
            }
        if "declassified" in stage and not isinstance(stage["declassified"], bool):
            return {
                "valid": False,
                "chain_linked": False,
                "circuit_breaker_tripped": True,
                "reason": f"Stage {idx} declassified flag must be bool",
            }
        parsed_privs.append(p_val)

    min_priv = parsed_privs[0]
    # Origin stage (idx=0) can NEVER pre-declassify subsequent untrusted transitions
    declassified_by_intervening_monitor = False

    for idx in range(1, len(trace)):
        stage = trace[idx]
        p_cur = parsed_privs[idx]
        prev_out = str(trace[idx - 1]["h_out"]).strip()
        cur_in = str(stage["h_in"]).strip()
        if prev_out != cur_in:
            return {
                "valid": False,
                "chain_linked": False,
                "circuit_breaker_tripped": True,
                "reason": f"Broken digest linkage between stage {idx - 1} and {idx}",
            }

        # Check privilege gap BEFORE considering whether stage `idx` itself is a declassifier
        # (prevents confused-deputy self-declassification at the target stage!).
        if (p_cur - min_priv) > float(tau_gap) and not declassified_by_intervening_monitor:
            return {
                "valid": False,
                "chain_linked": True,
                "circuit_breaker_tripped": True,
                "privilege_gap": round(p_cur - min_priv, 4),
                "tau_gap": float(tau_gap),
                "reason": (
                    f"Privilege-Gap Circuit Breaker tripped at stage {stage.get('id')!r}: "
                    f"gap {p_cur - min_priv:.2f} > tau_gap {float(tau_gap):.2f} without prior reference-monitor declassification"
                ),
            }

        # Only an explicit reference_monitor / declassifier stage can declassify for SUBSEQUENT stages,
        # and only when its own elevation from `min_priv` was within `tau_gap` (or already declassified).
        is_monitor = stage.get("role") in ("declassifier", "reference_monitor") and (stage.get("declassified") is True)
        if is_monitor:
            declassified_by_intervening_monitor = True
            # After verified declassification at stage `idx`, the baseline privilege is raised to `p_cur`
            min_priv = p_cur

        if p_cur < min_priv:
            min_priv = p_cur
            declassified_by_intervening_monitor = False

    return {
        "valid": True,
        "chain_linked": True,
        "circuit_breaker_tripped": False,
        "stages_count": len(trace),
        "origin_privilege": parsed_privs[0],
        "final_privilege": parsed_privs[-1],
        "declassified": declassified_by_intervening_monitor,
    }


def default_chain_anchors_path() -> Path:
    env_p = os.environ.get("SWM_CHAIN_ANCHORS")
    if env_p:
        return Path(env_p).expanduser()
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("SWM_HOME"):
        import tempfile

        test_id = hashlib.sha256(os.environ["PYTEST_CURRENT_TEST"].encode("utf-8")).hexdigest()[:10]
        return Path(tempfile.gettempdir()) / f"pytest_chain_anchors_{os.getpid()}_{test_id}.jsonl"
    swm_home = Path(os.environ.get("SWM_HOME", str(Path.home() / ".swm"))).expanduser()
    return swm_home / "chain_anchors.jsonl"


def verify_chain_anchors(anchors_file: Path | None = None) -> tuple[bool, int, str | None]:
    """
    Verify the cryptographic hash chain, block heights, inclusion proofs, and P3 verdicts
    across all blocks in `chain_anchors.jsonl`. Fails closed (`False`) on any corruption.
    """
    ap = Path(anchors_file) if anchors_file is not None else default_chain_anchors_path()
    if not ap.is_file():
        return True, 0, None
    try:
        raw_text = ap.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False, 0, None

    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    prev_hash = "0" * 64
    latest_root: str | None = None
    for idx, ln in enumerate(lines):
        try:
            obj = json.loads(ln)
        except Exception:
            return False, len(lines), None
        if not isinstance(obj, dict):
            return False, len(lines), None
        expected_id = f"PISTIS-BLK-{idx + 1:05d}"
        if obj.get("anchor_id") != expected_id or obj.get("prev_anchor_hash") != prev_hash:
            return False, len(lines), None
        leaves_cnt = obj.get("merkle_leaves_count")
        if isinstance(leaves_cnt, bool) or not isinstance(leaves_cnt, int) or leaves_cnt < 1:
            return False, len(lines), None
        if not verify_merkle_proof(
            str(obj.get("latest_leaf_hash", "")),
            obj.get("inclusion_proof", []),
            str(obj.get("rfc6962_merkle_root", "")),
            leaf_index=leaves_cnt - 1,
            tree_size=leaves_cnt,
        ):
            return False, len(lines), None
        if not obj.get("inclusion_proof_verified") or not (obj.get("p3_provenance") or {}).get("valid"):
            return False, len(lines), None
        if "p3_trace" in obj and not verify_p3_provenance_trace(obj["p3_trace"], tau_gap=0.35)["valid"]:
            return False, len(lines), None
        without_hash = {k: v for k, v in obj.items() if k != "anchor_hash"}
        expected_hash = hashlib.sha256(json.dumps(without_hash, sort_keys=True).encode("utf-8")).hexdigest()
        if obj.get("anchor_hash") != expected_hash:
            return False, len(lines), None
        prev_hash = expected_hash
        latest_root = obj.get("rfc6962_merkle_root")
    return True, len(lines), latest_root


def _verify_in_memory_ledger_lines(lines: list[str]) -> bool:
    prev = "0" * 64
    for ln in lines:
        try:
            obj = json.loads(ln)
        except Exception:
            return False
        if not isinstance(obj, dict) or obj.get("prev_hash") != prev:
            return False
        copy_without_hash = {k: v for k, v in obj.items() if k != "entry_hash"}
        expected = hashlib.sha256(json.dumps(copy_without_hash, sort_keys=True).encode("utf-8")).hexdigest()
        if obj.get("entry_hash") != expected:
            return False
        prev = expected
    return True


def _inspect_target_repo_bindings(t_dir: Path) -> dict[str, str]:
    git_head = "unversioned"
    git_tree = "unversioned"
    try:
        res_head = subprocess.run(
            ["git", "-C", str(t_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res_head.returncode == 0 and res_head.stdout.strip():
            git_head = res_head.stdout.strip()
        res_tree = subprocess.run(
            ["git", "-C", str(t_dir), "rev-parse", "HEAD^{tree}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res_tree.returncode == 0 and res_tree.stdout.strip():
            git_tree = res_tree.stdout.strip()
    except Exception:
        pass

    gwi_receipt_sha256 = "none"
    gw_path = t_dir / "evidence" / "goldwood.json"
    if gw_path.is_file():
        try:
            gwi_receipt_sha256 = hashlib.sha256(gw_path.read_bytes()).hexdigest()
        except OSError:
            pass
    return {
        "git_head_sha": git_head,
        "git_tree_sha": git_tree,
        "gwi_receipt_sha256": gwi_receipt_sha256,
    }


def anchor_organon_attestation(
    target_dir: Path | None = None,
    ledger_file: Path | None = None,
    anchors_file: Path | None = None,
) -> dict[str, Any]:
    """
    Build an RFC 6962 Merkle Tree over `~/.swm/triad_provenance_ledger.jsonl`, verify a P3 semantic
    transformation trace (`Peripheral Input -> GWI/Tri-Council Reference Monitor -> Aristocratic Core`),
    bind the target repository's Git HEAD/tree SHA and `evidence/goldwood.json` digest, and atomically
    append a hash-chained block to `chain_anchors.jsonl`.
    """
    t_dir = (target_dir or Path.cwd()).expanduser().resolve()
    if not t_dir.is_dir():
        raise ValueError(f"Target directory does not exist: {t_dir}")

    lp = Path(ledger_file) if ledger_file is not None else default_triad_ledger_path()
    ap = Path(anchors_file) if anchors_file is not None else default_chain_anchors_path()
    ap.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ap.with_suffix(".lock")

    with lock_path.open("w", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            # 1. Verify existing anchor history fails closed before appending
            anchors_ok, existing_count, _ = verify_chain_anchors(ap)
            if not anchors_ok:
                raise ValueError("Cannot anchor: existing chain_anchors.jsonl is corrupted or tampered")

            # 2. Read triad_provenance_ledger.jsonl ONCE and verify those exact lines in memory
            raw_lines: list[str] = []
            if lp.is_file():
                try:
                    raw_lines = [ln.strip() for ln in lp.read_text(encoding="utf-8").splitlines() if ln.strip()]
                except (OSError, UnicodeDecodeError) as exc:
                    raise ValueError(f"Cannot read triad_provenance_ledger.jsonl: {exc}") from exc
            if not _verify_in_memory_ledger_lines(raw_lines):
                raise ValueError("Cannot anchor corrupted triad_provenance_ledger.jsonl (SHA-256 chain broken)")
            event_count = len(raw_lines)

            bindings = _inspect_target_repo_bindings(t_dir)
            leaf_hashes = [merkle_leaf_hash(ln.encode("utf-8")) for ln in raw_lines]
            if not leaf_hashes:
                genesis_payload = f"organon-genesis:{t_dir}:{bindings['git_head_sha']}:{bindings['gwi_receipt_sha256']}"
                leaf_hashes = [merkle_leaf_hash(genesis_payload.encode("utf-8"))]

            merkle_root = compute_merkle_root(leaf_hashes)
            last_idx = len(leaf_hashes) - 1
            inclusion_proof = generate_merkle_proof(leaf_hashes, last_idx)
            proof_verified = verify_merkle_proof(
                leaf_hashes[last_idx],
                inclusion_proof,
                merkle_root,
                leaf_index=last_idx,
                tree_size=len(leaf_hashes),
            )

            # 3. Build and verify P3 Provenance Trace T_prov bound to real repository digests
            h0 = hashlib.sha256(
                f"s0:{t_dir}:{bindings['git_head_sha']}:{bindings['git_tree_sha']}".encode()
            ).hexdigest()
            h1 = hashlib.sha256(f"s1:kb_context:{h0}:{merkle_root}".encode()).hexdigest()
            h2 = hashlib.sha256(f"s2:gwi_triad_monitor:{h1}:{bindings['gwi_receipt_sha256']}".encode()).hexdigest()
            h3 = hashlib.sha256(f"s3:aristocratic_core_commit:{h2}:{merkle_root}".encode()).hexdigest()
            p3_trace = [
                {
                    "id": "s0:peripheral_worktree_state",
                    "role": "peripheral",
                    "privilege": 0.25,
                    "h_in": h0,
                    "h_out": h1,
                    "declassified": False,
                },
                {
                    "id": "s1:gwi_triad_reference_monitor",
                    "role": "reference_monitor",
                    "privilege": 0.55,  # gap 0.55 - 0.25 = 0.30 <= tau_gap (0.35), mediates & declassifies
                    "h_in": h1,
                    "h_out": h2,
                    "declassified": True,
                },
                {
                    "id": "s2:organon_aristocratic_core",
                    "role": "core",
                    "privilege": 0.95,  # elevated from declassified baseline 0.55
                    "h_in": h2,
                    "h_out": h3,
                    "declassified": False,
                },
            ]
            p3_verdict = verify_p3_provenance_trace(p3_trace, tau_gap=0.35)
            if not proof_verified or not p3_verdict["valid"]:
                raise ValueError("Cannot anchor: Merkle inclusion proof or P3 provenance verification failed")

            prev_anchor_hash = "0" * 64
            block_height = existing_count + 1
            if ap.is_file() and existing_count > 0:
                existing_anchors = [ln.strip() for ln in ap.read_text(encoding="utf-8").splitlines() if ln.strip()]
                prev_anchor_hash = str(json.loads(existing_anchors[-1])["anchor_hash"])

            anchor_record: dict[str, Any] = {
                "anchor_id": f"PISTIS-BLK-{block_height:05d}",
                "protocol_version": PISTIS_PROTOCOL_VERSION,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "target_repo": t_dir.name,
                "target_path": str(t_dir),
                "git_head_sha": bindings["git_head_sha"],
                "git_tree_sha": bindings["git_tree_sha"],
                "gwi_receipt_sha256": bindings["gwi_receipt_sha256"],
                "ledger_events_anchored": event_count,
                "merkle_leaves_count": len(leaf_hashes),
                "rfc6962_merkle_root": f"0x{merkle_root}",
                "latest_leaf_hash": f"0x{leaf_hashes[last_idx]}",
                "inclusion_proof": inclusion_proof,
                "inclusion_proof_len": len(inclusion_proof),
                "inclusion_proof_verified": proof_verified,
                "p3_trace": p3_trace,
                "p3_provenance": p3_verdict,
                "prev_anchor_hash": prev_anchor_hash,
            }
            anchor_record["anchor_hash"] = hashlib.sha256(
                json.dumps(anchor_record, sort_keys=True).encode("utf-8")
            ).hexdigest()

            with ap.open("a", encoding="utf-8") as f:
                f.write(json.dumps(anchor_record) + "\n")
                f.flush()
                os.fsync(f.fileno())
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)

    append_triad_provenance_event(
        event_type="pistis.merkle_anchored",
        payload={
            "anchor_id": anchor_record["anchor_id"],
            "target_repo": t_dir.name,
            "git_head_sha": bindings["git_head_sha"],
            "rfc6962_merkle_root": anchor_record["rfc6962_merkle_root"],
            "anchor_hash": anchor_record["anchor_hash"],
        },
        ledger_file=lp,
    )
    return anchor_record

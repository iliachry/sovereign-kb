"""Law L10 anti-rubber-stamp human adjudication and pre-seal freshness/integrity gate."""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from ..audit_registry import compute_subproject_fingerprint, is_bundled_starter_vault_path
from .constants import (
    _VALID_EXECUTION_MODES,
    DEFAULT_MAX_DIFF_CHARS,
    MIN_INDEPENDENT_N_EFF,
    MIN_REJECTION_REASON_CHARS,
    _is_strict_int,
    _safe_float,
    _safe_int,
    classify_model_lineage,
)
from .council import _record_triad_from_elenchus, _verify_review_bindings
from .git_diff import (
    _build_instructions_block,
    _collect_bounded_diff,
    _compute_gate_receipt_sha256,
    _compute_gwi_receipt_sha256,
    _compute_worktree_content_sha,
    _resolve_target_and_scope,
    build_elenchus_challenge_packet,
)
from .git_hash import (
    _canonical_gwi_semantics,
    _compute_gwi_semantic_sha256,
    _detect_gwi_schema_contradiction,
    _is_real_num,
)
from .panel import _compute_seal_status, compute_effective_panel_size, synthesize_elenchus_verdict


def adjudicate_elenchus_finding(
    target_path: str | Path,
    finding_id: str,
    decision: str,
    reason: str = "",
    adjudicator: str = "human-operator",
    ledger_file: Path | None = None,
) -> dict[str, Any]:
    """
    Record an explicit human adjudication (`accept`, `reject`, `escalate`) on a disputed Elenchus cluster.
    Enforces Law L10 anti-rubber-stamp guardrails (`reason >= 12 chars` on reject, no bulk `ALL` reject when
    multiple pending clusters exist, and strict worktree freshness check).
    """
    from ..bridge import append_triad_provenance_event

    target_dir, repo_root, subpath = _resolve_target_and_scope(target_path)
    if is_bundled_starter_vault_path(target_dir):
        raise ValueError("Refusing to write Elenchus adjudication inside bundled starter_vault")

    receipt_path = target_dir / "evidence" / "elenchus_challenge.json"
    if not receipt_path.is_file():
        raise FileNotFoundError(f"Missing Elenchus challenge receipt: {receipt_path}. Run `skb elenchus run` first.")

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict):
        raise ValueError(f"Malformed Elenchus challenge receipt: {receipt_path}")
    schema_err = _validate_receipt_nested_schema(receipt)
    if schema_err:
        raise ValueError(f"Malformed Elenchus challenge receipt: {schema_err}")

    bindings = receipt.get("bindings") or {}
    fp = compute_subproject_fingerprint(repo_root, subpath)
    try:
        cur_worktree_sha = _compute_worktree_content_sha(target_dir)
    except OSError as exc:
        raise ValueError(f"Cannot verify worktree state for Elenchus adjudication: {exc}") from exc

    rec_tree = str(bindings.get("tree_sha") or "").strip()
    rec_wt = str(bindings.get("worktree_sha") or "").strip()
    if not rec_tree or not rec_wt or rec_tree != fp["tree_sha"] or rec_wt != cur_worktree_sha:
        raise ValueError(
            "Cannot adjudicate stale Elenchus receipt: repository changed since challenge run. Re-run `skb elenchus run` first."
        )

    goldwood_path = target_dir / "evidence" / "goldwood.json"
    rec_gwi_sha = str(bindings.get("gwi_receipt_sha256") or "").strip()
    if rec_gwi_sha:
        if not goldwood_path.is_file() or _compute_gwi_receipt_sha256(goldwood_path) != rec_gwi_sha:
            raise ValueError(
                "Cannot adjudicate stale Elenchus receipt: `evidence/goldwood.json` changed or disappeared since challenge run."
            )
    elif goldwood_path.is_file():
        raise ValueError(
            "Cannot adjudicate stale Elenchus receipt: `evidence/goldwood.json` appeared after challenge run."
        )

    gate_receipt_path = target_dir / "evidence" / "gate_receipt.json"
    rec_gate_sha = str(bindings.get("gate_receipt_sha256") or "").strip()
    if rec_gate_sha:
        if not gate_receipt_path.is_file() or _compute_gate_receipt_sha256(gate_receipt_path) != rec_gate_sha:
            raise ValueError(
                "Cannot adjudicate stale Elenchus receipt: `evidence/gate_receipt.json` changed or disappeared since challenge run."
            )
    elif gate_receipt_path.is_file():
        raise ValueError(
            "Cannot adjudicate stale Elenchus receipt: `evidence/gate_receipt.json` appeared after challenge run."
        )

    dec_low = (decision or "").strip().lower()
    if dec_low in ("accept", "accept_finding", "confirm", "fix"):
        norm_decision = "ACCEPT_FINDING"
    elif dec_low in ("reject", "reject_finding", "dismiss", "override"):
        norm_decision = "REJECT_FINDING"
    elif dec_low in ("escalate", "hold", "block"):
        norm_decision = "ESCALATE"
    else:
        raise ValueError(f"Unsupported adjudication decision '{decision}'. Use accept, reject, or escalate.")

    clean_reason = (reason or "").strip()
    if norm_decision == "REJECT_FINDING" and len(clean_reason) < MIN_REJECTION_REASON_CHARS:
        raise ValueError(
            f"Explicit rationale (>= {MIN_REJECTION_REASON_CHARS} chars) is required to reject/override a challenger finding."
        )

    clusters = receipt.get("clusters") or []
    fid_clean = (finding_id or "").strip()
    if not fid_clean:
        raise ValueError("`--finding <cluster_id|ALL>` is required for adjudication.")

    if fid_clean.upper() == "ALL":
        pending = [
            c
            for c in clusters
            if c.get("requires_adjudication")
            and str((c.get("adjudication") or {}).get("status") or "") != "ADJUDICATED"
        ]
        if not pending:
            raise ValueError("No pending adjudicable Elenchus clusters remain for 'ALL'.")
        req_clusters = [c for c in clusters if c.get("requires_adjudication")]
        has_blocked = any(
            str((c.get("adjudication") or {}).get("decision") or "").upper() in ("ACCEPT_FINDING", "ESCALATE")
            for c in req_clusters
        )
        if norm_decision == "REJECT_FINDING" and (len(pending) > 1 or len(req_clusters) > 1 or has_blocked):
            raise ValueError(
                "Bulk 'ALL' override is forbidden when multiple disputed clusters exist or any cluster was accepted/escalated; adjudicate each cluster ID explicitly."
            )
        targets = pending
    else:
        targets = [c for c in clusters if str(c.get("cluster_id", "")).upper() == fid_clean.upper()]
        if not targets:
            raise ValueError(f"Unknown Elenchus cluster ID '{fid_clean}'.")

    now_iso = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    adj_name = (adjudicator or "").strip() or "human-operator"
    for c in targets:
        c["adjudication"] = {
            "status": "ADJUDICATED",
            "decision": norm_decision,
            "adjudicator": adj_name,
            "reason": clean_reason,
            "adjudicated_at": now_iso,
        }

    reviewer_summaries = receipt.get("reviewer_summaries") or []
    panel = compute_effective_panel_size(reviewer_summaries)
    receipt["panel"] = panel
    llm_verdicts = [
        "CHALLENGE"
        if str(r.get("execution_status") or "OK").upper() == "FAILED"
        else str(r.get("verdict") or "CHALLENGE")
        for r in reviewer_summaries
        if str(r.get("lineage") or "") != "deterministic"
    ]
    exec_mode = str(receipt.get("execution_mode") or "explicit")
    rev_bound = bool(receipt.get("review_binding_verified", exec_mode != "reviews_file"))
    new_seal_status = _compute_seal_status(
        panel,
        clusters,
        llm_verdicts,
        execution_mode=exec_mode,
        review_binding_verified=rev_bound,
    )
    receipt["seal_status"] = new_seal_status
    receipt["adjudicated_at"] = now_iso

    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    append_triad_provenance_event(
        event_type="elenchus.adjudicated",
        payload={
            "project_name": receipt.get("project_name", target_dir.name),
            "finding_id": fid_clean.upper() if fid_clean.upper() == "ALL" else targets[0]["cluster_id"],
            "decision": norm_decision,
            "adjudicator": adj_name,
            "seal_status": new_seal_status,
            "tree_sha": fp["tree_sha"],
        },
        ledger_file=ledger_file,
        commit_sha=str(fp["commit_sha"]),
    )

    if new_seal_status in ("READY_UNANIMOUS", "READY_ADJUDICATED"):
        _record_triad_from_elenchus(target_dir, subpath, receipt)

    return receipt


def _validate_receipt_nested_schema(loaded: dict[str, Any]) -> str | None:
    for bk in ("score_blind", "review_binding_verified"):
        if bk in loaded and not isinstance(loaded[bk], bool):
            return f"Malformed boolean `{bk}` in Elenchus challenge receipt."

    bindings = loaded.get("bindings")
    if not isinstance(bindings, dict):
        return "Missing `bindings` object in Elenchus challenge receipt."
    if "max_diff_chars" in bindings and not _is_strict_int(bindings["max_diff_chars"]):
        return "Malformed `bindings.max_diff_chars` in Elenchus receipt."
    for bk in ("dirty", "diff_truncated", "coverage_complete"):
        if bk in bindings and not isinstance(bindings[bk], bool):
            return f"Malformed boolean `bindings.{bk}` in Elenchus receipt."

    raw_panel = loaded.get("panel")
    if raw_panel is not None:
        if not isinstance(raw_panel, dict):
            return "Malformed `panel` object in Elenchus challenge receipt."
        for bk in ("independent_panel", "gate_closable"):
            if bk in raw_panel and not isinstance(raw_panel[bk], bool):
                return f"Malformed boolean `panel.{bk}` in Elenchus challenge receipt."
        for num_k in ("n_eff", "shannon_entropy", "min_independent_n_eff"):
            if num_k in raw_panel and not _is_real_num(raw_panel[num_k]):
                return f"Malformed numeric `panel.{num_k}` in Elenchus challenge receipt."
        raw_lins = raw_panel.get("distinct_llm_lineages")
        if raw_lins is not None and (not isinstance(raw_lins, list) or not all(isinstance(x, str) for x in raw_lins)):
            return "Malformed `panel.distinct_llm_lineages` in Elenchus receipt."

    raw_metrics = loaded.get("metrics")
    if raw_metrics is not None:
        if not isinstance(raw_metrics, dict):
            return "Malformed `metrics` object in Elenchus challenge receipt."
        if "incomplete_coverage" in raw_metrics and not isinstance(raw_metrics["incomplete_coverage"], bool):
            return "Malformed boolean `metrics.incomplete_coverage` in Elenchus receipt."
        for num_k in ("disagreement_index", "mean_anchoring_ratio", "anchoring_ratio", "prompt_anchoring_ratio"):
            if num_k in raw_metrics and not _is_real_num(raw_metrics[num_k]):
                return f"Malformed numeric `metrics.{num_k}` in Elenchus receipt."
        if "total_clusters" in raw_metrics and not _is_strict_int(raw_metrics["total_clusters"]):
            return "Malformed `metrics.total_clusters` in Elenchus receipt."

    raw_clusters = loaded.get("clusters")
    if raw_clusters is not None:
        if not isinstance(raw_clusters, list) or not all(isinstance(c, dict) for c in raw_clusters):
            return "Malformed `clusters` list in Elenchus challenge receipt."
        for cl in raw_clusters:
            for bk in ("requires_adjudication", "grounded_in_repo"):
                if bk in cl and not isinstance(cl[bk], bool):
                    return f"Malformed boolean `{bk}` in Elenchus cluster record."
            if "line" in cl and not _is_strict_int(cl["line"]):
                return "Malformed `line` in Elenchus cluster record."
            if "anchoring_ratio" in cl and not _is_real_num(cl["anchoring_ratio"]):
                return "Malformed `anchoring_ratio` in Elenchus cluster record."
            if "adjudication" in cl and not isinstance(cl["adjudication"], dict):
                return "Malformed `adjudication` in Elenchus cluster record."
            if "lineages" in cl and (
                not isinstance(cl["lineages"], list) or not all(isinstance(x, str) for x in cl["lineages"])
            ):
                return "Malformed `lineages` in Elenchus cluster record."

    raw_summaries = loaded.get("reviewer_summaries")
    if raw_summaries is not None:
        if not isinstance(raw_summaries, list) or not all(isinstance(rs, dict) for rs in raw_summaries):
            return "Malformed `reviewer_summaries` list in Elenchus challenge receipt."
        for rs in raw_summaries:
            if "findings_count" in rs and not _is_strict_int(rs["findings_count"]):
                return "Malformed `findings_count` in Elenchus `reviewer_summaries`."

    raw_reviews = loaded.get("raw_reviews")
    if raw_reviews is not None:
        if not isinstance(raw_reviews, list) or not all(isinstance(r, dict) for r in raw_reviews):
            return "Malformed `raw_reviews` list in Elenchus challenge receipt."
        for rev in raw_reviews:
            if "findings" in rev:
                r_findings = rev["findings"]
                if not isinstance(r_findings, list) or not all(isinstance(f, dict) for f in r_findings):
                    return "Malformed nested `raw_reviews[].findings` in Elenchus challenge receipt."
                for f in r_findings:
                    if "line" in f and not _is_strict_int(f["line"]):
                        return "Malformed `line` in nested `raw_reviews[].findings`."
            elif "raw_text" not in rev:
                return "Malformed reviewer record in `raw_reviews`: missing `findings` list."

    imp = loaded.get("import_bindings")
    if imp is not None:
        if not isinstance(imp, dict):
            return "Malformed `import_bindings` object in Elenchus challenge receipt."
        item_b = imp.get("item_bindings")
        if item_b is not None and (not isinstance(item_b, list) or not all(isinstance(x, dict) for x in item_b)):
            return "Malformed `import_bindings.item_bindings` in Elenchus challenge receipt."
        if "envelope" in imp and imp["envelope"] is not None and not isinstance(imp["envelope"], dict):
            return "Malformed `import_bindings.envelope` in Elenchus challenge receipt."

    return None


def _corrupted_gate_result(message: str, n_eff: float = 0.0) -> dict[str, Any]:
    return {
        "passed": False,
        "seal_status": "CORRUPTED",
        "reason_code": "CORRUPTED_OR_CONTRADICTORY_RECEIPT",
        "message": message,
        "n_eff": _safe_float(n_eff, 0.0),
        "pending_ids": [],
    }


def evaluate_elenchus_gate(
    target_path: str | Path,
    receipt: dict[str, Any] | None = None,
    gwi_report: dict[str, Any] | None = None,
    require_independent: bool = True,
) -> dict[str, Any]:
    """
    Fail-closed verifier gating `.evr` sealing (`--require-elenchus`) and `organon finish`.
    Checks receipt presence, worktree/tree SHA freshness, gate/GWI/report semantic bindings,
    imported review binding, recomputed cross-lineage independence `N_eff >= 1.80`, and
    contradiction-free human adjudication closure.
    """
    target_dir, repo_root, subpath = _resolve_target_and_scope(target_path)
    loaded = receipt
    if loaded is None:
        r_path = target_dir / "evidence" / "elenchus_challenge.json"
        if not r_path.is_file():
            return {
                "passed": False,
                "seal_status": "MISSING",
                "reason_code": "MISSING_ELENCHUS_RECEIPT",
                "message": "Missing `evidence/elenchus_challenge.json`. Run `skb elenchus run .` before sealing.",
                "n_eff": 0.0,
                "pending_ids": [],
            }
        try:
            loaded = json.loads(r_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return _corrupted_gate_result(f"Malformed `evidence/elenchus_challenge.json`: {exc}")

    if not isinstance(loaded, dict):
        return _corrupted_gate_result("Malformed Elenchus challenge receipt: expected JSON object.")

    raw_panel_hdr = loaded.get("panel")
    n_eff_fallback = _safe_float(raw_panel_hdr.get("n_eff") if isinstance(raw_panel_hdr, dict) else None, 0.0)
    schema_err = _validate_receipt_nested_schema(loaded)
    if schema_err:
        return _corrupted_gate_result(schema_err, n_eff_fallback)
    bindings = loaded["bindings"]

    rec_tree_sha = str(bindings.get("tree_sha") or "").strip()
    rec_worktree_sha = str(bindings.get("worktree_sha") or "").strip()
    if not rec_tree_sha or not rec_worktree_sha:
        return {
            "passed": False,
            "seal_status": "STALE",
            "reason_code": "STALE_WORKTREE",
            "message": "Elenchus challenge receipt lacks `tree_sha` or `worktree_sha` binding.",
            "n_eff": n_eff_fallback,
            "pending_ids": [],
        }

    fp = compute_subproject_fingerprint(repo_root, subpath)
    try:
        cur_worktree_sha = _compute_worktree_content_sha(target_dir)
    except OSError as exc:
        return {
            "passed": False,
            "seal_status": "STALE",
            "reason_code": "STALE_WORKTREE",
            "message": f"Cannot verify worktree content digest cleanly: {exc}",
            "n_eff": n_eff_fallback,
            "pending_ids": [],
        }
    if rec_tree_sha != fp["tree_sha"] or rec_worktree_sha != cur_worktree_sha:
        return {
            "passed": False,
            "seal_status": "STALE",
            "reason_code": "STALE_WORKTREE",
            "message": "Elenchus challenge receipt is stale: repository files/tree changed since challenge run.",
            "n_eff": n_eff_fallback,
            "pending_ids": [],
        }

    gate_receipt_path = target_dir / "evidence" / "gate_receipt.json"
    rec_gate_sha = str(bindings.get("gate_receipt_sha256") or "").strip()
    if rec_gate_sha:
        if not gate_receipt_path.is_file():
            return {
                "passed": False,
                "seal_status": "MISSING_GATE",
                "reason_code": "MISSING_GATE_RECEIPT",
                "message": "Bound `evidence/gate_receipt.json` was removed after Elenchus challenge run.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        try:
            cur_gate_sha = _compute_gate_receipt_sha256(gate_receipt_path)
        except OSError as exc:
            return {
                "passed": False,
                "seal_status": "INVALID_GATE",
                "reason_code": "INVALID_GATE_RECEIPT",
                "message": f"Cannot hash bound `evidence/gate_receipt.json`: {exc}",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        if cur_gate_sha != rec_gate_sha:
            return {
                "passed": False,
                "seal_status": "STALE_GATE",
                "reason_code": "STALE_GATE_RECEIPT",
                "message": "`evidence/gate_receipt.json` changed after Elenchus challenge run.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }

    if gate_receipt_path.is_file():
        try:
            gr_obj = json.loads(gate_receipt_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "passed": False,
                "seal_status": "INVALID_GATE",
                "reason_code": "INVALID_GATE_RECEIPT",
                "message": f"Malformed `evidence/gate_receipt.json`: {exc}",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        gr_ec = gr_obj.get("exit_code") if isinstance(gr_obj, dict) else None
        gr_fft = gr_obj.get("fresh_for_tree") if isinstance(gr_obj, dict) else None
        if (
            not isinstance(gr_obj, dict)
            or gr_obj.get("passed") is not True
            or gr_obj.get("executed") is not True
            or not _is_strict_int(gr_ec)
            or gr_ec != 0
            or ("fresh_for_tree" in gr_obj and gr_fft is not None and not isinstance(gr_fft, bool))
        ):
            return {
                "passed": False,
                "seal_status": "INVALID_GATE",
                "reason_code": "INVALID_GATE_RECEIPT",
                "message": "`evidence/gate_receipt.json` did not execute and pass cleanly (literal true passed/executed and int exit_code==0 required).",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        raw_gr_tree = gr_obj.get("tree_sha")
        raw_gr_wt = gr_obj.get("worktree_sha")
        gr_tree = raw_gr_tree.strip() if isinstance(raw_gr_tree, str) else ""
        gr_wt = raw_gr_wt.strip() if isinstance(raw_gr_wt, str) else ""
        if not gr_tree or gr_tree != fp["tree_sha"] or not gr_wt or gr_wt != cur_worktree_sha:
            return {
                "passed": False,
                "seal_status": "STALE_GATE",
                "reason_code": "STALE_GATE_RECEIPT",
                "message": "`evidence/gate_receipt.json` tree_sha or worktree_sha does not match current repository state.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        if not bool(fp.get("dirty")) and gr_obj.get("fresh_for_tree") is False:
            return {
                "passed": False,
                "seal_status": "STALE_GATE",
                "reason_code": "STALE_GATE_RECEIPT",
                "message": "`evidence/gate_receipt.json` reports `fresh_for_tree=False`.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        if not rec_gate_sha:
            return {
                "passed": False,
                "seal_status": "STALE_GATE",
                "reason_code": "STALE_GATE_RECEIPT",
                "message": "`evidence/gate_receipt.json` appeared after Elenchus challenge run.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }

    goldwood_path = target_dir / "evidence" / "goldwood.json"
    rec_gwi_sha = str(bindings.get("gwi_receipt_sha256") or "").strip()
    rec_gwi_sem_sha = str(bindings.get("gwi_semantic_sha256") or "").strip()
    rec_proj = str(loaded.get("project_name") or "").strip()
    if rec_gwi_sha:
        if not goldwood_path.is_file():
            return {
                "passed": False,
                "seal_status": "MISSING_GWI",
                "reason_code": "MISSING_GWI_RECEIPT",
                "message": "Bound `evidence/goldwood.json` was removed after Elenchus challenge run.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        if _compute_gwi_receipt_sha256(goldwood_path) != rec_gwi_sha:
            return {
                "passed": False,
                "seal_status": "STALE_GWI",
                "reason_code": "STALE_GWI_RECEIPT",
                "message": "Elenchus challenge receipt is stale: `evidence/goldwood.json` metrics changed since challenge run.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
    if goldwood_path.is_file():
        if not rec_gwi_sha:
            return {
                "passed": False,
                "seal_status": "STALE_GWI",
                "reason_code": "STALE_GWI_RECEIPT",
                "message": "`evidence/goldwood.json` appeared after Elenchus challenge run.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        try:
            g_disk = json.loads(goldwood_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "passed": False,
                "seal_status": "STALE_GWI",
                "reason_code": "STALE_GWI_RECEIPT",
                "message": f"Malformed `evidence/goldwood.json`: {exc}",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        if not isinstance(g_disk, dict):
            return _corrupted_gate_result("Malformed `evidence/goldwood.json`: expected JSON object.", n_eff_fallback)
        disk_contra = _detect_gwi_schema_contradiction(g_disk)
        if disk_contra:
            return _corrupted_gate_result(disk_contra, n_eff_fallback)
        if rec_gwi_sha and rec_gwi_sem_sha and _compute_gwi_semantic_sha256(g_disk) != rec_gwi_sem_sha:
            return {
                "passed": False,
                "seal_status": "STALE_GWI",
                "reason_code": "STALE_GWI_RECEIPT",
                "message": "`evidence/goldwood.json` semantic metrics drifted since challenge run.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        g_disk_sem = _canonical_gwi_semantics(g_disk)
        if g_disk_sem["status"] != "PASS":
            return {
                "passed": False,
                "seal_status": "GWI_FAIL",
                "reason_code": "GWI_NOT_PASS",
                "message": f"`evidence/goldwood.json` status is '{g_disk_sem['status']}' (expected PASS).",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        if g_disk_sem["project"] and rec_proj and g_disk_sem["project"] != rec_proj:
            return {
                "passed": False,
                "seal_status": "GWI_MISMATCH",
                "reason_code": "GWI_REPORT_MISMATCH",
                "message": f"`evidence/goldwood.json` project '{g_disk_sem['project']}' does not match '{rec_proj}'.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }

    if gwi_report is not None and not isinstance(gwi_report, dict):
        return _corrupted_gate_result("Malformed GWI report: expected dict.", n_eff_fallback)

    if isinstance(gwi_report, dict):
        rep_contra = _detect_gwi_schema_contradiction(gwi_report)
        if rep_contra:
            return _corrupted_gate_result(rep_contra, n_eff_fallback)
        g_sem = _canonical_gwi_semantics(gwi_report)
        if g_sem["status"] != "PASS":
            return {
                "passed": False,
                "seal_status": "GWI_FAIL",
                "reason_code": "GWI_NOT_PASS",
                "message": f"Cannot close Elenchus sealing gate while GWI status is '{g_sem['status'] or 'MISSING'}' (expected PASS).",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        if g_sem["project"] and rec_proj and g_sem["project"] != rec_proj:
            return {
                "passed": False,
                "seal_status": "GWI_MISMATCH",
                "reason_code": "GWI_REPORT_MISMATCH",
                "message": f"GWI report project '{g_sem['project']}' does not match Elenchus target '{rec_proj}'.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        if rec_gwi_sem_sha and _compute_gwi_semantic_sha256(gwi_report) != rec_gwi_sem_sha:
            return {
                "passed": False,
                "seal_status": "STALE_GWI",
                "reason_code": "STALE_GWI_RECEIPT",
                "message": "Sealed GWI report semantics differ from the `evidence/goldwood.json` bound into Elenchus.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }

    raw_reviews = loaded.get("raw_reviews")
    if not isinstance(raw_reviews, list) or not raw_reviews or not all(isinstance(r, dict) for r in raw_reviews):
        return _corrupted_gate_result(
            "Elenchus receipt lacks valid `raw_reviews` evidence for independent reconstruction.",
            n_eff_fallback,
        )

    exec_mode = str(loaded.get("execution_mode") or "explicit")
    if exec_mode not in _VALID_EXECUTION_MODES:
        return _corrupted_gate_result(f"Unsupported Elenchus `execution_mode`: {exec_mode!r}.", n_eff_fallback)
    if exec_mode == "offline" and any(
        classify_model_lineage(str(r.get("model_id") or r.get("model") or "")) != "deterministic" for r in raw_reviews
    ):
        return _corrupted_gate_result(
            "`execution_mode='offline'` contradicts non-deterministic reviewer entries in `raw_reviews`.",
            n_eff_fallback,
        )

    raw_budget = bindings.get("max_diff_chars")
    if "max_diff_chars" not in bindings or isinstance(raw_budget, bool) or raw_budget is None:
        return _corrupted_gate_result("Elenchus receipt lacks valid `bindings.max_diff_chars` budget.", n_eff_fallback)
    if not _is_strict_int(raw_budget):
        return _corrupted_gate_result("Malformed `bindings.max_diff_chars` in Elenchus receipt.", n_eff_fallback)
    max_chars_val = raw_budget
    if max_chars_val <= 0 or max_chars_val > DEFAULT_MAX_DIFF_CHARS:
        return _corrupted_gate_result(
            f"`bindings.max_diff_chars` ({max_chars_val}) is outside allowed bounds (1..{DEFAULT_MAX_DIFF_CHARS}).",
            n_eff_fallback,
        )

    rec_diff_sha = str(bindings.get("diff_sha256") or "").strip()
    rec_pkt_sha = str(bindings.get("packet_sha256") or "").strip()
    if not rec_diff_sha or not rec_pkt_sha:
        return _corrupted_gate_result(
            "Elenchus receipt lacks `bindings.diff_sha256` or `bindings.packet_sha256` integrity digest.",
            n_eff_fallback,
        )

    live_diff, _, live_trunc = _collect_bounded_diff(repo_root, bool(fp.get("dirty")), max_diff_chars=max_chars_val)
    live_diff_sha = hashlib.sha256(live_diff.encode("utf-8")).hexdigest()
    if live_diff_sha != rec_diff_sha:
        return _corrupted_gate_result(
            "Stored `bindings.diff_sha256` contradicts reconstructed repository diff.",
            n_eff_fallback,
        )
    if bool(bindings.get("diff_truncated", False)) != bool(live_trunc) or (
        bool(bindings.get("coverage_complete", not live_trunc)) != (not bool(live_trunc))
    ):
        return _corrupted_gate_result(
            "Stored `bindings.diff_truncated` or `coverage_complete` contradicts current diff truncation state.",
            n_eff_fallback,
        )

    cur_gwi_now = _compute_gwi_receipt_sha256(goldwood_path)
    cur_gate_now = _compute_gate_receipt_sha256(gate_receipt_path)
    if cur_gwi_now == rec_gwi_sha and cur_gate_now == rec_gate_sha:
        intent_str = str(loaded.get("intent") or "").strip()
        if not intent_str:
            return _corrupted_gate_result(
                "Elenchus receipt lacks non-empty `intent` for packet reconstruction.", n_eff_fallback
            )
        live_pkt = build_elenchus_challenge_packet(
            target_dir,
            intent=intent_str,
            blind_scores=bool(loaded.get("score_blind", True)),
            max_diff_chars=max_chars_val,
        )
        if str((live_pkt.get("bindings") or {}).get("packet_sha256") or "") != rec_pkt_sha:
            return _corrupted_gate_result(
                "Stored `bindings.packet_sha256` contradicts reconstructed challenge packet.",
                n_eff_fallback,
            )

    if exec_mode != "reviews_file" and bool(loaded.get("import_bindings")):
        return _corrupted_gate_result(
            "Non-empty `import_bindings` contradicts non-import `execution_mode`.",
            n_eff_fallback,
        )

    if exec_mode == "reviews_file":
        imp = loaded.get("import_bindings")
        if not isinstance(imp, dict):
            return {
                "passed": False,
                "seal_status": "UNBOUND_IMPORT_ADVISORY",
                "reason_code": "UNBOUND_REVIEW_IMPORT",
                "message": "Imported `--reviews` receipt lacks persisted binding proof; advisory only.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        item_b = imp.get("item_bindings")
        if (
            not isinstance(item_b, list)
            or len(item_b) != len(raw_reviews)
            or not all(isinstance(x, dict) for x in item_b)
        ):
            return _corrupted_gate_result(
                "`import_bindings.item_bindings` cardinality does not match `raw_reviews`.",
                n_eff_fallback,
            )
        try:
            imp_verified = _verify_review_bindings(
                item_b,
                dict(imp.get("envelope") or {}),
                bindings,
                is_file_import=True,
            )
        except ValueError as exc:
            return _corrupted_gate_result(f"Imported review binding contradiction: {exc}", n_eff_fallback)
        if not imp_verified or loaded.get("review_binding_verified") is not True:
            return {
                "passed": False,
                "seal_status": "UNBOUND_IMPORT_ADVISORY",
                "reason_code": "UNBOUND_REVIEW_IMPORT",
                "message": "Imported `--reviews` transcript lacks verified packet/worktree binding; advisory only.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
    elif "review_binding_verified" in loaded and loaded.get("review_binding_verified") is not True:
        return {
            "passed": False,
            "seal_status": "UNBOUND_IMPORT_ADVISORY",
            "reason_code": "UNBOUND_REVIEW_IMPORT",
            "message": "Elenchus review binding flag is unverified; advisory only.",
            "n_eff": n_eff_fallback,
            "pending_ids": [],
        }

    reviewer_summaries = loaded.get("reviewer_summaries")
    if not isinstance(reviewer_summaries, list) or not reviewer_summaries:
        return _corrupted_gate_result("Elenchus receipt lacks valid `reviewer_summaries` evidence.", n_eff_fallback)

    expected_syn = synthesize_elenchus_verdict(
        raw_reviews,
        {
            "target_dir": str(target_dir),
            "repo_root": str(repo_root),
            "intent": str(loaded.get("intent") or ""),
            "prompt_instructions_and_intent": _build_instructions_block(str(loaded.get("intent") or "")),
            "bindings": bindings,
        },
        target_dir=target_dir,
        execution_mode=exec_mode,
        review_binding_verified=True,
    )
    exp_summaries = expected_syn["reviewer_summaries"]
    if len(reviewer_summaries) != len(exp_summaries):
        return _corrupted_gate_result("`reviewer_summaries` count contradicts `raw_reviews`.", n_eff_fallback)
    for rs, es in zip(reviewer_summaries, exp_summaries, strict=False):
        rs_count = _safe_int(rs.get("findings_count", -1)) if isinstance(rs, dict) else None
        if (
            not isinstance(rs, dict)
            or rs_count is None
            or (
                str(rs.get("model_id") or "").strip(),
                classify_model_lineage(str(rs.get("model_id") or "")),
                str(rs.get("verdict") or "").upper(),
                str(rs.get("execution_status") or "OK").upper(),
                rs_count,
            )
            != (
                es["model_id"],
                es["lineage"],
                es["verdict"],
                es["execution_status"],
                es["findings_count"],
            )
        ):
            return _corrupted_gate_result("Stored `reviewer_summaries` contradicts `raw_reviews`.", n_eff_fallback)

    recomputed_panel = expected_syn["panel"]
    stored_panel = loaded.get("panel")
    if not isinstance(stored_panel, dict):
        return _corrupted_gate_result("Elenchus receipt lacks valid `panel` object.", float(recomputed_panel["n_eff"]))

    raw_lins = stored_panel.get("distinct_llm_lineages") or []
    if not isinstance(raw_lins, list):
        return _corrupted_gate_result("Malformed `panel.distinct_llm_lineages` in Elenchus receipt.")
    stored_lins = sorted(str(x) for x in raw_lins)
    stored_n_eff = stored_panel.get("n_eff")
    if not _is_real_num(stored_n_eff):
        return _corrupted_gate_result(
            "Malformed `panel.n_eff` in Elenchus receipt.",
            float(recomputed_panel["n_eff"]),
        )
    if (
        bool(stored_panel.get("independent_panel", False)) != bool(recomputed_panel["independent_panel"])
        or abs(_safe_float(stored_n_eff, 0.0) - float(recomputed_panel["n_eff"])) > 1e-3
        or stored_lins != list(recomputed_panel["distinct_llm_lineages"])
    ):
        return _corrupted_gate_result(
            "Stored Elenchus panel independence metrics contradict reconstructed `raw_reviews` panel.",
            float(recomputed_panel["n_eff"]),
        )

    n_eff = float(recomputed_panel["n_eff"])
    if require_independent and not recomputed_panel["independent_panel"]:
        return {
            "passed": False,
            "seal_status": str(loaded.get("seal_status") or "DEGRADED_NON_INDEPENDENT"),
            "reason_code": "NON_INDEPENDENT_PANEL",
            "message": (
                f"Elenchus panel is non-independent (mode={recomputed_panel['panel_mode']}, N_eff={n_eff:.2f} < {MIN_INDEPENDENT_N_EFF:.2f}). "
                "Cross-lineage review is required to close `--require-elenchus`."
            ),
            "n_eff": n_eff,
            "pending_ids": [],
        }

    clusters = loaded.get("clusters")
    if not isinstance(clusters, list):
        return _corrupted_gate_result("Elenchus receipt `clusters` must be a list.", n_eff)

    exp_clusters = expected_syn["clusters"]
    if len(clusters) != len(exp_clusters):
        return _corrupted_gate_result(
            f"Stored `clusters` count ({len(clusters)}) contradicts reconstructed cluster count ({len(exp_clusters)}).",
            n_eff,
        )
    stored_metrics = loaded.get("metrics")
    if isinstance(stored_metrics, dict) and "total_clusters" in stored_metrics:
        met_tot = _safe_int(stored_metrics.get("total_clusters"))
        if met_tot is None or met_tot != len(clusters):
            return _corrupted_gate_result("Stored `metrics.total_clusters` contradicts `clusters` length.", n_eff)

    for sc, ec in zip(clusters, exp_clusters, strict=False):
        sc_line = _safe_int(sc.get("line", 0)) if isinstance(sc, dict) else None
        if not isinstance(sc, dict) or sc_line is None:
            return _corrupted_gate_result("Malformed cluster record in Elenchus receipt.", n_eff)
        if (
            str(sc.get("cluster_id") or ""),
            str(sc.get("severity") or "").upper(),
            str(sc.get("file_path") or ""),
            sc_line,
            str(sc.get("symbol") or ""),
            str(sc.get("claim") or ""),
            bool(sc.get("requires_adjudication")),
        ) != (
            ec["cluster_id"],
            ec["severity"],
            ec["file_path"],
            ec["line"],
            ec["symbol"],
            ec["claim"],
            ec["requires_adjudication"],
        ):
            return _corrupted_gate_result(
                f"Stored cluster `{sc.get('cluster_id')}` was altered relative to reconstructed `raw_reviews`.",
                n_eff,
            )
        if ec["signal_class"] in ("LINEAGE_SPLIT", "INCOMPLETE_COVERAGE", "SINGLE_LINEAGE_NOTE") and (
            str(sc.get("signal_class") or "") != ec["signal_class"]
        ):
            return _corrupted_gate_result(
                f"Stored cluster `{sc.get('cluster_id')}` signal_class disagrees with reconstructed `raw_reviews`.",
                n_eff,
            )

    for c in clusters:
        if not isinstance(c, dict):
            return _corrupted_gate_result("Malformed cluster record in Elenchus receipt.", n_eff)
        sev = str(c.get("severity") or "").upper()
        sig = str(c.get("signal_class") or "").upper()
        mandatory_adj = (sev in ("P0", "P1")) or (
            sig in ("LINEAGE_SPLIT", "INCOMPLETE_COVERAGE") and sev in ("P0", "P1", "P2")
        )
        if mandatory_adj and not bool(c.get("requires_adjudication")):
            return _corrupted_gate_result(
                f"Mandatory disputed cluster `{c.get('cluster_id')}` had `requires_adjudication=False`.",
                n_eff,
            )
        adj = c.get("adjudication")
        if not isinstance(adj, dict):
            return _corrupted_gate_result(f"Cluster `{c.get('cluster_id')}` lacks adjudication object.", n_eff)
        adj_status = str(adj.get("status") or "")
        dec = str(adj.get("decision") or "").upper()
        reason = str(adj.get("reason") or "").strip()
        if adj_status == "ADJUDICATED":
            if dec not in ("ACCEPT_FINDING", "REJECT_FINDING", "ESCALATE"):
                return _corrupted_gate_result(
                    f"Cluster `{c.get('cluster_id')}` has invalid adjudication decision `{dec}`.",
                    n_eff,
                )
            if dec == "REJECT_FINDING" and len(reason) < MIN_REJECTION_REASON_CHARS:
                return _corrupted_gate_result(
                    f"Cluster `{c.get('cluster_id')}` was rejected without a >= {MIN_REJECTION_REASON_CHARS}-char rationale.",
                    n_eff,
                )
        elif dec:
            return _corrupted_gate_result(
                f"Cluster `{c.get('cluster_id')}` specifies decision `{dec}` while status is `{adj_status}`.",
                n_eff,
            )

    llm_verdicts = [
        "CHALLENGE"
        if str(r.get("execution_status") or "OK").upper() == "FAILED"
        else str(r.get("verdict") or "CHALLENGE").upper()
        for r in reviewer_summaries
        if classify_model_lineage(str(r.get("model_id") or "")) != "deterministic"
    ]
    expected_seal = _compute_seal_status(
        recomputed_panel,
        clusters,
        llm_verdicts,
        execution_mode=exec_mode,
        review_binding_verified=True,
    )
    stored_seal = str(loaded.get("seal_status") or "UNKNOWN")
    if stored_seal != expected_seal:
        return _corrupted_gate_result(
            f"Stored `seal_status` ('{stored_seal}') contradicts recomputed state ('{expected_seal}').",
            n_eff,
        )

    pending_ids = [
        str(c.get("cluster_id"))
        for c in clusters
        if c.get("requires_adjudication") and str((c.get("adjudication") or {}).get("status") or "") != "ADJUDICATED"
    ]
    blocked_ids = [
        str(c.get("cluster_id"))
        for c in clusters
        if str((c.get("adjudication") or {}).get("decision") or "").upper() in ("ACCEPT_FINDING", "ESCALATE")
    ]

    if expected_seal not in ("READY_UNANIMOUS", "READY_ADJUDICATED") or blocked_ids or pending_ids:
        action_ids = blocked_ids or pending_ids
        return {
            "passed": False,
            "seal_status": expected_seal,
            "reason_code": expected_seal,
            "message": (
                f"Elenchus seal status is '{expected_seal}' (disputed/blocking clusters: {action_ids or ['verdict-split']}). "
                "Resolve defects or adjudicate via `skb elenchus adjudicate`."
            ),
            "n_eff": n_eff,
            "pending_ids": action_ids,
        }

    return {
        "passed": True,
        "seal_status": expected_seal,
        "reason_code": "OK",
        "message": f"Elenchus gate passed ({expected_seal}, N_eff={n_eff:.2f}).",
        "n_eff": n_eff,
        "pending_ids": [],
    }

"""Model lineage diversity (`N_eff`), prompt-anchoring Jaccard detection, and verdict synthesis."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .constants import (
    _SEVERITY_RANK,
    _STOPWORDS,
    _VERDICT_SCORE,
    _VERIFIED_LLM_LINEAGES,
    ANCHOR_ECHO_THRESHOLD,
    MIN_INDEPENDENT_N_EFF,
    _normalize_execution_status,
    classify_model_lineage,
)
from .prose_rules import _normalize_finding_dict, _normalize_verdict
from .redaction import _scrub_review_dict
from .review_parser import parse_reviewer_output


def compute_effective_panel_size(
    models: Sequence[str | dict[str, Any]],
    failed_models: set[str] | list[str] | None = None,
) -> dict[str, Any]:
    """
    Compute Shannon-Hill effective independent lineage count `N_eff = exp(H)` over verified,
    successfully executed LLM reviewers. Single-lineage, unverified-alias, failed, or
    deterministic panels fail closed (`gate_closable = False`).
    """
    failed_set = {str(x).strip() for x in (failed_models or []) if str(x).strip()}
    classified: list[dict[str, str]] = []
    llm_lineages: list[str] = []
    det_count = 0
    unverified_count = 0
    failed_count = 0

    for item in models:
        if isinstance(item, dict):
            m_str = str(item.get("model_id") or item.get("model") or "").strip()
            exec_st = _normalize_execution_status(item.get("execution_status"))
        else:
            m_str = str(item or "").strip()
            exec_st = "FAILED" if m_str in failed_set else "OK"
        if not m_str:
            continue
        raw_lin = classify_model_lineage(m_str)
        if exec_st == "FAILED":
            failed_count += 1
            classified.append(
                {
                    "model_id": m_str,
                    "lineage": raw_lin if raw_lin in _VERIFIED_LLM_LINEAGES else "unverified",
                    "raw_lineage": raw_lin,
                    "execution_status": "FAILED",
                }
            )
            continue
        if raw_lin == "deterministic":
            det_count += 1
            classified.append(
                {
                    "model_id": m_str,
                    "lineage": "deterministic",
                    "raw_lineage": raw_lin,
                    "execution_status": "OK",
                }
            )
        elif raw_lin in _VERIFIED_LLM_LINEAGES:
            llm_lineages.append(raw_lin)
            classified.append(
                {
                    "model_id": m_str,
                    "lineage": raw_lin,
                    "raw_lineage": raw_lin,
                    "execution_status": "OK",
                }
            )
        else:
            unverified_count += 1
            classified.append(
                {
                    "model_id": m_str,
                    "lineage": "unverified",
                    "raw_lineage": raw_lin,
                    "execution_status": "OK",
                }
            )

    m_llm = len(llm_lineages)
    counts = Counter(llm_lineages)
    if m_llm == 0:
        entropy = 0.0
        n_eff = 1.0
    else:
        entropy = -sum((c / m_llm) * math.log(c / m_llm) for c in counts.values() if c > 0)
        n_eff = math.exp(entropy)

    n_eff_round = round(n_eff, 4)
    independent_panel = m_llm >= 2 and n_eff >= MIN_INDEPENDENT_N_EFF
    panel_mode = "CROSS_LINEAGE_INDEPENDENT" if independent_panel else "DEGRADED_SINGLE_LINEAGE"
    if independent_panel:
        warning = ""
    elif failed_count or unverified_count:
        warning = (
            f"DEGRADED NON-INDEPENDENT SANITY — verified LLM lineages={sorted(counts.keys())}, "
            f"failed={failed_count}, unverified={unverified_count}; advisory only, cannot close --require-elenchus gate"
        )
    else:
        warning = "DEGRADED NON-INDEPENDENT SANITY — single or deterministic lineage; advisory only, cannot close --require-elenchus gate"

    return {
        "models": classified,
        "total_reviewers": len(classified),
        "llm_reviewer_count": m_llm,
        "deterministic_reviewer_count": det_count,
        "unverified_reviewer_count": unverified_count,
        "failed_reviewer_count": failed_count,
        "lineage_counts": dict(sorted(counts.items())),
        "distinct_llm_lineages": sorted(counts.keys()),
        "shannon_entropy": round(entropy, 4),
        "n_eff": n_eff_round,
        "min_independent_n_eff": MIN_INDEPENDENT_N_EFF,
        "independent_panel": independent_panel,
        "panel_mode": panel_mode,
        "gate_closable": bool(independent_panel),
        "warning": warning,
    }


def _content_tokens(text: str) -> set[str]:
    toks = re.findall(r"[a-z0-9_]{3,}", (text or "").lower())
    return {t for t in toks if t not in _STOPWORDS}


def _extract_ngrams_2_3(text: str) -> set[str]:
    words = [w for w in re.findall(r"[a-z0-9_]+", (text or "").lower()) if len(w) >= 2]
    grams: set[str] = set()
    for n in (2, 3):
        for i in range(max(0, len(words) - n + 1)):
            grams.add(" ".join(words[i : i + n]))
    return grams


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union > 0 else 0.0


def _findings_match(a: dict[str, Any], b: dict[str, Any]) -> bool:
    fp_a = a["file_path"]
    fp_b = b["file_path"]
    if fp_a and fp_b and fp_a != fp_b:
        return False
    if fp_a and fp_b and fp_a == fp_b:
        sym_a = a["symbol"].lower()
        sym_b = b["symbol"].lower()
        if sym_a and sym_b and sym_a == sym_b:
            return True
        if a["line"] > 0 and b["line"] > 0 and abs(a["line"] - b["line"]) <= 15:
            return True
        if _jaccard(a["tokens"], b["tokens"]) >= 0.25:
            return True
    return _jaccard(a["tokens"], b["tokens"]) >= 0.38


def _is_path_grounded(rel_path: str, target_dir: Path | None, repo_root: Path | None) -> bool:
    clean = (rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not clean or ".." in Path(clean).parts:
        return False
    for base in (target_dir, repo_root):
        if base is None:
            continue
        try:
            cand = (base / clean).resolve()
            cand.relative_to(base.resolve())
            if cand.is_file():
                return True
        except Exception:
            continue
    return False


def _compute_seal_status(
    panel: dict[str, Any],
    clusters: list[dict[str, Any]],
    llm_verdicts: list[str],
    execution_mode: str = "explicit",
    review_binding_verified: bool = True,
) -> str:
    for c in clusters:
        dec = str((c.get("adjudication") or {}).get("decision") or "").upper()
        if dec == "ACCEPT_FINDING":
            return "BLOCKED_DEFECT_CONFIRMED"
    for c in clusters:
        dec = str((c.get("adjudication") or {}).get("decision") or "").upper()
        if dec == "ESCALATE":
            return "BLOCKED_ESCALATED"
    if not review_binding_verified:
        return "UNBOUND_IMPORT_ADVISORY"
    if not panel.get("independent_panel", False):
        return "DEGRADED_NON_INDEPENDENT"
    req_clusters = [c for c in clusters if c.get("requires_adjudication")]
    for c in req_clusters:
        if str((c.get("adjudication") or {}).get("status") or "") != "ADJUDICATED":
            return "PENDING_ADJUDICATION"
    if req_clusters:
        return "READY_ADJUDICATED"
    if llm_verdicts and all(v == "APPROVE" for v in llm_verdicts):
        return "READY_UNANIMOUS"
    return "PENDING_ADJUDICATION"


def synthesize_elenchus_verdict(
    reviews: list[dict[str, Any]],
    packet: dict[str, Any],
    target_dir: Path | None = None,
    execution_mode: str = "explicit",
    review_binding_verified: bool = True,
) -> dict[str, Any]:
    """
    Aggregate cross-lineage reviewer outputs into calibrated dispute clusters (`INDEPENDENT_CONVERGENCE`,
    `PROMPT_ANCHORED_ECHO`, `LINEAGE_SPLIT`, `INCOMPLETE_COVERAGE`, `SINGLE_LINEAGE_NOTE`), compute
    Hill independence `N_eff`, prompt-anchoring ratio, `disagreement_index`, and deterministic `seal_status`.
    """
    t_dir = Path(target_dir).resolve() if target_dir is not None else Path(packet["target_dir"]).resolve()
    r_root = Path(packet["repo_root"]).resolve()

    normalized_reviews: list[dict[str, Any]] = []
    for r in reviews:
        if not isinstance(r, dict):
            raise ValueError("Malformed reviewer record: expected dict.")
        mid = str(r.get("model_id") or r.get("model") or "unknown").strip()
        raw_exec_st = _normalize_execution_status(r.get("execution_status"))
        if "raw_text" in r and "findings" not in r:
            nr = parse_reviewer_output(mid, str(r["raw_text"]))
            if raw_exec_st == "FAILED":
                nr["execution_status"] = "FAILED"
        else:
            if "findings" in r:
                raw_f_val = r["findings"]
                if not isinstance(raw_f_val, list) or not all(isinstance(f, dict) for f in raw_f_val):
                    raise ValueError("Malformed reviewer `findings`: expected list of finding objects.")
                raw_f = raw_f_val
            else:
                raw_f = []
            norm_f = [_normalize_finding_dict(f) for f in raw_f]
            nr = {
                "model_id": mid,
                "lineage": classify_model_lineage(mid),
                "verdict": _normalize_verdict(str(r.get("verdict") or ""), norm_f),
                "execution_status": raw_exec_st,
                "summary": str(r.get("summary") or "").strip(),
                "findings": norm_f,
            }
        normalized_reviews.append(_scrub_review_dict(nr))

    panel = compute_effective_panel_size(normalized_reviews)
    llm_lineages_present = set(panel["distinct_llm_lineages"])
    prompt_instr = str(packet.get("prompt_instructions_and_intent") or packet.get("intent") or "")
    prompt_ngrams = _extract_ngrams_2_3(prompt_instr)
    prompt_tokens = _content_tokens(prompt_instr)

    # Flatten findings with metadata
    flat_items: list[dict[str, Any]] = []
    for r in normalized_reviews:
        for f in r["findings"]:
            if not f.get("claim"):
                continue
            item = dict(f)
            item["model_id"] = r["model_id"]
            item["lineage"] = r["lineage"]
            item["tokens"] = _content_tokens(f"{f['symbol']} {f['claim']} {f['evidence']}")
            item["ngrams"] = _extract_ngrams_2_3(f"{f['claim']} {f['evidence']}")
            flat_items.append(item)

    # Synthesize split/challenge clusters over verified non-failed LLM reviewers; any failed reviewer
    # (including a failed deterministic probe) injects CHALLENGE so failures never become READY_UNANIMOUS.
    llm_reviews = [
        r
        for r in normalized_reviews
        if classify_model_lineage(r["model_id"]) in _VERIFIED_LLM_LINEAGES and r["execution_status"] != "FAILED"
    ]
    llm_verdicts = [
        "CHALLENGE" if r["execution_status"] == "FAILED" else r["verdict"]
        for r in normalized_reviews
        if r["lineage"] != "deterministic" or r["execution_status"] == "FAILED"
    ]
    has_split_verdict = ("APPROVE" in [r["verdict"] for r in llm_reviews]) and any(
        r["verdict"] in ("CHALLENGE", "VETO") for r in llm_reviews
    )

    # Cluster findings greedily
    raw_clusters: list[list[dict[str, Any]]] = []
    for item in flat_items:
        placed = False
        for grp in raw_clusters:
            if any(_findings_match(item, rep) for rep in grp):
                grp.append(item)
                placed = True
                break
        if not placed:
            raw_clusters.append([item])

    clusters: list[dict[str, Any]] = []
    for grp in raw_clusters:
        best_sev = min((g["severity"] for g in grp), key=lambda s: _SEVERITY_RANK.get(s, 9))
        cats = Counter(g["category"] for g in grp)
        best_cat = cats.most_common(1)[0][0]
        file_paths = [g["file_path"] for g in grp if g["file_path"]]
        primary_file = Counter(file_paths).most_common(1)[0][0] if file_paths else ""
        lines_non_zero = [g["line"] for g in grp if g["line"] > 0]
        primary_line = min(lines_non_zero) if lines_non_zero else 0
        syms = [g["symbol"] for g in grp if g["symbol"]]
        primary_sym = Counter(syms).most_common(1)[0][0] if syms else ""

        lineages = sorted({g["lineage"] for g in grp})
        llm_lin_in_cluster = sorted({g["lineage"] for g in grp if g["lineage"] in _VERIFIED_LLM_LINEAGES})
        model_ids = sorted({g["model_id"] for g in grp})
        grounded = _is_path_grounded(primary_file, t_dir, r_root)

        # Compute anchoring_ratio across distinct lineages in the cluster
        anchoring_ratio = 0.0
        if len(llm_lin_in_cluster) >= 2:
            per_lin_grams: dict[str, set[str]] = {}
            per_lin_tokens: dict[str, set[str]] = {}
            for g in grp:
                if g["lineage"] not in _VERIFIED_LLM_LINEAGES:
                    continue
                per_lin_grams.setdefault(g["lineage"], set()).update(g["ngrams"])
                per_lin_tokens.setdefault(g["lineage"], set()).update(g["tokens"])
            gram_df = Counter(ng for lin_grams in per_lin_grams.values() for ng in lin_grams)
            shared_grams = {ng for ng, cnt in gram_df.items() if cnt >= 2}
            tok_df = Counter(tk for lin_toks in per_lin_tokens.values() for tk in lin_toks)
            shared_toks = {tk for tk, cnt in tok_df.items() if cnt >= 2}
            ngram_ratio = (len(shared_grams & prompt_ngrams) / len(shared_grams)) if shared_grams else 0.0
            tok_ratio = (len(shared_toks & prompt_tokens) / len(shared_toks)) if shared_toks else 0.0
            anchoring_ratio = ngram_ratio if grounded else max(ngram_ratio, tok_ratio)
        anchoring_ratio = round(anchoring_ratio, 4)

        if not panel["independent_panel"]:
            signal_class = "SINGLE_LINEAGE_NOTE"
        elif len(llm_lin_in_cluster) >= 2:
            if anchoring_ratio >= ANCHOR_ECHO_THRESHOLD and not grounded:
                signal_class = "PROMPT_ANCHORED_ECHO"
            else:
                signal_class = "INDEPENDENT_CONVERGENCE"
        else:
            signal_class = "LINEAGE_SPLIT"

        requires_adj = (best_sev in ("P0", "P1")) or (
            signal_class == "LINEAGE_SPLIT" and best_sev in ("P0", "P1", "P2")
        )
        rep_claim = max((g["claim"] for g in grp), key=len)
        rep_evidence = max((g["evidence"] for g in grp if g["evidence"]), key=len, default="")

        clusters.append(
            {
                "cluster_id": "",
                "severity": best_sev,
                "category": best_cat,
                "signal_class": signal_class,
                "file_path": primary_file,
                "line": primary_line,
                "symbol": primary_sym,
                "grounded_in_repo": grounded,
                "anchoring_ratio": anchoring_ratio,
                "lineages": lineages,
                "model_ids": model_ids,
                "claim": rep_claim,
                "evidence": rep_evidence,
                "requires_adjudication": requires_adj,
                "adjudication": {
                    "status": "PENDING" if requires_adj else "ADVISORY",
                    "decision": None,
                    "adjudicator": None,
                    "reason": "",
                    "adjudicated_at": None,
                },
                "member_findings": [
                    {
                        "model_id": g["model_id"],
                        "lineage": g["lineage"],
                        "severity": g["severity"],
                        "file_path": g["file_path"],
                        "line": g["line"],
                        "symbol": g["symbol"],
                        "claim": g["claim"],
                    }
                    for g in grp
                ],
            }
        )

    # Sort deterministically by severity then file/claim and assign sequential ELC-001 IDs
    clusters.sort(key=lambda c: (_SEVERITY_RANK.get(c["severity"], 9), c["file_path"], c["claim"]))
    for idx, c in enumerate(clusters, 1):
        c["cluster_id"] = f"ELC-{idx:03d}"

    # Guarantee top-level verdict splits or non-APPROVE challenges always have >= 1 adjudicable cluster
    has_split_cluster = any(c["signal_class"] == "LINEAGE_SPLIT" for c in clusters)
    if panel["independent_panel"] and has_split_verdict and not has_split_cluster:
        dissenters = [r for r in llm_reviews if r["verdict"] in ("CHALLENGE", "VETO")]
        approvers = [r for r in llm_reviews if r["verdict"] == "APPROVE"]
        if any(not d["findings"] for d in dissenters) or not clusters:
            clusters.append(
                {
                    "cluster_id": "ELC-SPLIT-001",
                    "severity": "P1",
                    "category": "provenance",
                    "signal_class": "LINEAGE_SPLIT",
                    "file_path": "",
                    "line": 0,
                    "symbol": "",
                    "grounded_in_repo": False,
                    "anchoring_ratio": 0.0,
                    "lineages": sorted({d["lineage"] for d in dissenters}),
                    "model_ids": sorted({d["model_id"] for d in dissenters}),
                    "claim": (
                        f"Cross-lineage top-level verdict split: "
                        f"{sorted({d['lineage'] for d in dissenters})} voted {[d['verdict'] for d in dissenters]} "
                        f"while {sorted({a['lineage'] for a in approvers})} voted APPROVE."
                    ),
                    "evidence": "; ".join(d.get("summary", "") for d in dissenters if d.get("summary"))[:240],
                    "requires_adjudication": True,
                    "adjudication": {
                        "status": "PENDING",
                        "decision": None,
                        "adjudicator": None,
                        "reason": "",
                        "adjudicated_at": None,
                    },
                    "member_findings": [],
                }
            )
    elif panel["independent_panel"] and not clusters and any(v in ("CHALLENGE", "VETO") for v in llm_verdicts):
        clusters.append(
            {
                "cluster_id": "ELC-CHALLENGE-001",
                "severity": "P1",
                "category": "provenance",
                "signal_class": "INDEPENDENT_CONVERGENCE",
                "file_path": "",
                "line": 0,
                "symbol": "",
                "grounded_in_repo": False,
                "anchoring_ratio": 0.0,
                "lineages": sorted(llm_lineages_present),
                "model_ids": sorted({r["model_id"] for r in llm_reviews}),
                "claim": "Independent panel challenged/vetoed completion without itemized findings.",
                "evidence": "; ".join(r.get("summary", "") for r in llm_reviews if r.get("summary"))[:240],
                "requires_adjudication": True,
                "adjudication": {
                    "status": "PENDING",
                    "decision": None,
                    "adjudicator": None,
                    "reason": "",
                    "adjudicated_at": None,
                },
                "member_findings": [],
            }
        )

    # Always surface incomplete diff coverage uniformly across all execution modes whenever diff is truncated
    diff_truncated = bool((packet.get("bindings") or {}).get("diff_truncated", False))
    incomplete_coverage = diff_truncated
    if (
        diff_truncated
        and panel["independent_panel"]
        and not any(c.get("cluster_id") == "ELC-COVERAGE-001" for c in clusters)
    ):
        clusters.append(
            {
                "cluster_id": "ELC-COVERAGE-001",
                "severity": "P1",
                "category": "provenance",
                "signal_class": "INCOMPLETE_COVERAGE",
                "file_path": "",
                "line": 0,
                "symbol": "",
                "grounded_in_repo": False,
                "anchoring_ratio": 0.0,
                "lineages": sorted(llm_lineages_present),
                "model_ids": sorted({r["model_id"] for r in llm_reviews}),
                "claim": "Challenge packet diff exceeded budget and was truncated before tool-less reviewer inspection.",
                "evidence": "bindings.diff_truncated=True; tool-less reviewers could not inspect the complete diff.",
                "requires_adjudication": True,
                "adjudication": {
                    "status": "PENDING",
                    "decision": None,
                    "adjudicator": None,
                    "reason": "",
                    "adjudicated_at": None,
                },
                "member_findings": [],
            }
        )

    # Ensure if an independent panel issued CHALLENGE/VETO and existing clusters were all advisory,
    # the top cluster becomes explicitly adjudicable so the state machine can never deadlock.
    if (
        panel["independent_panel"]
        and any(v in ("CHALLENGE", "VETO") for v in llm_verdicts)
        and clusters
        and not any(c["requires_adjudication"] for c in clusters)
    ):
        clusters[0]["requires_adjudication"] = True
        clusters[0]["adjudication"]["status"] = "PENDING"

    # Disagreement index: blend of split cluster ratio and cross-lineage verdict divergence
    n_clusters = len(clusters)
    n_split = sum(1 for c in clusters if c["signal_class"] == "LINEAGE_SPLIT")
    n_conv = sum(1 for c in clusters if c["signal_class"] == "INDEPENDENT_CONVERGENCE")
    n_echo = sum(1 for c in clusters if c["signal_class"] == "PROMPT_ANCHORED_ECHO")
    n_single = sum(1 for c in clusters if c["signal_class"] == "SINGLE_LINEAGE_NOTE")
    n_req = sum(1 for c in clusters if c["requires_adjudication"])

    lineage_verdict_scores: list[float] = []
    for lin in sorted(llm_lineages_present):
        v_list = [_VERDICT_SCORE.get(r["verdict"], 0.5) for r in llm_reviews if r["lineage"] == lin]
        if v_list:
            lineage_verdict_scores.append(max(v_list))
    verdict_spread = (
        (max(lineage_verdict_scores) - min(lineage_verdict_scores)) if len(lineage_verdict_scores) >= 2 else 0.0
    )
    split_ratio = (n_split / n_clusters) if n_clusters > 0 else 0.0
    if n_clusters > 0:
        disagreement_index = round(min(1.0, max(0.0, 0.7 * split_ratio + 0.3 * verdict_spread)), 4)
    else:
        disagreement_index = round(verdict_spread, 4)

    seal_status = _compute_seal_status(
        panel,
        clusters,
        llm_verdicts,
        execution_mode=execution_mode,
        review_binding_verified=review_binding_verified,
    )
    reviewer_summaries = [
        {
            "model_id": r["model_id"],
            "lineage": r["lineage"],
            "verdict": r["verdict"],
            "execution_status": r["execution_status"],
            "findings_count": len(r["findings"]),
            "summary": r["summary"][:280],
        }
        for r in normalized_reviews
    ]

    return {
        "panel": panel,
        "reviewer_summaries": reviewer_summaries,
        "raw_reviews": normalized_reviews,
        "clusters": clusters,
        "metrics": {
            "total_clusters": n_clusters,
            "independent_convergence_count": n_conv,
            "prompt_anchored_echo_count": n_echo,
            "lineage_split_count": n_split,
            "single_lineage_count": n_single,
            "requires_adjudication_count": n_req,
            "incomplete_coverage": incomplete_coverage,
            "disagreement_index": disagreement_index,
        },
        "seal_status": seal_status,
    }

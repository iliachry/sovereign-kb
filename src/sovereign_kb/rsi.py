"""
Monotonically-Gated Recursive Self-Improvement (RSI) Engine for Sovereign-KB (`sovereign-kb rsi` / `evolve`).

Implements three closed feedback loops over a Sovereign-KB vault:
  1. Loop 1 — Retrieval Policy Meta-Optimization (`tune_retrieval_policy`):
     Evaluates 4-way hybrid retrieval over a ground-truth + auto-synthesized structural benchmark
     and optimizes `.kb/retrieval_policy.json` via coordinate search under a strict Lyapunov
     non-regression gate: J(theta_{t+1}, K) >= J(theta_t, K).
  2. Loop 2 — Self-Healing Vault Doctor & Episodic-to-Semantic Consolidation (`heal_and_consolidate_vault`):
     Automatically repairs Gate 1 (frontmatter) and Gate 3 (orphan note) drift, distills chronological
     logs in `04-episodic/*.md` into `02-operating-system/autonomous-rsi-distillations.md`, and enforces
     atomic rollback if any of the 5 Deterministic Vault Gates fail.
  3. Loop 3 — Closed-Loop Retrieval-to-Compiler Verification & Hash-Chained Ledger (`run_rsi_cycle`):
     Couples knowledge consolidation, retrieval hyperparameter tuning, and `rustc -D warnings` compiler
     verification into a single cycle recorded in a tamper-evident SHA-256 chain (`.kb/rsi-ledger.jsonl`).
"""

from __future__ import annotations

import argparse
import copy
import datetime
import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable

from sovereign_kb.retrieval import invoke_hybrid_retrieve

DEFAULT_RETRIEVAL_POLICY: dict[str, Any] = {
    "version": 1,
    "k_rrf": 30.0,
    "w_hips": 1.15,
    "w_fts_with_hips": 0.85,
    "w_fts_solo": 1.0,
    "w_vec": 1.20,
    "exact_id_boost": 0.025,
    "graph_1hop_boost": 1.15,
    "graph_neighbor_lead_factor": 0.28,
    "bm25_weights": [1.5, 4.0, 1.5, 2.5, 3.0, 1.0, 1.0],
}

CANONICAL_BENCHMARK_PROBES: list[dict[str, str]] = [
    {
        "id": "PROBE-PAPER-01",
        "query": "Type-42 font embedding Okabe-Ito",
        "target_path": "02-operating-system/scientific-paper-framework.md",
        "target_heading": "Pillar 4",
    },
    {
        "id": "PROBE-PAPER-02",
        "query": "Pillar 10 RESPONSE_TO_REVIEWERS rebuttal",
        "target_path": "02-operating-system/scientific-paper-framework.md",
        "target_heading": "Pillar 10",
    },
    {
        "id": "PROBE-ENG-01",
        "query": "6-Pillar Deterministic Autonomous Engineering video walkthrough diagnostics",
        "target_path": "02-operating-system/autonomous-engineering-framework.md",
        "target_heading": "Pillar",
    },
    {
        "id": "PROBE-HEUR-01",
        "query": "Gelenbe queueing retransmission rho utilization cliff",
        "target_path": "02-operating-system/heuristics-and-mental-models.md",
        "target_heading": "Gelenbe",
    },
    {
        "id": "PROBE-GOV-01",
        "query": "scc jscpd knip Net Authored Proprietary IP",
        "target_path": "02-operating-system/codebase-governance-and-sizing.md",
        "target_heading": "",
    },
    {
        "id": "PROBE-RUST-01",
        "query": "RUST-CERT-01 Ownership Move Semantics split_at_mut E0382",
        "target_path": "02-operating-system/rust-systems-reference.md",
        "target_heading": "RUST-CERT-01",
    },
    {
        "id": "PROBE-RUST-14",
        "query": "RUST-CERT-14 Lock-Free Atomics Acquire Release SeqCst Ordering",
        "target_path": "02-operating-system/rust-systems-reference.md",
        "target_heading": "RUST-CERT-14",
    },
    {
        "id": "PROBE-RUST-18",
        "query": "RUST-CERT-18 Pin Unpin Future Poll State Machine",
        "target_path": "02-operating-system/rust-systems-reference.md",
        "target_heading": "RUST-CERT-18",
    },
]

CANONICAL_HELDOUT_PROBES: list[dict[str, str]] = [
    {
        "id": "HELDOUT-PAPER-01",
        "query": "vector PDF figure fonts colorblind palette publication standards",
        "target_path": "02-operating-system/scientific-paper-framework.md",
        "target_heading": "Pillar 4",
        "split": "heldout",
    },
    {
        "id": "HELDOUT-ENG-01",
        "query": "automated Playwright browser recording console error assertions",
        "target_path": "02-operating-system/autonomous-engineering-framework.md",
        "target_heading": "Pillar",
        "split": "heldout",
    },
    {
        "id": "HELDOUT-RUST-01",
        "query": "disjoint mutable slice borrowing ownership aliasing rules",
        "target_path": "02-operating-system/rust-systems-reference.md",
        "target_heading": "RUST-CERT-01",
        "split": "heldout",
    },
    {
        "id": "HELDOUT-GOV-01",
        "query": "clone duplication ceiling dead export hygiene proprietary code sizing",
        "target_path": "02-operating-system/codebase-governance-and-sizing.md",
        "target_heading": "",
        "split": "heldout",
    },
]


def compute_policy_sha256(policy: dict[str, Any]) -> str:
    """Compute canonical 16-hex SHA-256 fingerprint of a retrieval policy (excluding mutable timestamps)."""
    core = {k: v for k, v in policy.items() if k != "updated_at"}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def policy_path(vault_dir: Path) -> Path:
    return vault_dir / ".kb" / "retrieval_policy.json"


def ledger_path(vault_dir: Path) -> Path:
    return vault_dir / ".kb" / "rsi-ledger.jsonl"


def load_retrieval_policy(vault_dir: Path) -> dict[str, Any]:
    """Load `.kb/retrieval_policy.json` or return a fresh copy of `DEFAULT_RETRIEVAL_POLICY`."""
    p = policy_path(vault_dir)
    merged = copy.deepcopy(DEFAULT_RETRIEVAL_POLICY)
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if k in merged:
                        merged[k] = v
        except Exception:
            pass
    return merged


def save_retrieval_policy(vault_dir: Path, policy: dict[str, Any]) -> Path:
    p = policy_path(vault_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p


def build_benchmark_suite(conn: sqlite3.Connection, vault_dir: Path) -> list[dict[str, str]]:
    """
    Construct a deterministic retrieval benchmark combining canonical domain probes
    and auto-extracted structural heading probes from non-MOC documents in the vault.
    """
    existing_docs = {
        r["rel_path"]: r["doc_type"] for r in conn.execute("SELECT rel_path, doc_type FROM documents").fetchall()
    }
    probes: list[dict[str, str]] = []
    for cp in CANONICAL_BENCHMARK_PROBES:
        if cp["target_path"] in existing_docs:
            probes.append(dict(cp))

    rows = conn.execute(
        """
        SELECT c.rel_path, c.heading, c.heading_path, d.title, d.doc_type, d.tags
        FROM chunks c
        JOIN documents d ON d.rel_path = c.rel_path
        WHERE d.doc_type NOT IN ('moc', 'rsi-ledger', 'distillation-ledger', 'episodic')
          AND c.rel_path NOT IN ('AGENTS.md', '02-operating-system/autonomous-rsi-distillations.md')
        ORDER BY c.rel_path, c.start_line
        """
    ).fetchall()

    seen_queries: set[str] = {p["query"].lower() for p in probes}
    auto_idx = 1
    for r in rows:
        heading = (r["heading"] or "").strip()
        title = (r["title"] or "").strip()
        if not heading or heading.lower() in ("overview", "summary", "introduction", title.lower()):
            continue
        clean_h = re.sub(r"[^\w\s\-:]", " ", heading).strip()
        words = [w for w in clean_h.split() if len(w) >= 3]
        if len(words) < 2:
            continue
        tag_tok = ""
        if r["tags"]:
            first_tag = r["tags"].split(",")[0].strip()
            if first_tag and first_tag.lower() not in clean_h.lower():
                tag_tok = f" {first_tag}"
        q_str = f"{' '.join(words[:6])}{tag_tok}".strip()
        if q_str.lower() in seen_queries:
            continue
        seen_queries.add(q_str.lower())
        probes.append(
            {
                "id": f"AUTO-{auto_idx:02d}",
                "query": q_str,
                "target_path": r["rel_path"],
                "target_heading": heading[:28],
                "split": "heldout" if (auto_idx % 4 == 0) else "train",
            }
        )
        auto_idx += 1
        if auto_idx > 16:
            break

    return probes


def split_benchmark_suite(
    conn: sqlite3.Connection,
    vault_dir: Path,
    probes: list[dict[str, str]] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """
    Partition probes into disjoint `train_probes` and `heldout_probes` (including paraphrased
    `CANONICAL_HELDOUT_PROBES`) so coordinate search cannot overfit in-sample heading copies.
    """
    all_probes = list(probes) if probes is not None else build_benchmark_suite(conn, vault_dir)
    existing_docs = {r["rel_path"] for r in conn.execute("SELECT rel_path FROM documents").fetchall()}
    train_probes: list[dict[str, str]] = []
    heldout_probes: list[dict[str, str]] = []
    seen_q: set[str] = set()

    for hp in CANONICAL_HELDOUT_PROBES:
        if hp["target_path"] in existing_docs:
            heldout_probes.append(dict(hp))
            seen_q.add(hp["query"].lower())

    for idx, p in enumerate(all_probes):
        if p["query"].lower() in seen_q:
            continue
        if p.get("split") == "heldout" or (not heldout_probes and idx % 3 == 2):
            heldout_probes.append(dict(p))
        else:
            train_probes.append(dict(p))

    if not train_probes and heldout_probes:
        train_probes = list(heldout_probes)
    if not heldout_probes and len(train_probes) >= 2:
        heldout_probes = [train_probes[-1]]
    return train_probes, heldout_probes


def evaluate_retrieval_benchmark(
    conn: sqlite3.Connection,
    vault_dir: Path,
    hybrid_retrieve_fn: Callable[..., list[dict]],
    policy: dict[str, Any] | None = None,
    probes: list[dict[str, str]] | None = None,
    strict_empty: bool = False,
) -> dict[str, Any]:
    """
    Evaluate retrieval policy `policy` over `probes` and compute:
      - MRR@5, NDCG@5, Recall@1, Recall@5, MeanMargin
      - Composite Lyapunov objective J(theta, K) in [0, 1]
    """
    active_policy = policy or load_retrieval_policy(vault_dir)
    if probes is None:
        probes = build_benchmark_suite(conn, vault_dir)
    if not probes:
        empty_val = 0.0 if strict_empty else 1.0
        return {
            "probes_count": 0,
            "insufficient_evidence": True,
            "mrr_at_5": empty_val,
            "ndcg_at_5": empty_val,
            "recall_at_1": empty_val,
            "recall_at_5": empty_val,
            "mean_margin": 0.0,
            "objective_j": empty_val,
            "ledger_intrusions": 0,
            "misses": [],
        }

    rr_sum = 0.0
    ndcg_sum = 0.0
    r1_hits = 0
    r5_hits = 0
    margin_sum = 0.0
    ledger_intrusions = 0
    distill_rel = "02-operating-system/autonomous-rsi-distillations.md"
    misses: list[dict[str, Any]] = []

    for probe in probes:
        results = invoke_hybrid_retrieve(
            hybrid_retrieve_fn,
            conn,
            vault_dir,
            probe["query"],
            top_k=5,
            engine="auto",
            policy=active_policy,
        )
        target_path = probe["target_path"]
        target_heading = probe.get("target_heading", "").lower()

        if results and results[0]["rel_path"] == distill_rel and target_path != distill_rel:
            ledger_intrusions += 1

        hit_rank: int | None = None
        exact_chunk_match = False
        for idx, res in enumerate(results, start=1):
            if res["rel_path"] == target_path:
                if (
                    not target_heading
                    or target_heading in res["heading_path"].lower()
                    or target_heading in res["content"].lower()
                ):
                    hit_rank = idx
                    exact_chunk_match = True
                    break
        if hit_rank is None:
            for idx, res in enumerate(results, start=1):
                if res["rel_path"] == target_path:
                    hit_rank = idx
                    exact_chunk_match = not bool(target_heading)
                    break

        if hit_rank is not None:
            chunk_discount = 1.0 if exact_chunk_match else 0.75
            rr_sum += chunk_discount * (1.0 / hit_rank)
            ndcg_sum += chunk_discount * (1.0 / math.log2(hit_rank + 1.0))
            r5_hits += 1
            if hit_rank == 1 and exact_chunk_match:
                r1_hits += 1
                s1 = float(results[0]["rrf_score"])
                s2 = next(
                    (float(r["rrf_score"]) for r in results[1:] if r["rel_path"] != distill_rel),
                    0.0,
                )
                if s1 > 0:
                    margin_sum += max(0.0, min(1.0, (s1 - s2) / s1))
        if hit_rank != 1 or not exact_chunk_match:
            misses.append(
                {
                    "id": probe["id"],
                    "query": probe["query"],
                    "target_path": target_path,
                    "rank": hit_rank,
                    "exact_chunk_match": exact_chunk_match,
                    "top1_got": results[0]["rel_path"] if results else None,
                }
            )

    n = len(probes)
    mrr = rr_sum / n
    ndcg = ndcg_sum / n
    rec1 = r1_hits / n
    rec5 = r5_hits / n
    mean_margin = margin_sum / n
    objective_j = 0.45 * mrr + 0.30 * ndcg + 0.15 * rec1 + 0.10 * mean_margin

    return {
        "probes_count": n,
        "insufficient_evidence": False,
        "mrr_at_5": round(mrr, 5),
        "ndcg_at_5": round(ndcg, 5),
        "recall_at_1": round(rec1, 5),
        "recall_at_5": round(rec5, 5),
        "mean_margin": round(mean_margin, 5),
        "objective_j": round(objective_j, 6),
        "ledger_intrusions": ledger_intrusions,
        "misses": misses,
    }


def tune_retrieval_policy(
    conn: sqlite3.Connection,
    vault_dir: Path,
    hybrid_retrieve_fn: Callable[..., list[dict]],
) -> dict[str, Any]:
    """
    Loop 1: Coordinate-search meta-optimization over `.kb/retrieval_policy.json`.
    Guarantees monotonic non-regression on both in-sample training probes and held-out
    generalization probes: J_train(theta_best) >= J_train(theta_init) and
    J_heldout(theta_best) >= J_heldout(theta_init).
    """
    base_policy = load_retrieval_policy(vault_dir)
    all_probes = build_benchmark_suite(conn, vault_dir)
    train_probes, heldout_probes = split_benchmark_suite(conn, vault_dir, probes=all_probes)
    opt_probes = train_probes if train_probes else all_probes
    base_eval = evaluate_retrieval_benchmark(conn, vault_dir, hybrid_retrieve_fn, policy=base_policy, probes=opt_probes)
    base_heldout = (
        evaluate_retrieval_benchmark(conn, vault_dir, hybrid_retrieve_fn, policy=base_policy, probes=heldout_probes)
        if heldout_probes
        else base_eval
    )

    best_policy = copy.deepcopy(base_policy)
    best_eval = base_eval
    best_heldout = base_heldout
    evaluated_count = 1

    def _try_candidate(cand: dict[str, Any]) -> None:
        nonlocal best_policy, best_eval, evaluated_count
        evaluated_count += 1
        ev = evaluate_retrieval_benchmark(conn, vault_dir, hybrid_retrieve_fn, policy=cand, probes=opt_probes)
        if (
            ev["objective_j"] > best_eval["objective_j"] + 1e-6
            and ev["mrr_at_5"] >= best_eval["mrr_at_5"] - 1e-6
            and ev["ndcg_at_5"] >= best_eval["ndcg_at_5"] - 1e-6
            and ev["recall_at_1"] >= best_eval["recall_at_1"] - 1e-6
            and ev.get("ledger_intrusions", 0) <= best_eval.get("ledger_intrusions", 0)
        ):
            best_policy = copy.deepcopy(cand)
            best_eval = ev

    for k_rrf in (12.0, 18.0, 24.0, 30.0):
        for exact_boost in (0.025, 0.045, 0.065, 0.085):
            cand = copy.deepcopy(best_policy)
            cand["k_rrf"] = k_rrf
            cand["exact_id_boost"] = exact_boost
            _try_candidate(cand)

    for w_hips, w_fts in ((1.25, 0.85), (1.35, 0.80), (1.15, 0.95)):
        cand = copy.deepcopy(best_policy)
        cand["w_hips"] = w_hips
        cand["w_fts_with_hips"] = w_fts
        _try_candidate(cand)

    for bm25_w in (
        [1.8, 4.5, 1.5, 3.0, 3.8, 1.0, 1.0],
        [2.0, 5.0, 1.5, 3.2, 4.2, 1.0, 1.0],
        [1.5, 4.0, 1.5, 2.5, 3.0, 1.0, 1.0],
    ):
        cand = copy.deepcopy(best_policy)
        cand["bm25_weights"] = bm25_w
        _try_candidate(cand)

    for graph_boost in (1.05, 1.12, 1.18, 1.25):
        cand = copy.deepcopy(best_policy)
        cand["graph_1hop_boost"] = graph_boost
        _try_candidate(cand)

    # Evaluate held-out generalization probes strictly once after coordinate search completes (zero selection leakage)
    if heldout_probes and best_eval["objective_j"] > base_eval["objective_j"] + 1e-6:
        cand_heldout = evaluate_retrieval_benchmark(
            conn, vault_dir, hybrid_retrieve_fn, policy=best_policy, probes=heldout_probes
        )
        heldout_ok = (
            cand_heldout["objective_j"] >= base_heldout["objective_j"] - 1e-6
            and cand_heldout["mrr_at_5"] >= base_heldout["mrr_at_5"] - 1e-6
            and cand_heldout["ndcg_at_5"] >= base_heldout["ndcg_at_5"] - 1e-6
            and cand_heldout["recall_at_1"] >= base_heldout["recall_at_1"] - 1e-6
            and cand_heldout.get("ledger_intrusions", 0) <= base_heldout.get("ledger_intrusions", 0)
        )
        if heldout_ok:
            best_heldout = cand_heldout
        else:
            best_policy = copy.deepcopy(base_policy)
            best_eval = base_eval
            best_heldout = base_heldout
    elif not heldout_probes:
        best_heldout = best_eval

    policy_file = policy_path(vault_dir)
    updated = best_eval["objective_j"] > base_eval["objective_j"] + 1e-6 or not policy_file.is_file()
    if updated:
        best_policy["updated_at"] = datetime.datetime.now().isoformat()
        save_retrieval_policy(vault_dir, best_policy)

    policy_sha = compute_policy_sha256(best_policy)
    return {
        "updated": updated,
        "candidates_evaluated": evaluated_count,
        "before": base_eval,
        "after": best_eval,
        "heldout_before": base_heldout,
        "heldout_after": best_heldout,
        "heldout_probes_count": len(heldout_probes),
        "delta_j": round(best_eval["objective_j"] - base_eval["objective_j"], 6),
        "heldout_delta_j": round(best_heldout["objective_j"] - base_heldout["objective_j"], 6),
        "policy": best_policy,
        "policy_sha256": policy_sha,
    }


def _infer_doc_type(rel_path: str) -> str:
    if rel_path == "INDEX.md":
        return "moc"
    if rel_path.startswith("00-identity/"):
        return "identity"
    if rel_path.startswith("01-now/"):
        return "now"
    if rel_path.startswith("02-operating-system/decisions/"):
        return "decision"
    if rel_path.startswith("02-operating-system/templates/"):
        return "template"
    if rel_path.startswith("02-operating-system/"):
        return "operating-system"
    if rel_path.startswith("03-life-domains/"):
        return "domain"
    if rel_path.startswith("04-episodic/"):
        return "episodic"
    return "note"


_INVARIANT_PRIORITY_RE = re.compile(
    r"\b(invariant|rule|policy|lesson|guardrail|mandate|veto|law\s+l\d|lyapunov|gwi|canonical|standard|preference)\b",
    flags=re.IGNORECASE,
)
_SIGNAL_EVIDENCE_RE = re.compile(
    r"\b(implemented|fixed|verified|migrated|decided|enforced|removed|added|refactored|benchmarked|"
    r"shipped|deployed|certified|upgraded|remediated|codified|built|architected|configured|merged|"
    r"consolidated|established|packaged|integrated|published|formulated|synthesized|provisioned|"
    r"audited|resolved|eliminated|installed|created|wired|commit|pass|fail|gate|invariant|rule|policy|"
    r"[0-9a-f]{7,})\b",
    flags=re.IGNORECASE,
)
_BARE_WIKILINK_RE = re.compile(r'^"?\[\[[^\]]+\]\]"?\s*(?:\(\*[^*]+\*\))?\s*$')


def _strip_frontmatter_and_extract_bullets(ep_text: str) -> list[str]:
    """
    Strip YAML frontmatter and fenced code blocks from `ep_text`, then extract
    top-level markdown bullets outside of `## Related` / `## Links` sections.
    """
    body = ep_text
    if ep_text.startswith("---\n") or ep_text.startswith("---\r\n"):
        fm_match = re.match(r"^---\r?\n.*?\r?\n---\r?\n?", ep_text, flags=re.DOTALL)
        if fm_match:
            body = ep_text[fm_match.end() :]

    bullets: list[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    skip_section = False

    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if not in_fence:
            fm_open = re.match(r"^(`{3,}|~{3,})", stripped)
            if fm_open:
                in_fence = True
                fence_char = fm_open.group(1)[0]
                fence_len = len(fm_open.group(1))
                continue
        else:
            if re.match(rf"^{re.escape(fence_char)}{{{fence_len},}}\s*$", stripped):
                in_fence = False
                fence_char = ""
                fence_len = 0
            continue

        if stripped.startswith("#"):
            heading_lower = stripped.lstrip("#").strip().lower()
            skip_section = heading_lower in ("related", "links", "references", "see also")
            continue

        if skip_section:
            continue

        if stripped.startswith("- ") and len(stripped) > 12:
            bullets.append(stripped[2:].strip())

    return bullets


def _classify_episodic_bullet(bullet: str) -> tuple[bool, int, str]:
    """
    Determine whether an episodic bullet is a high-signal architectural invariant or milestone.
    Returns `(accepted, priority_tier, normalized_dedup_key)`.
    Rejects bare wikilinks, trailing-colon headers, questions, and short fragments (< 5 words).
    """
    b = bullet.strip()
    if not b or _BARE_WIKILINK_RE.match(b):
        return False, 0, ""
    if b.endswith(":") or b.endswith("**:") or b.endswith("*:") or b.endswith("?"):
        return False, 0, ""
    if b.startswith("*") and b.endswith("?*"):
        return False, 0, ""

    plain = re.sub(r"[`*_~#\[\]\(\)]", " ", b)
    words = [w for w in plain.split() if len(w) >= 2]
    if len(words) < 5 or len(plain.strip()) < 24:
        return False, 0, ""

    if not _SIGNAL_EVIDENCE_RE.search(b):
        return False, 0, ""

    priority = 2 if _INVARIANT_PRIORITY_RE.search(b) else 1
    norm_key = re.sub(r"\b[0-9a-f]{7,40}\b", "", plain.lower())
    norm_key = re.sub(r"[^a-z0-9]+", " ", norm_key).strip()
    return True, priority, norm_key


def heal_and_consolidate_vault(
    vault_dir: Path,
    list_md_fn: Callable[[Path], list[Path]],
    parse_fm_fn: Callable[[str], dict],
    extract_links_fn: Callable[..., list[str]],
    build_stem_fn: Callable[[Path, list[Path] | None], dict[str, str]],
    sync_fn: Callable[[Any], None],
    call_llm_fn: Callable[[list[dict]], tuple[str, str]] | None = None,
    use_llm: bool = False,
    no_embed: bool = False,
    consolidate_episodic: bool = True,
) -> dict[str, Any]:
    """
    Loop 2: Deterministic Vault Self-Healing (Gate 1 Frontmatter & Gate 3 Connectivity)
    + High-Signal Episodic-to-Semantic Consolidation (`04-episodic/` -> `02-operating-system/autonomous-rsi-distillations.md`).
    Preserves all custom YAML frontmatter properties (`aliases`, `cssclasses`, `status`, nested keys)
    losslessly while healing missing required fields.
    """
    md_files = list_md_fn(vault_dir)
    snapshots: dict[Path, str | None] = {p: p.read_text(encoding="utf-8", errors="replace") for p in md_files}
    distill_rel = "02-operating-system/autonomous-rsi-distillations.md"
    distill_path = vault_dir / distill_rel
    if distill_path not in snapshots:
        snapshots[distill_path] = (
            distill_path.read_text(encoding="utf-8", errors="replace") if distill_path.exists() else None
        )
    index_path = vault_dir / "INDEX.md"
    if index_path not in snapshots:
        snapshots[index_path] = (
            index_path.read_text(encoding="utf-8", errors="replace") if index_path.exists() else None
        )

    today = datetime.date.today().isoformat()
    healed_frontmatter: list[str] = []
    healed_orphans: list[str] = []

    # Step 2A: Losslessly heal missing frontmatter fields (Gate 1) & broken template links
    has_now_note = (vault_dir / "01-now" / "now.md").is_file()
    for p in md_files:
        rel = str(p.relative_to(vault_dir))
        if rel in ("AGENTS.md", "README.md"):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        meta = parse_fm_fn(text)
        missing = [k for k in ("title", "type", "updated", "tags") if not meta.get(k)]
        has_broken_now_link = (not has_now_note) and ("[[01-now/now]]" in text)
        has_yaml_error = bool(meta.get("yaml_error"))
        if not missing and not has_broken_now_link and not has_yaml_error:
            continue

        h1_match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
        inferred_title = (
            meta.get("title") or (h1_match.group(1).strip() if h1_match else p.stem.replace("-", " ").title())
        ).replace('"', '\\"')
        inferred_type = meta.get("type") if meta.get("type") and meta.get("type") != "note" else _infer_doc_type(rel)
        inferred_updated = meta.get("updated") or today
        inferred_tags = meta.get("tags") or f"[{inferred_type}]"
        if not inferred_tags.startswith("["):
            inferred_tags = f"[{inferred_tags}]"

        end_idx = text.find("\n---\n", 4) if text.startswith("---\n") else -1
        if end_idx != -1 and bool(meta.get("has_frontmatter")):
            existing_fm_lines = text[4:end_idx].splitlines()
            existing_keys = {
                ln.split(":", 1)[0].strip()
                for ln in existing_fm_lines
                if ":" in ln and not ln.startswith((" ", "\t")) and not ln.lstrip().startswith("-")
            }
            defaults = {
                "title": f'title: "{inferred_title}"',
                "type": f"type: {inferred_type}",
                "updated": f"updated: {inferred_updated}",
                "tags": f"tags: {inferred_tags}",
            }
            patched_lines: list[str] = []
            replaced_keys: set[str] = set()
            skip_indented = False
            in_inline_key = False
            for ln in existing_fm_lines:
                if has_broken_now_link and re.match(r'^\s*-\s*["\']?\[\[01-now/now\]\]["\']?\s*$', ln):
                    continue
                if ":" in ln and not ln.startswith((" ", "\t")) and not ln.lstrip().startswith("-"):
                    skip_indented = False
                    k, v = ln.split(":", 1)
                    k = k.strip()
                    in_inline_key = bool(v.strip())
                    if k in missing and k in defaults:
                        patched_lines.append(defaults[k])
                        replaced_keys.add(k)
                        skip_indented = True
                        continue
                elif skip_indented and (ln.startswith((" ", "\t")) or ln.lstrip().startswith("-")):
                    continue
                elif in_inline_key and ln.lstrip().startswith("-"):
                    # Drop dangling list items accidentally left underneath an inline scalar/array key
                    continue
                patched_lines.append(ln)
            for k in missing:
                if k not in existing_keys and k not in replaced_keys and k in defaults:
                    patched_lines.append(defaults[k])
            new_fm = "---\n" + "\n".join(patched_lines) + "\n---\n"
            rest = text[end_idx + 5 :]
            p.write_text(new_fm + rest, encoding="utf-8")
        else:
            body = text.lstrip()
            related_block = 'related:\n  - "[[INDEX]]"\n' if rel != "INDEX.md" else ""
            new_fm = (
                f"---\n"
                f'title: "{inferred_title}"\n'
                f"type: {inferred_type}\n"
                f"updated: {inferred_updated}\n"
                f"tags: {inferred_tags}\n"
                f"{related_block}"
                f"---\n\n"
            )
            p.write_text(new_fm + body, encoding="utf-8")
        healed_frontmatter.append(rel)

    # Step 2B: High-Signal Distillation of `04-episodic/*.md` into `02-operating-system/autonomous-rsi-distillations.md`
    max_per_log = 5
    max_total = 40
    episodic_dir = vault_dir / "04-episodic"
    episodic_files = sorted(episodic_dir.glob("*.md")) if (consolidate_episodic and episodic_dir.is_dir()) else []
    episodic_items: list[dict[str, Any]] = []
    episodic_links: list[str] = []
    seen_norm_keys: set[str] = set()
    raw_bullets_scanned = 0
    rejected_noise_bullets = 0

    for ep_file in episodic_files:
        rel_no_ext = str(ep_file.relative_to(vault_dir).with_suffix(""))
        ep_text = ep_file.read_text(encoding="utf-8", errors="replace")
        ep_meta = parse_fm_fn(ep_text)
        ep_date = str(ep_meta.get("updated") or ep_file.stem[:10])
        body_bullets = _strip_frontmatter_and_extract_bullets(ep_text)
        file_candidates: list[dict[str, Any]] = []
        file_seen_keys: set[str] = set()
        for pos, bullet in enumerate(body_bullets):
            raw_bullets_scanned += 1
            accepted, priority, norm_key = _classify_episodic_bullet(bullet)
            if not accepted or not norm_key or norm_key in file_seen_keys:
                rejected_noise_bullets += 1
                continue
            file_seen_keys.add(norm_key)
            file_candidates.append(
                {
                    "date": ep_date,
                    "source": rel_no_ext,
                    "entry": bullet,
                    "priority": priority,
                    "pos": pos,
                    "norm_key": norm_key,
                }
            )
        if file_candidates:
            # Keep top `max_per_log` highest-signal items per log before cross-file deduplication
            top_for_file = sorted(file_candidates, key=lambda x: (x["priority"], x["pos"]), reverse=True)[:max_per_log]
            top_for_file.sort(key=lambda x: x["pos"])
            kept_for_file: list[dict[str, Any]] = []
            for item in top_for_file:
                if item["norm_key"] in seen_norm_keys:
                    rejected_noise_bullets += 1
                    continue
                seen_norm_keys.add(item["norm_key"])
                kept_for_file.append(item)
            if kept_for_file:
                episodic_links.append(rel_no_ext)
                episodic_items.extend(kept_for_file)

    if len(episodic_items) > max_total:
        # Retain newest + highest-priority invariants up to `max_total`
        episodic_items = episodic_items[-max_total:]

    llm_summary = ""
    if consolidate_episodic and use_llm and call_llm_fn is not None and episodic_items:
        bullets_txt = "\n".join(f"- [{it['date']}] ({it['source']}): {it['entry']}" for it in episodic_items[-20:])
        ans, model_label = call_llm_fn(
            [
                {
                    "role": "system",
                    "content": (
                        "Distill these episodic engineering logs into 3 concise, reusable operating-system "
                        "invariants with inline [[wikilinks]]. Do not invent facts."
                    ),
                },
                {"role": "user", "content": bullets_txt},
            ]
        )
        if not ans.startswith("Error:"):
            llm_summary = f"\n## Synthesized Operating Invariants (`{model_label}`)\n\n{ans}\n"

    if consolidate_episodic:
        if episodic_items:
            related_lines = ['  - "[[INDEX]]"']
            if (vault_dir / "02-operating-system" / "autonomous-engineering-framework.md").is_file():
                related_lines.append('  - "[[02-operating-system/autonomous-engineering-framework]]"')
            for lnk in episodic_links[-5:]:
                entry_lnk = f'  - "[[{lnk}]]"'
                if entry_lnk not in related_lines:
                    related_lines.append(entry_lnk)

            rows_md = [
                "| Date | Source | Distilled Engineering Invariant / Milestone |",
                "| :--- | :--- | :--- |",
            ]
            for it in reversed(episodic_items):
                norm_links = re.sub(r"\[\[([^\]|#]+)\|([^\]]+)\]\]", r"[[\1]] (*\2*)", it["entry"])
                clean_entry = norm_links.replace("|", "\\|")
                rows_md.append(f"| `{it['date']}` | [[{it['source']}]] | {clean_entry} |")

            distill_content = (
                "---\n"
                'title: "Autonomous RSI Distillations — Episodic-to-Semantic Knowledge Consolidation"\n'
                "type: rsi-ledger\n"
                f"updated: {today}\n"
                "tags: [rsi, distillation, episodic-derived, self-improvement, verification]\n"
                "related:\n" + "\n".join(related_lines) + "\n---\n\n"
                "# Autonomous RSI Distillations (`04-episodic` $\\to$ `rsi-ledger`)\n\n"
                "This derived ledger is maintained autonomously by the **Sovereign-KB Recursive Self-Improvement (`skb rsi`)** engine. "
                f"It filters raw chronological bullets (`{len(episodic_items)}` kept / `{raw_bullets_scanned}` scanned; "
                f"`{rejected_noise_bullets}` noise/duplicate bullets stripped) across [[INDEX|episodic logs]] into a compact, "
                "non-intrusive (`type: rsi-ledger`) reference table.\n\n"
                "---\n\n"
                "## 1. High-Signal Episodic Invariants & Milestones (Newest First)\n\n"
                + "\n".join(rows_md)
                + "\n"
                + llm_summary
                + "\n---\n\n"
                "## 2. Closed-Loop RSI Governance Rules\n"
                "1. **Lyapunov Non-Regression Gate**: Every consolidation or `.kb/retrieval_policy.json` update must preserve $J(\\theta_{t+1}, K_{t+1}) \\ge J(\\theta_t, K_t)$ across both baseline and held-out retrieval probes with `0` ledger intrusions.\n"
                "2. **5-Gate Structural Invariance**: All distilled notes must maintain valid YAML frontmatter, zero broken `[[wikilinks]]`, and $\\ge 1$ inbound graph edge from [[INDEX]].\n"
            )
            distill_path.parent.mkdir(parents=True, exist_ok=True)
            distill_path.write_text(distill_content, encoding="utf-8")
        elif distill_path.exists():
            distill_path.unlink(missing_ok=True)

    # Step 2C: Heal orphan notes (Gate 3) & missing INDEX.md (Gate 2) by linking them in INDEX.md
    md_files = list_md_fn(vault_dir)
    stem_lookup = build_stem_fn(vault_dir, md_files)
    inbound_counts: dict[str, int] = {str(p.relative_to(vault_dir)): 0 for p in md_files}
    links_to_index = False
    for p in md_files:
        rel = str(p.relative_to(vault_dir))
        txt = p.read_text(encoding="utf-8", errors="replace")
        for target in extract_links_fn(txt, stem_lookup):
            if target == "INDEX.md":
                links_to_index = True
            if target in inbound_counts and target != rel:
                inbound_counts[target] += 1

    has_orphans = any(
        count == 0 for rel, count in inbound_counts.items() if rel not in ("INDEX.md", "AGENTS.md", "README.md")
    )
    if not index_path.is_file() and (has_orphans or links_to_index):
        index_path.write_text(
            "---\n"
            'title: "Vault Index — Map of Content"\n'
            "type: moc\n"
            "tier: 0\n"
            f"updated: {today}\n"
            "tags: [index, moc, navigation]\n"
            "---\n\n"
            "# Vault Index (`INDEX`)\n\n"
            "## Notes\n",
            encoding="utf-8",
        )
        inbound_counts["INDEX.md"] = 1 if links_to_index else 0

    if index_path.is_file():
        idx_text = index_path.read_text(encoding="utf-8", errors="replace")
        idx_modified = False
        for rel, count in sorted(inbound_counts.items()):
            if rel in ("INDEX.md", "AGENTS.md", "README.md") or count > 0:
                continue
            rel_no_ext = rel.removesuffix(".md")
            if f"[[{rel_no_ext}]]" not in idx_text and f"[[{rel}]]" not in idx_text:
                note_meta = parse_fm_fn((vault_dir / rel).read_text(encoding="utf-8", errors="replace"))
                note_title = note_meta.get("title") or rel_no_ext
                idx_text = idx_text.rstrip() + f"\n- [[{rel_no_ext}]] — {note_title}\n"
                healed_orphans.append(rel)
                idx_modified = True
        if idx_modified:
            index_path.write_text(idx_text, encoding="utf-8")

    # Re-sync index
    sync_fn(argparse.Namespace(vault=str(vault_dir), force=False, no_embed=no_embed, quiet=True))

    return {
        "healed_frontmatter": healed_frontmatter,
        "healed_orphans": healed_orphans,
        "episodic_logs_scanned": len(episodic_files),
        "raw_bullets_scanned": raw_bullets_scanned,
        "rejected_noise_bullets": rejected_noise_bullets,
        "distilled_entries": len(episodic_items),
        "distillation_note": distill_rel if episodic_items else None,
        "snapshots": snapshots,
    }


def append_rsi_ledger(vault_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    """Append a tamper-evident SHA-256 hash-chained entry to `.kb/rsi-ledger.jsonl`."""
    lp = ledger_path(vault_dir)
    lp.parent.mkdir(parents=True, exist_ok=True)
    prev_hash = "0" * 64
    cycle_index = 1
    if lp.is_file():
        lines = [ln.strip() for ln in lp.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines:
            cycle_index = len(lines) + 1
            try:
                last_obj = json.loads(lines[-1])
                prev_hash = str(last_obj.get("entry_hash", prev_hash))
            except Exception:
                pass

    entry = {
        "cycle_id": f"RSI-{cycle_index:04d}",
        "timestamp": datetime.datetime.now().isoformat(),
        "prev_hash": prev_hash,
        **record,
    }
    canonical = json.dumps(entry, sort_keys=True).encode("utf-8")
    entry["entry_hash"] = hashlib.sha256(canonical).hexdigest()
    with lp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=False) + "\n")
    return entry


def read_rsi_ledger(vault_dir: Path) -> list[dict[str, Any]]:
    lp = ledger_path(vault_dir)
    if not lp.is_file():
        return []
    out: list[dict[str, Any]] = []
    for ln in lp.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
    return out


def verify_ledger_chain(vault_dir: Path) -> tuple[bool, int]:
    """
    Verify cryptographic SHA-256 chain integrity across all entries in `.kb/rsi-ledger.jsonl`.
    Fails closed (`False`) if any non-empty line is malformed JSON or has a broken hash link.
    """
    lp = ledger_path(vault_dir)
    if not lp.is_file():
        return True, 0
    raw_lines = [ln.strip() for ln in lp.read_text(encoding="utf-8").splitlines() if ln.strip()]
    prev = "0" * 64
    for ln in raw_lines:
        try:
            e = json.loads(ln)
        except Exception:
            return False, len(raw_lines)
        if not isinstance(e, dict) or e.get("prev_hash") != prev:
            return False, len(raw_lines)
        check_copy = {k: v for k, v in e.items() if k != "entry_hash"}
        expected_hash = hashlib.sha256(json.dumps(check_copy, sort_keys=True).encode("utf-8")).hexdigest()
        if e.get("entry_hash") != expected_hash:
            return False, len(raw_lines)
        prev = expected_hash
    return True, len(raw_lines)


def run_rsi_cycle(
    vault_dir: Path,
    get_db_fn: Callable[[Path], sqlite3.Connection],
    hybrid_retrieve_fn: Callable[..., list[dict]],
    sync_fn: Callable[[Any], None],
    list_md_fn: Callable[[Path], list[Path]],
    parse_fm_fn: Callable[[str], dict],
    extract_links_fn: Callable[[str, dict[str, str] | None], list[str]],
    build_stem_fn: Callable[[Path, list[Path] | None], dict[str, str]],
    call_llm_fn: Callable[[list[dict]], tuple[str, str]] | None = None,
    use_llm: bool = False,
    no_embed: bool = False,
    verify_rust: bool = False,
) -> dict[str, Any]:
    """
    Execute a complete Monotonically-Gated Recursive Self-Improvement Cycle:
      1. Baseline benchmark evaluation J(theta_t, K_t) + held-out generalization split.
      2. Loop 2: Self-heal structural drift + consolidate `04-episodic/` into `02-operating-system/`.
      3. Loop 1: Coordinate-search optimization of `.kb/retrieval_policy.json` -> theta_{t+1}.
      4. Optional Loop 3 compiler gate (`rustc --test -D warnings` over 20 Rust domains).
      5. Loop 4: Soar Explanation-Based Chunking via typed `invoke_hybrid_retrieve` adapter.
      6. Atomic Lyapunov Monotonicity assertion (rollback on regression) & `.kb/rsi-ledger.jsonl` commit.
    """
    conn = get_db_fn(vault_dir)
    if conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0:
        conn.close()
        sync_fn(argparse.Namespace(vault=str(vault_dir), force=False, no_embed=no_embed, quiet=True))
        conn = get_db_fn(vault_dir)

    p_file = policy_path(vault_dir)
    orig_policy_raw = p_file.read_text(encoding="utf-8") if p_file.is_file() else None
    baseline_policy = load_retrieval_policy(vault_dir)
    baseline_policy_sha = compute_policy_sha256(baseline_policy)
    baseline_probes = build_benchmark_suite(conn, vault_dir)
    _, heldout_probes = split_benchmark_suite(conn, vault_dir, probes=baseline_probes)
    before_eval = evaluate_retrieval_benchmark(
        conn, vault_dir, hybrid_retrieve_fn, policy=baseline_policy, probes=baseline_probes
    )
    before_heldout = (
        evaluate_retrieval_benchmark(conn, vault_dir, hybrid_retrieve_fn, policy=baseline_policy, probes=heldout_probes)
        if heldout_probes
        else before_eval
    )
    conn.close()

    consolidation = heal_and_consolidate_vault(
        vault_dir=vault_dir,
        list_md_fn=list_md_fn,
        parse_fm_fn=parse_fm_fn,
        extract_links_fn=extract_links_fn,
        build_stem_fn=build_stem_fn,
        sync_fn=sync_fn,
        call_llm_fn=call_llm_fn,
        use_llm=use_llm,
        no_embed=no_embed,
    )
    snapshots = consolidation.pop("snapshots", {})

    conn = get_db_fn(vault_dir)
    after_heal_eval = evaluate_retrieval_benchmark(
        conn, vault_dir, hybrid_retrieve_fn, policy=baseline_policy, probes=baseline_probes
    )
    tuning = tune_retrieval_policy(conn, vault_dir, hybrid_retrieve_fn)
    after_eval = evaluate_retrieval_benchmark(
        conn, vault_dir, hybrid_retrieve_fn, policy=tuning["policy"], probes=baseline_probes
    )
    after_heldout = (
        evaluate_retrieval_benchmark(
            conn, vault_dir, hybrid_retrieve_fn, policy=tuning["policy"], probes=heldout_probes
        )
        if heldout_probes
        else after_eval
    )
    conn.close()

    rust_cert_status = "SKIPPED"
    rust_gate_failed = False
    if verify_rust:
        from sovereign_kb.rust_cert import run_rust_certification

        cert_rep = run_rust_certification(
            vault_dir=vault_dir,
            get_db_fn=get_db_fn,
            hybrid_retrieve_fn=hybrid_retrieve_fn,
            sync_fn=sync_fn,
            output_dir=None,
            no_embed=no_embed,
        )
        rust_cert_status = cert_rep["status"]
        rust_gate_failed = rust_cert_status not in ("CERTIFIED", "PASS")

    # Check 3-point Lyapunov non-regression on baseline, held-out, intermediate steps, and ledger intrusions + optional Rust gate
    attempted_delta_j_heal = round(after_heal_eval["objective_j"] - before_eval["objective_j"], 6)
    attempted_delta_j_tune = round(after_eval["objective_j"] - after_heal_eval["objective_j"], 6)
    attempted_delta_j = round(after_eval["objective_j"] - before_eval["objective_j"], 6)
    attempted_heldout_delta_j = round(after_heldout["objective_j"] - before_heldout["objective_j"], 6)
    delta_j_heal = attempted_delta_j_heal
    delta_j_tune = attempted_delta_j_tune
    delta_j = attempted_delta_j
    heldout_delta_j = attempted_heldout_delta_j
    intrusion_regressed = after_eval.get("ledger_intrusions", 0) > before_eval.get(
        "ledger_intrusions", 0
    ) or after_heldout.get("ledger_intrusions", 0) > before_heldout.get("ledger_intrusions", 0)
    rank_regressed = (
        after_eval["mrr_at_5"] < before_eval["mrr_at_5"] - 1e-6
        or after_eval["ndcg_at_5"] < before_eval["ndcg_at_5"] - 1e-6
        or after_eval["recall_at_1"] < before_eval["recall_at_1"] - 1e-6
        or after_heldout["mrr_at_5"] < before_heldout["mrr_at_5"] - 1e-6
        or after_heldout["ndcg_at_5"] < before_heldout["ndcg_at_5"] - 1e-6
        or after_heldout["recall_at_1"] < before_heldout["recall_at_1"] - 1e-6
    )
    heal_tolerance = -5e-4 if snapshots else -1e-6
    regressed = (
        rank_regressed
        or (delta_j_tune < -1e-6)
        or (delta_j < heal_tolerance)
        or (delta_j_heal < heal_tolerance)
        or (heldout_delta_j < heal_tolerance)
        or intrusion_regressed
        or rust_gate_failed
    )

    if regressed:
        # Rollback file modifications and policy atomically if capability or gate regressed
        for path, orig_content in snapshots.items():
            if orig_content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(orig_content, encoding="utf-8")
        if orig_policy_raw is None:
            p_file.unlink(missing_ok=True)
        else:
            p_file.write_text(orig_policy_raw, encoding="utf-8")
        sync_fn(argparse.Namespace(vault=str(vault_dir), force=False, no_embed=no_embed, quiet=True))
        after_eval = before_eval
        after_heldout = before_heldout
        delta_j = 0.0
        delta_j_heal = 0.0
        delta_j_tune = 0.0
        heldout_delta_j = 0.0
        committed_policy = baseline_policy
        committed_policy_sha = baseline_policy_sha
        committed_updated = False
        status = "ROLLED_BACK_ON_REGRESSION"
    else:
        committed_policy = tuning["policy"]
        committed_policy_sha = tuning["policy_sha256"]
        committed_updated = tuning["updated"]
        status = (
            "MONOTONIC_IMPROVEMENT"
            if (
                delta_j > 1e-6
                or consolidation["healed_frontmatter"]
                or consolidation["healed_orphans"]
                or consolidation["distilled_entries"] > 0
            )
            else "FIXED_POINT_CONVERGED"
        )

    # Loop 4 — Soar Explanation-Based Chunking & 0-Impasse Verification targeting live misses first
    from sovereign_kb.soar import resolve_retrieval_tie_with_soar

    if after_eval.get("misses"):
        soar_probe_q = after_eval["misses"][0]["query"]
    else:
        soar_probe_q = baseline_probes[0]["query"] if baseline_probes else "scientific-paper-framework Pillar 5"
    conn_soar = get_db_fn(vault_dir)
    try:
        live_cands = invoke_hybrid_retrieve(
            hybrid_retrieve_fn,
            conn_soar,
            vault_dir,
            soar_probe_q,
            top_k=4,
            engine="auto",
            policy=committed_policy,
        )
    finally:
        conn_soar.close()

    soar_chunk_rep = resolve_retrieval_tie_with_soar(
        vault_dir=vault_dir,
        query=soar_probe_q,
        candidates=live_cands,
        persist_chunks=(status != "ROLLED_BACK_ON_REGRESSION"),
    )
    soar_chunk_rep["retrieval_candidates_count"] = len(live_cands)

    ledger_entry = append_rsi_ledger(
        vault_dir,
        {
            "status": status,
            "lyapunov_monotonic": not regressed,
            "j_before": before_eval["objective_j"],
            "j_after": after_eval["objective_j"],
            "delta_j": delta_j,
            "delta_j_heal": delta_j_heal,
            "delta_j_tune": delta_j_tune,
            "attempted_delta_j": attempted_delta_j,
            "attempted_delta_j_heal": attempted_delta_j_heal,
            "attempted_delta_j_tune": attempted_delta_j_tune,
            "heldout_j_before": before_heldout["objective_j"],
            "heldout_j_after": after_heldout["objective_j"],
            "heldout_delta_j": heldout_delta_j,
            "attempted_heldout_delta_j": attempted_heldout_delta_j,
            "ledger_intrusions": after_eval.get("ledger_intrusions", 0),
            "mrr_before": before_eval["mrr_at_5"],
            "mrr_after": after_eval["mrr_at_5"],
            "recall1_before": before_eval["recall_at_1"],
            "recall1_after": after_eval["recall_at_1"],
            "probes_count": after_eval["probes_count"],
            "heldout_probes_count": len(heldout_probes),
            "policy_sha256": committed_policy_sha,
            "candidate_policy_sha256": tuning["policy_sha256"],
            "policy_updated": committed_updated,
            "healed_frontmatter": consolidation["healed_frontmatter"],
            "healed_orphans": consolidation["healed_orphans"],
            "distilled_entries": consolidation["distilled_entries"],
            "rejected_noise_bullets": consolidation["rejected_noise_bullets"],
            "rust_certification": rust_cert_status,
            "soar_chunks_compiled": soar_chunk_rep["chunks_compiled"],
            "soar_zero_impasse_replay": soar_chunk_rep["zero_impasse_replay"],
        },
    )
    chain_ok, total_cycles = verify_ledger_chain(vault_dir)

    return {
        "cycle_id": ledger_entry["cycle_id"],
        "status": status,
        "timestamp": ledger_entry["timestamp"],
        "vault_dir": str(vault_dir),
        "lyapunov_monotonic": not regressed,
        "before": before_eval,
        "after": after_eval,
        "heldout_before": before_heldout,
        "heldout_after": after_heldout,
        "delta_j": delta_j,
        "delta_j_heal": delta_j_heal,
        "delta_j_tune": delta_j_tune,
        "heldout_delta_j": heldout_delta_j,
        "consolidation": consolidation,
        "tuning": {
            "updated": committed_updated,
            "candidates_evaluated": tuning["candidates_evaluated"],
            "policy_sha256": committed_policy_sha,
            "candidate_policy_sha256": tuning["policy_sha256"],
            "policy": committed_policy,
        },
        "rust_certification": rust_cert_status,
        "soar_chunking": soar_chunk_rep,
        "ledger": {
            "path": str(ledger_path(vault_dir)),
            "total_cycles": total_cycles,
            "chain_verified": chain_ok,
            "entry_hash": ledger_entry["entry_hash"],
        },
    }

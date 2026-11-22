"""Regression tests for the 5 Lyapunov RSI upgrades in sovereign_kb.rsi & cli."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from sovereign_kb import cli, rsi
from sovereign_kb.retrieval import RetrievalPolicy


def test_retrieval_policy_includes_episodic_dampening() -> None:
    p = RetrievalPolicy.from_dict({"episodic_dampening": 0.42, "k_rrf": 9.0})
    assert p.episodic_dampening == pytest.approx(0.42)
    assert p.to_dict()["episodic_dampening"] == pytest.approx(0.42)


def test_bug_a_heldout_dip_and_bug_b_fixed_point_labeling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sovereign_kb.soar as soar_pkg

    vault = tmp_path / "vault"
    (vault / ".kb").mkdir(parents=True)

    def get_db(_v: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE documents (id INTEGER)")
        conn.execute("INSERT INTO documents VALUES (1)")
        return conn

    train_p = [{"id": "T1", "query": "q1", "target_path": "a.md", "target_heading": ""}]
    held_p = [{"id": "H1", "query": "q2", "target_path": "a.md", "target_heading": "", "split": "heldout"}]
    monkeypatch.setattr(rsi, "build_benchmark_suite", lambda conn, v: train_p)
    monkeypatch.setattr(rsi, "split_benchmark_suite", lambda conn, v, probes=None: (train_p, held_p))
    monkeypatch.setattr(rsi, "invoke_hybrid_retrieve", lambda *a, **k: [])
    monkeypatch.setattr(
        soar_pkg,
        "resolve_retrieval_tie_with_soar",
        lambda **k: {"chunks_compiled": 0, "zero_impasse_replay": True},
    )
    base_policy = rsi.load_retrieval_policy(vault)
    monkeypatch.setattr(
        rsi,
        "tune_retrieval_policy",
        lambda conn, v, fn: {
            "policy": base_policy,
            "policy_sha256": rsi.compute_policy_sha256(base_policy),
            "updated": False,
            "candidates_evaluated": 1,
        },
    )

    def _ev(j: float) -> dict[str, Any]:
        return {
            "objective_j": j,
            "mrr_at_5": 1.0,
            "ndcg_at_5": 1.0,
            "recall_at_1": 1.0,
            "ledger_intrusions": 0,
            "probes_count": 1,
            "latency_p95_ms": 1.2,
            "misses": [],
        }

    # Bug B: distilled_entries == 40, no files written, delta == 0 -> FIXED_POINT_CONVERGED
    idle_snaps = rsi._SnapshotMap({vault / "INDEX.md": None})
    seq_idle = iter([0.92, 0.92, 0.92, 0.92, 0.92])
    monkeypatch.setattr(rsi, "evaluate_retrieval_benchmark", lambda *a, **k: _ev(next(seq_idle)))
    monkeypatch.setattr(
        rsi,
        "heal_and_consolidate_vault",
        lambda **k: {
            "healed_frontmatter": [],
            "healed_orphans": [],
            "distilled_entries": 40,
            "rejected_noise_bullets": 100,
            "snapshots": idle_snaps,
        },
    )
    rep_idle = rsi.run_rsi_cycle(
        vault_dir=vault,
        get_db_fn=get_db,
        hybrid_retrieve_fn=lambda *a, **k: [],
        sync_fn=lambda ns: None,
        list_md_fn=lambda v: [],
        parse_fm_fn=lambda t: {},
        extract_links_fn=lambda t, lk=None: [],
        build_stem_fn=lambda v, f=None: {},
    )
    assert rep_idle["status"] == "FIXED_POINT_CONVERGED"
    assert rep_idle["lyapunov_monotonic"] is True

    # Bug A: heldout dips by -6e-6 while files were written -> TOLERATED_CONSOLIDATION (never NON_REGRESSION)
    note = vault / "x.md"
    note.write_text("new\n", encoding="utf-8")
    busy_snaps = rsi._SnapshotMap({note: "old\n"})
    busy_snaps.written[note] = "new\n"
    # order: before_eval, before_heldout, after_heal_eval, after_eval, after_heldout
    seq_dip = iter([0.92, 0.920006, 0.92, 0.92, 0.92])
    monkeypatch.setattr(rsi, "evaluate_retrieval_benchmark", lambda *a, **k: _ev(next(seq_dip)))
    monkeypatch.setattr(
        rsi,
        "heal_and_consolidate_vault",
        lambda **k: {
            "healed_frontmatter": [],
            "healed_orphans": [],
            "distilled_entries": 40,
            "rejected_noise_bullets": 100,
            "snapshots": busy_snaps,
        },
    )
    rep_dip = rsi.run_rsi_cycle(
        vault_dir=vault,
        get_db_fn=get_db,
        hybrid_retrieve_fn=lambda *a, **k: [],
        sync_fn=lambda ns: None,
        list_md_fn=lambda v: [],
        parse_fm_fn=lambda t: {},
        extract_links_fn=lambda t, lk=None: [],
        build_stem_fn=lambda v, f=None: {},
    )
    assert rep_dip["status"] == "TOLERATED_CONSOLIDATION"
    assert rep_dip["lyapunov_monotonic"] is False


def test_adaptive_coordinate_search_explores_below_old_floor_and_new_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    (vault / ".kb").mkdir(parents=True)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE documents (rel_path TEXT, doc_type TEXT, title TEXT, tags TEXT)")
    conn.execute("INSERT INTO documents VALUES ('02-operating-system/a.md', 'operating-system', 'A', 'tag')")

    train_p = [{"id": "P1", "query": "q", "target_path": "02-operating-system/a.md", "target_heading": ""}]
    held_p = [
        {"id": "H1", "query": "hq", "target_path": "02-operating-system/a.md", "target_heading": "", "split": "heldout"}
    ]
    monkeypatch.setattr(rsi, "build_benchmark_suite", lambda c, v: train_p)
    monkeypatch.setattr(rsi, "split_benchmark_suite", lambda c, v, probes=None: (train_p, held_p))

    seen_k_rrf: list[float] = []
    seen_w_vec: list[float] = []
    seen_lead: list[float] = []

    def fake_eval(c, v, fn, policy=None, probes=None, **kw):
        assert policy is not None
        k_val = float(policy["k_rrf"])
        wv = float(policy["w_vec"])
        lf = float(policy["graph_neighbor_lead_factor"])
        seen_k_rrf.append(k_val)
        seen_w_vec.append(wv)
        seen_lead.append(lf)
        # Reward k_rrf < 12.0 and w_vec > 1.20
        score = 0.85 + max(0.0, (12.0 - k_val) * 0.002) + max(0.0, (wv - 1.2) * 0.01)
        return {
            "objective_j": round(min(0.99, score), 6),
            "mrr_at_5": 1.0,
            "ndcg_at_5": 1.0,
            "recall_at_1": 1.0,
            "ledger_intrusions": 0,
            "probes_count": 1,
            "latency_p95_ms": 0.5,
            "misses": [],
        }

    monkeypatch.setattr(rsi, "evaluate_retrieval_benchmark", fake_eval)
    rsi.save_retrieval_policy(vault, {**rsi.DEFAULT_RETRIEVAL_POLICY, "k_rrf": 12.0})
    res = rsi.tune_retrieval_policy(conn, vault, lambda *a, **k: [])
    conn.close()

    assert any(k < 12.0 for k in seen_k_rrf)
    assert len(set(seen_w_vec)) > 1
    assert len(set(seen_lead)) > 1
    assert res["updated"] is True
    assert res["policy"]["k_rrf"] < 12.0
    assert res["policy"]["w_vec"] > 1.20


def test_vault_health_queues_lyapunov_potential_and_negative_controls(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "02-operating-system").mkdir(parents=True)
    distill = vault / "02-operating-system" / "autonomous-rsi-distillations.md"
    distill.write_text("\n".join(f"line {i}" for i in range(180)) + "\n", encoding="utf-8")

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE documents (rel_path TEXT, doc_type TEXT, updated TEXT)")
    conn.execute("CREATE TABLE links (target_path TEXT)")
    conn.execute("INSERT INTO documents VALUES ('02-operating-system/old.md', 'operating-system', '2025-01-01')")
    conn.execute("INSERT INTO documents VALUES ('02-operating-system/fresh.md', 'operating-system', '2026-11-20')")
    conn.execute("INSERT INTO links VALUES ('02-operating-system/fresh.md')")

    queues = rsi.compute_vault_health_queues(conn, vault, latency_p95_ms=50.0)
    assert queues["q_latency"] == pytest.approx(0.2)
    assert queues["q_staleness"] == pytest.approx(0.5)
    assert 0.0 < queues["q_bloat"] <= 1.0

    pot = rsi.compute_lyapunov_potential(0.90, queues)
    assert pot["lyapunov_v"] > pot["lyapunov_v_det"] > 0.10

    # Negative probes: one returns high score (false positive), two return empty
    probes = [{"id": "P1", "query": "fresh", "target_path": "02-operating-system/fresh.md", "target_heading": ""}]

    def fake_retrieve(_c, vault_dir, q, **_kw):
        if "xylophone" in q:
            return [
                {"rel_path": "02-operating-system/fresh.md", "heading_path": "H", "content": "c", "rrf_score": 0.08}
            ]
        if "fresh" in q:
            return [
                {"rel_path": "02-operating-system/fresh.md", "heading_path": "H", "content": "c", "rrf_score": 0.09}
            ]
        return []

    ev = rsi.evaluate_retrieval_benchmark(
        conn, vault, fake_retrieve, probes=probes, negative_probes=rsi.CANONICAL_NEGATIVE_PROBES
    )
    conn.close()
    assert ev["negative_fp_count"] == 1
    assert ev["negative_rejection_rate"] == pytest.approx(2 / 3, rel=1e-4)
    assert ev["latency_p95_ms"] >= 0.0


def test_query_feedback_cli_and_heldout_integration(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = tmp_path / "vault"
    (vault / ".kb").mkdir(parents=True)
    cli.main(
        [
            "--vault",
            str(vault),
            "rsi",
            "feedback",
            "--query",
            "lyapunov drift plus penalty",
            "--target",
            "02-operating-system/heuristics-and-mental-models.md",
            "--json",
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert out["recorded"] is True
    assert out["total_probes"] == 1

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE documents (rel_path TEXT, doc_type TEXT)")
    conn.execute(
        "INSERT INTO documents VALUES ('02-operating-system/heuristics-and-mental-models.md', 'operating-system')"
    )
    _train, heldout = rsi.split_benchmark_suite(conn, vault, probes=[])
    conn.close()
    assert any(p["query"] == "lyapunov drift plus penalty" for p in heldout)

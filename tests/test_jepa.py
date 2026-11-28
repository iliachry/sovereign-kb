"""F-23: Comprehensive unit & integration tests for Unified Vault JEPA (`sovereign_kb.jepa` / `skb jepa`)."""

from __future__ import annotations

import array
import ast
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from sovereign_kb.cli.parser_main import main as skb_main
from sovereign_kb.evr.reader import EvrReader
from sovereign_kb.jepa import (
    ACTIONS,
    JEPAConfig,
    VaultJEPA,
    benchmark_jepa,
    detect_surprise,
    discover_gaps,
    extract_transitions,
    heal_vault_links_with_jepa,
    jepa_hybrid_search,
    jepa_status,
    load_vault_corpus,
    plan_latent_trajectory,
    predict_latent,
    resolve_impasse_with_jepa,
    retrieve_chunks_with_hjepa,
    run_code_graph_jepa,
    train_hierarchical_jepa,
    train_vault_jepa,
)
from sovereign_kb.jepa.features import dot


def _create_synthetic_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "jepa_vault"
    vault.mkdir()
    skb_main(["--vault", str(vault), "init", str(vault), "--no-embed", "--minimal"])

    # Add rich interconnected domain, operating-system, and episodic notes
    notes = {
        "02-operating-system/retrieval-architecture.md": (
            "---\ntitle: Retrieval Architecture\ntype: operating-system\nupdated: 2026-11-01\n"
            "tags: [retrieval, bm25, wand, vectors]\n---\n"
            "# Retrieval Architecture\n\n## Lexical Stage\nSQLite FTS5 BM25 and Block-Max WAND.\n\n"
            "## Semantic Stage\nDense embeddings and [[02-operating-system/graph-propagation]].\n"
        ),
        "02-operating-system/graph-propagation.md": (
            "---\ntitle: Graph Propagation\ntype: operating-system\nupdated: 2026-11-02\n"
            "tags: [graph, wikilinks, rrf]\n---\n"
            "# Graph Propagation\n\n## One-Hop Boost\nPropagates scores across [[02-operating-system/retrieval-architecture]] "
            "and [[03-life-domains/systems-benchmarks]].\n"
        ),
        "03-life-domains/systems-benchmarks.md": (
            "---\ntitle: Systems Benchmarks\ntype: domain\nupdated: 2026-11-03\n"
            "tags: [benchmarks, latency, rust]\n---\n"
            "# Systems Benchmarks\n\n## Latency Budget\nSub-millisecond search latency via [[02-operating-system/retrieval-architecture]].\n\n"
            "## Memory Footprint\nZero-copy memory-mapped index structures.\n"
        ),
        "03-life-domains/world-models.md": (
            "---\ntitle: Latent World Models\ntype: domain\nupdated: 2026-11-04\n"
            "tags: [jepa, latent, predictive]\n---\n"
            "# Latent World Models\n\n## Energy-Based Prediction\nPredicts latent target representations without token generation.\n\n"
            "## Anti-Collapse\nUses EMA target encoder and [[02-operating-system/graph-propagation]].\n"
        ),
        "04-episodic/2026-11-01-log.md": (
            "---\ntitle: 2026-11-01 Log\ntype: episodic\nupdated: 2026-11-01\n"
            "tags: [episodic, log]\n---\n"
            "# 2026-11-01\n\n## Work\nTuned [[02-operating-system/retrieval-architecture]] BM25 weights.\n"
        ),
        "04-episodic/2026-11-02-log.md": (
            "---\ntitle: 2026-11-02 Log\ntype: episodic\nupdated: 2026-11-02\n"
            "tags: [episodic, log]\n---\n"
            "# 2026-11-02\n\n## Work\nMeasured [[03-life-domains/systems-benchmarks]] p99 latency.\n"
        ),
        "04-episodic/2026-11-03-log.md": (
            "---\ntitle: 2026-11-03 Log\ntype: episodic\nupdated: 2026-11-03\n"
            "tags: [episodic, log]\n---\n"
            "# 2026-11-03\n\n## Work\nPrototyped [[03-life-domains/world-models]] energy objective.\n"
        ),
    }
    for rel, body in notes.items():
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    # Wire them in INDEX.md so vault links stay clean
    index_p = vault / "INDEX.md"
    idx_txt = index_p.read_text(encoding="utf-8")
    extra_links = "\n".join(f"- [[{rel[:-3]}]]" for rel in sorted(notes))
    index_p.write_text(idx_txt + "\n## Test Notes\n" + extra_links + "\n", encoding="utf-8")

    skb_main(["--vault", str(vault), "sync", "--no-embed"])
    return vault


def test_jepa_transition_extraction_all_actions_and_hashed_mode(tmp_path: Path) -> None:
    vault = _create_synthetic_vault(tmp_path)
    corpus = load_vault_corpus(vault)
    assert corpus.embedding_mode == "hashed"
    assert corpus.input_dim == 128
    assert len(corpus.doc_paths) >= 8

    train_t, held_t = extract_transitions(corpus, seed=42)
    all_t = train_t + held_t
    seen_actions = {tr.action for tr in all_t}
    for action in ACTIONS:
        assert action in seen_actions, f"Missing expected action {action} in extracted transitions"


def test_jepa_train_anticollapse_rollout_gaps_surprise_and_evr(tmp_path: Path) -> None:
    vault = _create_synthetic_vault(tmp_path)
    evr_out = tmp_path / "jepa_ckpt.evr"

    cfg = JEPAConfig(input_dim=128, proj_dim=64, latent_dim=16, hidden_dim=32, epochs=20, lr=0.05, seed=42)
    res = train_vault_jepa(vault, config=cfg, evr_out=evr_out)

    cm = res["collapse_metrics"]
    assert cm["collapsed"] is False
    assert cm["effective_rank"] >= 0.25 * cfg.latent_dim
    assert cm["energy_margin"] > 0.0
    assert res["trained_heldout_energy"] < res["untrained_heldout_energy"]

    # Verify .evr capsule export
    assert evr_out.is_file()
    with EvrReader(evr_out) as reader:
        ver = reader.verify()
        assert ver["valid"] is True
        summ = reader.summary()
        paths = {c["path"] for c in summ.get("chunks", [])}
        assert "tensors/W_ctx.tensor" in paths
        assert "tensors/W_tgt.tensor" in paths
        assert "state/jepa_manifest.json" in paths
        w_ctx_t = reader.read_tensor("tensors/W_ctx.tensor")
        assert w_ctx_t["shape"] == [16, 64]

    # Multi-step world-model rollout
    roll = predict_latent(vault, "04-episodic/2026-11-01-log.md", action="EPISODIC_NEXT", steps=2, top_k=3)
    assert roll["steps"] == 2
    assert len(roll["rollout"]) == 2
    assert len(roll["rollout"][0]["matches"]) == 3
    assert all(m["rel_path"] != "04-episodic/2026-11-01-log.md" for m in roll["rollout"][0]["matches"])

    # Query-string prediction (offline fallback)
    q_roll = predict_latent(vault, "sub-millisecond search latency", action="WIKILINK", steps=1, top_k=3)
    assert q_roll["matched_doc"] is None
    assert len(q_roll["rollout"][0]["matches"]) == 3

    # Missing-link gap discovery (with <=2 per source/target diversity cap)
    gaps = discover_gaps(vault, top_k=5, action="WIKILINK")
    assert len(gaps["gaps"]) > 0
    tgt_hist: dict[str, int] = {}
    for g in gaps["gaps"]:
        assert g["source_path"] != g["target_path"]
        tgt_hist[g["target_path"]] = tgt_hist.get(g["target_path"], 0) + 1
    assert max(tgt_hist.values()) <= 2

    # Surprise / drift detection
    surp = detect_surprise(vault, top_k=5)
    assert surp["transitions_scored"] > 0
    assert len(surp["surprises"]) > 0

    # Benchmark
    bench = benchmark_jepa(vault, config=cfg)
    assert bench["ranking"]["count"] > 0
    assert bench["collapse_metrics"]["collapsed"] is False

    # Status
    st = jepa_status(vault)
    assert st["trained"] is True
    assert st["model_sha256"] == res["model_sha256"]

    # Pivoted-QR orthonormal basis & proj_b64 checkpoint round-trip
    loaded, raw_ckpt = VaultJEPA.load(vault / ".kb" / "jepa_model.json")
    assert "proj_b64" in raw_ckpt
    assert loaded._proj_fitted is True
    assert loaded._proj_mat is not None
    assert abs(dot(loaded._proj_mat[0], loaded._proj_mat[0]) - 1.0) < 1e-4
    assert abs(dot(loaded._proj_mat[0], loaded._proj_mat[1])) < 1e-4

    # Goal-conditioned latent beam planner
    plan = plan_latent_trajectory(
        vault,
        start="04-episodic/2026-11-01-log.md",
        goal="03-life-domains/world-models.md",
        max_steps=3,
        beam_width=4,
    )
    assert len(plan["best_plan"]) >= 1
    assert 0.0 <= plan["final_energy"] <= 2.0
    assert plan["best_plan"][0]["action"] in ACTIONS

    # 5th-channel hybrid search
    jsearch = jepa_hybrid_search(vault, "latent world model", top_k=4)
    assert jsearch["count"] > 0
    assert any(r.get("jepa_energy") is not None for r in jsearch["results"])


def test_jepa_dense_blob_mode(tmp_path: Path) -> None:
    vault = _create_synthetic_vault(tmp_path)
    db_path = vault / ".kb" / "index.db"
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT id, rel_path FROM chunks ORDER BY id").fetchall()
        for cid, rel_path in rows:
            # Deterministic 32-d float32 dense vectors keyed by path
            vec = [0.0] * 32
            h = sum(ord(ch) for ch in rel_path)
            vec[h % 32] = 1.0
            vec[(h + cid) % 32] += 0.5
            blob = array.array("f", vec).tobytes()
            conn.execute("UPDATE chunks SET embedding = ? WHERE id = ?", (blob, cid))
        conn.commit()
    finally:
        conn.close()

    corpus = load_vault_corpus(vault)
    assert corpus.embedding_mode == "dense"
    assert corpus.input_dim == 32
    res = train_vault_jepa(vault, config=JEPAConfig(input_dim=32, proj_dim=0, latent_dim=16, epochs=10, seed=7))
    assert res["embedding_mode"] == "dense"
    assert res["input_dim"] == 32
    assert res["collapse_metrics"]["collapsed"] is False


def test_jepa_cli_smoke_json(tmp_path: Path) -> None:
    vault = _create_synthetic_vault(tmp_path)
    for argv in (
        ["--vault", str(vault), "jepa", "train", "--epochs", "8", "--json"],
        ["--vault", str(vault), "jepa", "status", "--json"],
        ["--vault", str(vault), "jepa", "predict", "01-now/now.md", "--action", "WIKILINK", "--steps", "2", "--json"],
        [
            "--vault",
            str(vault),
            "jepa",
            "plan",
            "04-episodic/2026-11-01-log.md",
            "03-life-domains/world-models.md",
            "--steps",
            "2",
            "--json",
        ],
        ["--vault", str(vault), "jepa", "search", "retrieval latency", "-n", "3", "--json"],
        ["--vault", str(vault), "search", "retrieval latency", "--jepa", "-n", "3", "--json"],
        ["--vault", str(vault), "jepa", "gaps", "-n", "3", "--json"],
        ["--vault", str(vault), "jepa", "surprise", "-n", "3", "--json"],
        ["--vault", str(vault), "jepa", "bench", "--epochs", "8", "--json"],
        ["--vault", str(vault), "jepa", "hjepa", "--epochs", "6", "--json"],
        ["--vault", str(vault), "jepa", "soar", "retrieval latency", "--json"],
        ["--vault", str(vault), "jepa", "heal", "-n", "2", "--json"],
    ):
        proc = subprocess.run(
            [sys.executable, "-m", "sovereign_kb.cli", *argv],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"argv={argv}\nstdout={proc.stdout}\nstderr={proc.stderr}"
        parsed = json.loads(proc.stdout)
        assert isinstance(parsed, dict)


def test_jepa_barbell_isolation() -> None:
    src_root = Path(__file__).resolve().parents[1] / "src" / "sovereign_kb"
    core_files = [
        src_root / "gwi_core.py",
        src_root / "gwi_lenses.py",
        src_root / "gwi_science.py",
        src_root / "gwi_antifragility.py",
        src_root / "gwi_rigor.py",
        src_root / "retrieval.py",
        src_root / "rsi.py",
    ]
    for path in core_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "jepa" not in alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert "jepa" not in node.module


@given(
    vec=st.lists(
        st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False), min_size=16, max_size=16
    ),
    action=st.sampled_from(ACTIONS),
)
@settings(max_examples=20)
def test_jepa_latent_unit_norm_and_energy_bounds_property(vec: list[float], action: str) -> None:
    if sum(v * v for v in vec) <= 1e-8:
        vec = [1.0] + [0.0] * 15
    model = VaultJEPA(JEPAConfig(input_dim=16, proj_dim=0, latent_dim=8, hidden_dim=16, seed=11))
    sx = model.encode_context(vec)
    sy = model.encode_target(vec)
    shat = model.predict(sx, action)
    assert abs(dot(sx, sx) - 1.0) < 1e-5
    assert abs(dot(shat, shat) - 1.0) < 1e-5
    e = model.energy(shat, sy)
    assert -1e-6 <= e <= 2.0 + 1e-6


def test_jepa_hierarchical_soar_heal_and_code(tmp_path: Path) -> None:
    vault = _create_synthetic_vault(tmp_path)
    cfg = JEPAConfig(input_dim=128, proj_dim=64, latent_dim=16, hidden_dim=32, epochs=12, seed=42)
    train_vault_jepa(vault, config=cfg)

    # 1. Hierarchical Chunk H-JEPA
    hres = train_hierarchical_jepa(vault, config=cfg)
    assert hres["chunks_indexed"] >= 10
    assert (vault / ".kb" / "hjepa_model.json").is_file()
    chits = retrieve_chunks_with_hjepa(vault, "sub-millisecond search latency", top_k=3)
    assert len(chits) == 3
    assert all(c["start_line"] >= 1 and c["end_line"] >= c["start_line"] for c in chits)

    # 2. Neuro-Symbolic Soar <-> JEPA impasse resolution
    sres = resolve_impasse_with_jepa(vault, "latent world model collapse")
    assert sres["resolved"] is True
    assert sres["first_run_impasses"] >= 1
    assert sres["replay_impasses"] == 0
    assert sres["zero_impasse_replay"] is True
    assert len(sres["compiled_chunks"]) >= 1
    # The tie is decided in S2 by the shared deliberation loop using JEPA energy (no hand-written S2 rule)
    delib = sres["deliberations"][0]
    assert delib["s2_rule"].startswith("neuro*s2*skb-jepa-tie*prefer-select-doc")
    assert all(n.startswith(f"chunk*{delib['s2_rule']}*") for n in sres["compiled_chunks"])
    if delib["engine"] == "jepa":
        top2 = sorted(sres["scored_candidates"][:2], key=lambda c: c["jepa_energy"])
        assert sres["winner_doc"] == top2[0]["rel_path"]

    # 3. Closed-loop Lyapunov-gated graph heal (dry-run + apply)
    dry = heal_vault_links_with_jepa(vault, max_edges=2, apply=False)
    assert dry["dry_run"] is True
    assert len(dry["proposed_edges"]) >= 1

    applied = heal_vault_links_with_jepa(vault, max_edges=2, apply=True)
    assert "j_before" in applied and "j_after" in applied
    if applied["applied"]:
        assert applied["gates_ok"] is True
        assert applied["delta_j"] >= -1e-6

    # 4. Polyglot Code-Graph JEPA on a mini repo
    mini_repo = tmp_path / "mini_repo"
    mini_repo.mkdir()
    (mini_repo / "core.py").write_text(
        "def load_data(x: int) -> int:\n    return x + 1\n\n"
        "def transform_data(x: int) -> int:\n    return load_data(x) * 2\n\n"
        "def run_pipeline(x: int) -> int:\n    return transform_data(x) + 3\n",
        encoding="utf-8",
    )
    (mini_repo / "test_core.py").write_text(
        "from core import run_pipeline, transform_data\n\n"
        "def test_pipeline() -> None:\n    assert run_pipeline(1) == 7\n    assert transform_data(2) == 6\n",
        encoding="utf-8",
    )
    cres = run_code_graph_jepa(mini_repo, query_symbol="load_data", top_k=3, epochs=10, seed=42)
    assert cres["total_symbols"] >= 4
    assert cres["collapse_metrics"]["collapsed"] is False
    assert len(cres["predicted_impacts"]) >= 1

    clean_home = tmp_path / "clean_home"
    clean_home.mkdir()
    clean_env = {k: v for k, v in os.environ.items() if k != "KB_VAULT_DIR"}
    clean_env["HOME"] = str(clean_home)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "sovereign_kb.cli",
            "jepa",
            "code",
            str(mini_repo),
            "load_data",
            "--epochs",
            "6",
            "--json",
        ],
        capture_output=True,
        text=True,
        cwd=str(mini_repo),
        env=clean_env,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["total_symbols"] >= 4

    # 5. Frontmatter related:[] and inline related:[...] guard in heal._inject_related_wikilink
    from sovereign_kb.jepa.heal import _inject_related_wikilink

    empty_rel = "---\ntitle: A\ntype: note\nupdated: 2026-11-27\ntags: [a]\nrelated: []\n---\nBody\n"
    injected = _inject_related_wikilink(empty_rel, "02-operating-system/b")
    assert injected is not None
    assert injected.count("related:") == 1
    assert "[[02-operating-system/b]]" in injected

    inline_rel = '---\ntitle: A\ntype: note\nupdated: 2026-11-27\ntags: [a]\nrelated: ["[[foo]]"]\n---\nBody\n'
    assert _inject_related_wikilink(inline_rel, "02-operating-system/b") is None

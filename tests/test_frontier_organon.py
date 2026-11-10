"""Unit & integration tests for `sovereign_kb.frontier_organon` (F-19: Closing Humanity-to-Organon Gaps 1-6)."""

from __future__ import annotations

import json
import struct
from pathlib import Path

from sovereign_kb.frontier_organon import (
    CodeGraphIndexer,
    audit_and_package_3dgs,
    check_formal_proofs,
    check_property_and_mutation_rigor,
    compile_organon_prompts,
    export_sleep_wake_lora_dataset,
    parse_splat_asset_header,
    run_ast_mutation_probe,
    slice_symbol_subgraph,
)


def test_polyglot_code_graph_indexer_and_2hop_subgraph_slice(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)

    (src_dir / "math_kernel.py").write_text(
        '''"""Core mathematical barrier kernel."""
from typing import overload

@overload
def compute_barrier(score: int, theta: int = 7) -> float: ...

@overload
def compute_barrier(score: float, theta: float = 7.0) -> float: ...

def compute_barrier(score: float, theta: float = 7.0) -> float:
    """Compute sigmoid barrier value."""
    return max(0.0, score - theta)

def local_helper() -> float:
    return 1.0

class QualityGate:
    """Evaluates Goldwood quality threshold."""
    def evaluate(self, s: float) -> bool:
        val = compute_barrier(s) + local_helper()
        return val >= 2.0
''',
        encoding="utf-8",
    )
    (src_dir / "pipeline_runner.py").write_text(
        '''"""Pipeline runner calling QualityGate."""
from src.math_kernel import QualityGate, compute_barrier

def local_helper() -> float:
    return 99.0

def run_stage4(score: float) -> bool:
    gate = QualityGate()
    raw = compute_barrier(score)
    return gate.evaluate(score) and raw > 0.0
''',
        encoding="utf-8",
    )
    (src_dir / "engine.rs").write_text(
        """use std::collections::HashMap;

pub struct BlockMaxIndex {
    pub docs: usize,
}

impl BlockMaxIndex {
    pub fn new() -> Self {
        Self { docs: 0 }
    }
}

pub struct PostingList {
    pub len: usize,
}

impl PostingList {
    pub fn new() -> Self {
        Self { len: 0 }
    }
}

pub fn query_wand(q: &str) -> usize {
    q.len()
}
""",
        encoding="utf-8",
    )
    (src_dir / "viewer.ts").write_text(
        """import { renderFrame } from "./gl";

export function initSplatViewer(canvasId: string): boolean {
    return canvasId.length > 0;
}
""",
        encoding="utf-8",
    )

    indexer = CodeGraphIndexer(tmp_path)
    graph = indexer.build_graph(persist=True, export_hips=True)

    assert graph["files_scanned"] == 4
    assert graph["total_symbols"] >= 9
    assert graph["resolved_call_edges"] >= 2
    assert graph["languages"]["python"] >= 5
    assert graph["languages"]["rust"] >= 4
    assert graph["languages"]["typescript"] == 1
    assert Path(graph["db_path"]).is_file()
    assert Path(graph["hips_corpus_path"]).is_file()

    # Verify local call resolution: math_kernel.py#QualityGate.evaluate calling local_helper resolves to math_kernel.py
    local_edges = [e for e in graph["edges"] if e.target_name == "local_helper" and "math_kernel.py" in e.source_id]
    assert len(local_edges) == 1
    assert "math_kernel.py#local_helper()" in (local_edges[0].target_id or "")

    # Verify hops=0 returns only seed symbols without expanding 1-hop neighbors
    sub0 = slice_symbol_subgraph(tmp_path, "compute_barrier", hops=0, top_k=2)
    assert list(sub0["hop_layers"].keys()) == [0]

    # Slice 2-hop subgraph around compute_barrier -> should pull in pipeline_runner.py as caller
    sub = slice_symbol_subgraph(tmp_path, "compute_barrier QualityGate", hops=2, top_k=4)
    assert len(sub["seed_symbols"]) >= 1
    assert "src/math_kernel.py" in sub["affected_files"]
    assert "src/pipeline_runner.py" in sub["affected_files"]


def test_property_and_mutation_rigor_and_ast_mutation_kill_ratio(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "src" / "calc"
    tests_dir = tmp_path / "tests"
    pkg_dir.mkdir(parents=True)
    tests_dir.mkdir(parents=True)

    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "pricing.py").write_text(
        """def net_margin(revenue: float, cost: float) -> float:
    if revenue >= cost:
        return revenue - cost
    return 0.0
""",
        encoding="utf-8",
    )

    # 1. Baseline with NO tests -> must report NO_TESTS_COLLECTED and passed=False (never vacuous PASS)
    no_tests_res = run_ast_mutation_probe(tmp_path, target_rel_path="src/calc/pricing.py", max_mutants=2)
    assert no_tests_res["status"] == "NO_TESTS_COLLECTED"
    assert no_tests_res["passed"] is False
    assert no_tests_res["mutation_kill_ratio"] == 0.0

    # 2. Assertion-free test suite -> GWI-SCI-06 must emit WARN even with <= 2 tests
    (tests_dir / "test_pricing.py").write_text(
        """# from hypothesis import given  (commented import must not count as property fuzzing)
def test_smoke_only() -> None:
    pass
""",
        encoding="utf-8",
    )
    f_empty, _, m_empty = check_property_and_mutation_rigor(tmp_path)
    assert len(f_empty) == 1
    assert f_empty[0]["id"] == "GWI-SCI-06"
    assert m_empty["has_property_fuzzing"] is False

    # 3. Symlink safety: mutating a symlinked file must never overwrite the external target
    ext_file = tmp_path / "external_source.py"
    ext_file.write_text("def ext_fn(x: int) -> int:\n    return x + 1\n", encoding="utf-8")
    sym_link = pkg_dir / "linked.py"
    sym_link.symlink_to(ext_file)

    # 4. Real assertion-dense test suite -> passes GWI-SCI-06 and kills AST mutants
    (tests_dir / "test_pricing.py").write_text(
        """from calc.pricing import net_margin

def test_net_margin_positive_and_floor() -> None:
    assert net_margin(100.0, 40.0) == 60.0
    assert net_margin(50.0, 50.0) == 0.0
    assert net_margin(20.0, 50.0) == 0.0
""",
        encoding="utf-8",
    )

    findings, passes, metrics = check_property_and_mutation_rigor(tmp_path)
    assert len(findings) == 0
    assert metrics["total_test_functions"] == 1
    assert metrics["total_assertions"] == 3
    assert len(passes) == 1

    # Mutate symlinked file and confirm external_source.py is untouched
    run_ast_mutation_probe(tmp_path, target_rel_path="src/calc/linked.py", max_mutants=1)
    assert "x + 1" in ext_file.read_text(encoding="utf-8")
    sym_link.unlink()

    mut_res = run_ast_mutation_probe(tmp_path, target_rel_path="src/calc/pricing.py", max_mutants=4)
    assert mut_res["mutants_tested"] >= 2
    assert mut_res["mutants_killed"] >= 2
    assert mut_res["mutation_kill_ratio"] >= 0.80
    assert mut_res["status"] == "PASS"
    assert mut_res["passed"] is True


def test_check_formal_proofs_detects_lean_sorry_and_certifies_clean_proofs(tmp_path: Path) -> None:
    (tmp_path / "paper.tex").write_text(
        r"""\documentclass{article}
\begin{document}
\begin{theorem}
For all $\lambda > 0$, $\Delta\tau^\star > 0$.
\end{theorem}
\end{document}
""",
        encoding="utf-8",
    )
    (tmp_path / "CascadeProof.lean").write_text(
        """theorem hysteresis_pos (x : Nat) : x + 0 = x := by
  sorry
""",
        encoding="utf-8",
    )
    bad_cert = check_formal_proofs(tmp_path)
    assert not bad_cert["passed"]
    assert len(bad_cert["lean_sorries"]) == 1
    assert bad_cert["findings"][0]["id"] == "GWI-SCI-07"

    # Replace sorry with rfl and include Lean block/doc comments containing the words `sorry` and `admit`
    (tmp_path / "CascadeProof.lean").write_text(
        """/-- Doc comment explaining why this theorem needs no sorry or admit. -/
/- Multi-line block comment:
   sorry is not used here. -/
theorem hysteresis_pos (x : Nat) : x + 0 = x := by
  rfl
""",
        encoding="utf-8",
    )
    (tmp_path / "verify_proof.py").write_text(
        """import math
try:
    import sympy as sp
except ImportError:
    sp = None

def test_symbolic_residual() -> None:
    if sp is not None:
        x = sp.Symbol("x")
        assert sp.simplify((x + 1) - x) == 1
    assert math.isclose(2.0 * 2.0, 4.0)
""",
        encoding="utf-8",
    )
    good_cert = check_formal_proofs(tmp_path, execute_symbolic=True)
    assert good_cert["passed"]
    assert len(good_cert["lean_theorems"]) == 1
    assert len(good_cert["lean_sorries"]) == 0
    assert good_cert["tex_theorem_count"] == 1
    assert len(good_cert["certificate_sha256"]) == 16


def test_compile_organon_prompts_and_sleep_wake_lora_export(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "02-operating-system").mkdir(parents=True)
    (vault / "02-operating-system" / "laws.md").write_text(
        "# Subtractive Design Laws\n\n" + ("Law L7 enforces canonical regularity across UI surfaces. " * 6),
        encoding="utf-8",
    )
    ledger = tmp_path / "triad_provenance_ledger.jsonl"
    ledger.write_text(
        "\n".join(
            [
                "[]",  # Non-dict JSONL line must be safely ignored
                json.dumps({"wp_id": 999, "disposition": "APPROVED", "goldwood_g": 0.0}),  # 0.0 score must be rejected
                json.dumps(
                    {
                        "wp_id": 1766,
                        "subject": "SWM Control Plane",
                        "disposition": "NOT_APPROVED",
                        "goldwood_g": 8.4,
                        "blockers": [{"id": "B1", "description": "Missing <20MB companion MP4"}],
                    }
                ),
                json.dumps(
                    {
                        "wp_id": 1766,
                        "subject": "SWM Control Plane",
                        "disposition": "APPROVED",
                        "goldwood_g": 9.66,
                        "worker": "fable",
                        "summary": "Delivered full 6-stage pipeline with <20MB companion MP4 and G=9.66",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    compiled = compile_organon_prompts(vault, provenance_ledger_path=ledger)
    assert compiled["objective_j_prompt"] > 0.75
    assert len(compiled["stages"]["stage1_triage"]["few_shot_exemplars"]) == 1
    assert compiled["stages"]["stage1_triage"]["few_shot_exemplars"][0]["wp_id"] == 1766
    assert "Missing <20MB companion MP4" in compiled["stages"]["stage3_implement"]["anti_regression_guardrails"]
    assert (vault / ".kb" / "compiled_organon_prompts.json").is_file()

    # Verify monotonic non-regression: empty ledger must not overwrite higher-scoring incumbent
    empty_ledger = tmp_path / "empty_ledger.jsonl"
    empty_ledger.write_text("", encoding="utf-8")
    preserved = compile_organon_prompts(vault, provenance_ledger_path=empty_ledger)
    assert preserved["monotonic_preserved"] is True
    assert preserved["objective_j_prompt"] == compiled["objective_j_prompt"]

    lora_manifest = export_sleep_wake_lora_dataset(vault, provenance_ledger_path=ledger)
    assert lora_manifest["mining_guardrail_enforced"] is True
    assert lora_manifest["export_only"] is True
    assert lora_manifest["sft_trajectory_count"] >= 2
    assert lora_manifest["grpo_preference_pair_count"] == 1
    assert Path(lora_manifest["sft_path"]).is_file()
    assert Path(lora_manifest["grpo_path"]).is_file()
    assert Path(lora_manifest["gx10_script_path"]).is_file()


def test_3dgs_splat_header_parser_and_auditor(tmp_path: Path) -> None:
    ply_path = tmp_path / "venue_pod.ply"
    ply_header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        "element vertex 125000\n"
        "property float x\n"
        "property float y\n"
        "property float z\n" + "".join(f"property float f_rest_{i}\n" for i in range(9)) + "end_header\n"
    )
    ply_path.write_bytes(ply_header.encode("ascii") + b"\x00" * 512)

    # 1.92 MB `.splat` file (60,000 gaussians) -> tests that directory audit does NOT drop >1.5MB assets
    splat_path = tmp_path / "compact_scene.splat"
    splat_path.write_bytes(b"\x00" * (32 * 60000))

    # Valid Niantic `.spz` header with magic 0x5053474E ("NGSP"), version 2, 5000 points, SH degree 2
    spz_path = tmp_path / "compressed.spz"
    spz_path.write_bytes(struct.pack("<IIIb3x", 0x5053474E, 2, 5000, 2) + b"\x00" * 64)

    info_ply = parse_splat_asset_header(ply_path)
    assert info_ply["gaussian_count"] == 125000
    assert info_ply["sh_degree"] == 1
    assert info_ply["quest3_90fps_budget_ok"] is True
    assert info_ply["passed"] is True

    info_spz = parse_splat_asset_header(spz_path)
    assert info_spz["gaussian_count"] == 5000
    assert info_spz["sh_degree"] == 2
    assert info_spz["passed"] is True

    report = audit_and_package_3dgs(tmp_path)
    assert report["asset_count"] == 3
    assert report["total_gaussians"] == 190000
    assert report["passed"] is True

    # Empty `.spz` must fail (`passed=False`)
    empty_spz = tmp_path / "empty.spz"
    empty_spz.write_bytes(b"")
    assert parse_splat_asset_header(empty_spz)["passed"] is False

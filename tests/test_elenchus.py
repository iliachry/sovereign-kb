"""Deterministic test suite for Elenchus (`F-20`: pre-seal cross-lineage challenge & adjudication gate)."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import sovereign_kb.audit_registry as audit_registry
from sovereign_kb.audit_registry import evaluate_subproject_freshness, record_gwi_audit
from sovereign_kb.cli import main as skb_main
from sovereign_kb.elenchus import (
    ANCHOR_ECHO_THRESHOLD,
    _compute_worktree_content_sha,
    _strip_inline_formatting,
    adjudicate_elenchus_finding,
    build_elenchus_challenge_packet,
    classify_model_lineage,
    compute_effective_panel_size,
    evaluate_elenchus_gate,
    parse_oc_triad_stdout,
    parse_reviewer_output,
    run_elenchus_challenge,
    synthesize_elenchus_verdict,
)
from sovereign_kb.evr import EvrReader, seal_gwi_audit_capsule
from sovereign_kb.organon import main_cli as organon_main


def _init_git_repo(repo_dir: Path) -> None:
    (repo_dir / ".gitignore").write_text("evidence/\nartifacts/\n.pytest_cache/\n__pycache__/\n", encoding="utf-8")
    (repo_dir / "README.md").write_text(
        "# Elenchus Sample\n\nDeterministic verification target.\n",
        encoding="utf-8",
    )
    (repo_dir / "AGENTS.md").write_text(
        "# AGENTS.md\n\n## Feature Registry\n- `FEAT-01`: Safe division (`src/engine.py`)\n",
        encoding="utf-8",
    )
    (repo_dir / "pyproject.toml").write_text(
        '[project]\nname = "elenchus-sample"\nversion = "0.1.0"\nrequires-python = ">=3.10"\ndependencies = []\n',
        encoding="utf-8",
    )
    (repo_dir / "src").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "engine.py").write_text(
        "def safe_ratio(num: float, den: float) -> float:\n"
        "    if den <= 0:\n"
        "        raise ValueError('denominator must be positive')\n"
        "    return num / den\n",
        encoding="utf-8",
    )
    (repo_dir / "tests").mkdir(parents=True, exist_ok=True)
    (repo_dir / "tests" / "test_engine.py").write_text(
        'def test_feat_01() -> None:\n    """FEAT-01"""\n    assert 4.0 / 2.0 == 2.0\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo_dir), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", "feat: add safe_ratio engine"], check=True)


def test_elenchus_lineage_classification_and_hill_independence_f20() -> None:
    """F-20: Verify gateway-prefix stripping and Hill-number N_eff lineage independence calculation."""
    assert classify_model_lineage("amazon-bedrock/global.anthropic.claude-fable-5-1") == "anthropic"
    assert classify_model_lineage("amazon-bedrock/global.openai.gpt-6-astra") == "openai"
    assert classify_model_lineage("amazon-bedrock/us.amazon.nova-pro-v1:0") == "amazon"
    assert classify_model_lineage("openrouter/google/gemini-3-pro") == "google"
    assert classify_model_lineage("qwen3.6-35b-a3b") == "qwen"
    assert classify_model_lineage("elenchus-local-structural/1.0") == "deterministic"
    assert classify_model_lineage("kb-gwi/1.4.0") == "deterministic"

    dual = compute_effective_panel_size(
        [
            "amazon-bedrock/global.anthropic.claude-fable-5-1",
            "amazon-bedrock/global.openai.gpt-6-astra",
        ]
    )
    assert dual["n_eff"] == pytest.approx(2.0, abs=1e-4)
    assert dual["independent_panel"] is True
    assert dual["panel_mode"] == "CROSS_LINEAGE_INDEPENDENT"
    assert dual["gate_closable"] is True
    assert dual["warning"] == ""

    monoculture = compute_effective_panel_size(
        [
            "claude-sonnet-5-5",
            "amazon-bedrock/global.anthropic.claude-fable-5-1",
            "claude-opus-5-5",
        ]
    )
    assert monoculture["n_eff"] == pytest.approx(1.0, abs=1e-4)
    assert monoculture["independent_panel"] is False
    assert monoculture["panel_mode"] == "DEGRADED_SINGLE_LINEAGE"
    assert monoculture["gate_closable"] is False
    assert "DEGRADED" in monoculture["warning"]

    single_plus_det = compute_effective_panel_size(
        [
            "amazon-bedrock/global.anthropic.claude-fable-5-1",
            "elenchus-local-structural/1.0",
        ]
    )
    assert single_plus_det["n_eff"] == pytest.approx(1.0, abs=1e-4)
    assert single_plus_det["independent_panel"] is False
    assert single_plus_det["gate_closable"] is False

    tri = compute_effective_panel_size(
        [
            "amazon-bedrock/global.anthropic.claude-fable-5-1",
            "amazon-bedrock/global.openai.gpt-6-astra",
            "qwen3.6-35b-a3b",
        ]
    )
    assert tri["n_eff"] == pytest.approx(3.0, abs=1e-4)
    assert tri["independent_panel"] is True


def test_elenchus_score_blind_packet_and_anchoring_vs_split_calibration_f20(tmp_path: Path) -> None:
    """F-20: Verify score-blind prompt sanitization and convergence vs. prompt-echo vs. split calibration."""
    repo_dir = tmp_path / "calib-repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)

    ev_dir = repo_dir / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    (ev_dir / "goldwood.json").write_text(
        json.dumps(
            {
                "standardVersion": "1.4.0",
                "profile": "app",
                "project": "calib-repo",
                "generatedAt": "2026-11-17T12:00:00Z",
                "compositeScoreG": 9.64,
                "linearScoreG": 9.64,
                "status": "PASS",
                "verdict": "PASS (9+ GOLDWOOD SOVEREIGN STANDARD)",
                "tiers": {"S_sub": 9.5, "S_perc": 9.6, "S_act": 9.8, "S_exist": 9.6},
                "blockers": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (ev_dir / "gate_receipt.json").write_text(
        json.dumps({"passed": True, "executed": True, "fresh_for_tree": True, "exit_code": 0}),
        encoding="utf-8",
    )

    custom_intent = "Ensure error boundary sanitization rejects malformed telemetry packets before persistence"
    packet = build_elenchus_challenge_packet(repo_dir, intent=custom_intent, blind_scores=True)

    assert "9.64" not in packet["prompt"]
    assert "PASS (9+ GOLDWOOD SOVEREIGN STANDARD)" not in packet["prompt"]
    assert "compositeScoreG" not in packet["prompt"]
    assert packet["bindings"]["gwi_receipt_sha256"] != ""
    assert packet["bindings"]["gate_receipt_sha256"] != ""

    # 2 uncorrelated reviewers producing:
    # (a) grounded convergence on src/engine.py
    # (b) ungrounded prompt-anchored echo repeating exact prompt/intent n-grams without file_path
    # (c) single-lineage split on src/engine.py
    reviews = [
        {
            "model_id": "amazon-bedrock/global.anthropic.claude-fable-5-1",
            "verdict": "CHALLENGE",
            "summary": "Found grounded bounds defect and echoed prompt instructions.",
            "findings": [
                {
                    "severity": "P1",
                    "category": "correctness",
                    "file_path": "src/engine.py",
                    "line": 2,
                    "symbol": "safe_ratio",
                    "claim": "safe_ratio does not reject NaN or Inf denominators when den <= 0 is False",
                    "evidence": "src/engine.py:2 checks `den <= 0`, which evaluates False for float('nan')",
                },
                {
                    "severity": "P2",
                    "category": "provenance",
                    "file_path": "",
                    "line": 0,
                    "symbol": "",
                    "claim": (
                        "Ensure error boundary sanitization rejects malformed telemetry packets before persistence "
                        "and satisfy the stated intent across behavioral tests and security boundaries"
                    ),
                    "evidence": "Challenge whether the implementation and behavioral tests satisfy the stated intent",
                },
            ],
        },
        {
            "model_id": "amazon-bedrock/global.openai.gpt-6-astra",
            "verdict": "CHALLENGE",
            "summary": "Independent review spotted NaN bypass, missing test assertion, and echoed boilerplate.",
            "findings": [
                {
                    "severity": "P1",
                    "category": "correctness",
                    "file_path": "src/engine.py",
                    "line": 3,
                    "symbol": "safe_ratio",
                    "claim": "NaN input passes `den <= 0` in safe_ratio without raising ValueError",
                    "evidence": "src/engine.py:2-4 omits math.isfinite check on num and den",
                },
                {
                    "severity": "P2",
                    "category": "provenance",
                    "file_path": "",
                    "line": 0,
                    "symbol": "",
                    "claim": (
                        "Ensure error boundary sanitization rejects malformed telemetry packets before persistence "
                        "and satisfy the stated intent across behavioral tests and security boundaries"
                    ),
                    "evidence": "Challenge whether the implementation and behavioral tests satisfy the stated intent",
                },
                {
                    "severity": "P1",
                    "category": "test_gap",
                    "file_path": "tests/test_engine.py",
                    "line": 1,
                    "symbol": "test_feat_01",
                    "claim": "test_feat_01 asserts arithmetic literal instead of calling safe_ratio",
                    "evidence": "tests/test_engine.py:3 uses `4.0 / 2.0 == 2.0` instead of invoking safe_ratio",
                },
            ],
        },
    ]

    syn = synthesize_elenchus_verdict(reviews, packet, target_dir=repo_dir)
    by_sig = {c["signal_class"]: c for c in syn["clusters"]}
    assert "INDEPENDENT_CONVERGENCE" in by_sig
    assert by_sig["INDEPENDENT_CONVERGENCE"]["file_path"] == "src/engine.py"
    assert by_sig["INDEPENDENT_CONVERGENCE"]["grounded_in_repo"] is True

    assert "PROMPT_ANCHORED_ECHO" in by_sig
    assert by_sig["PROMPT_ANCHORED_ECHO"]["anchoring_ratio"] >= ANCHOR_ECHO_THRESHOLD
    assert by_sig["PROMPT_ANCHORED_ECHO"]["requires_adjudication"] is False

    assert "LINEAGE_SPLIT" in by_sig
    assert by_sig["LINEAGE_SPLIT"]["file_path"] == "tests/test_engine.py"
    assert by_sig["LINEAGE_SPLIT"]["requires_adjudication"] is True
    assert syn["metrics"]["disagreement_index"] > 0.0
    assert syn["seal_status"] == "PENDING_ADJUDICATION"

    # Top-level verdict split without structured findings still produces LINEAGE_SPLIT cluster
    split_verdict_syn = synthesize_elenchus_verdict(
        [
            {
                "model_id": "amazon-bedrock/global.anthropic.claude-fable-5-1",
                "verdict": "APPROVE",
                "summary": "Clean",
                "findings": [],
            },
            {
                "model_id": "amazon-bedrock/global.openai.gpt-6-astra",
                "verdict": "VETO",
                "summary": "Architectural concern with error propagation",
                "findings": [],
            },
        ],
        packet,
        target_dir=repo_dir,
    )
    assert any(c["signal_class"] == "LINEAGE_SPLIT" for c in split_verdict_syn["clusters"])
    assert split_verdict_syn["seal_status"] == "PENDING_ADJUDICATION"


def test_elenchus_adjudication_fsm_law_l10_and_evr_sealing_gate_f20(tmp_path: Path, monkeypatch) -> None:
    """F-20: Verify adjudication FSM, Law L10 anti-rubber-stamp checks, SQLite .evr seal, and worktree invalidation."""
    swm_home = tmp_path / "swm-home"
    monkeypatch.setenv("SWM_HOME", str(swm_home))
    monkeypatch.setattr(audit_registry, "AUDIT_REGISTRY_PATH", swm_home / "audit_registry.json")

    repo_dir = tmp_path / "gate-repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)
    ev_dir = repo_dir / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    goldwood_file = ev_dir / "goldwood.json"
    goldwood_file.write_text(
        json.dumps(
            {
                "standardVersion": "1.4.0",
                "profile": "app",
                "project": "gate-repo",
                "generatedAt": "2026-11-17T12:00:00Z",
                "compositeScoreG": 9.64,
                "status": "PASS",
                "tiers": {"S_sub": 9.5, "S_perc": 9.6, "S_act": 9.8, "S_exist": 9.6},
                "craftLenses": {"coupled_goldwood_g": 9.64, "lenses": [{"id": "principal-verification", "score": 9.6}]},
                "blockers": [],
            }
        ),
        encoding="utf-8",
    )

    gwi_report = {
        "project_name": "gate-repo",
        "profile": "app",
        "calculation": {"goldwood_g": 9.64, "status": "PASS", "barrier_product": 1.0, "snrc": 20.0},
        "tiers": {"S_sub": 9.5, "S_perc": 9.6, "S_act": 9.8, "S_exist": 9.6},
        "craft_lenses": {"coupled_goldwood_g": 9.64, "lenses": [{"id": "principal-verification", "score": 9.6}]},
    }

    reviews = [
        {
            "model_id": "amazon-bedrock/global.anthropic.claude-fable-5-1",
            "verdict": "CHALLENGE",
            "summary": "Converged on bounds edge case.",
            "findings": [
                {
                    "severity": "P1",
                    "category": "correctness",
                    "file_path": "src/engine.py",
                    "line": 2,
                    "symbol": "safe_ratio",
                    "claim": "Bounded finite check missing for NaN denominator in safe_ratio",
                    "evidence": "src/engine.py:2",
                }
            ],
        },
        {
            "model_id": "amazon-bedrock/global.openai.gpt-6-astra",
            "verdict": "CHALLENGE",
            "summary": "Spotted bounds edge case plus a test coverage split.",
            "findings": [
                {
                    "severity": "P1",
                    "category": "correctness",
                    "file_path": "src/engine.py",
                    "line": 3,
                    "symbol": "safe_ratio",
                    "claim": "safe_ratio omits math.isfinite guard for NaN",
                    "evidence": "src/engine.py:2-4",
                },
                {
                    "severity": "P1",
                    "category": "test_gap",
                    "file_path": "tests/test_engine.py",
                    "line": 1,
                    "symbol": "test_feat_01",
                    "claim": "test_feat_01 does not directly call safe_ratio",
                    "evidence": "tests/test_engine.py:3",
                },
            ],
        },
    ]

    receipt = run_elenchus_challenge(repo_dir, reviews=reviews, ledger_file=swm_home / "ledger.jsonl")
    assert receipt["seal_status"] == "PENDING_ADJUDICATION"
    assert len(receipt["clusters"]) == 2
    ids = [c["cluster_id"] for c in receipt["clusters"]]

    # Sealing with --require-elenchus MUST block while PENDING_ADJUDICATION
    with pytest.raises(ValueError, match="PENDING_ADJUDICATION"):
        seal_gwi_audit_capsule(
            repo_dir,
            output_path=ev_dir / "blocked.evr",
            report=gwi_report,
            sign_key="test-key",
            ledger_file=swm_home / "ledger.jsonl",
            require_elenchus=True,
        )

    # Law L10: bulk 'ALL' override is forbidden when > 1 pending clusters exist
    with pytest.raises(ValueError, match="Bulk 'ALL'"):
        adjudicate_elenchus_finding(
            repo_dir,
            finding_id="ALL",
            decision="reject",
            reason="Attempting bulk rubber stamp override across multiple findings",
            ledger_file=swm_home / "ledger.jsonl",
        )

    # Law L10: short rejection rationale (< 12 chars) is rejected
    with pytest.raises(ValueError, match="Explicit rationale"):
        adjudicate_elenchus_finding(
            repo_dir,
            finding_id=ids[0],
            decision="reject",
            reason="ok",
            ledger_file=swm_home / "ledger.jsonl",
        )

    # ACCEPT_FINDING transitions to BLOCKED_DEFECT_CONFIRMED and still blocks sealing
    accepted = adjudicate_elenchus_finding(
        repo_dir,
        finding_id=ids[0],
        decision="accept",
        reason="Confirmed valid edge case; keeping blocked until fixed",
        adjudicator="ilias",
        ledger_file=swm_home / "ledger.jsonl",
    )
    assert accepted["seal_status"] == "BLOCKED_DEFECT_CONFIRMED"
    with pytest.raises(ValueError, match="BLOCKED_DEFECT_CONFIRMED"):
        seal_gwi_audit_capsule(
            repo_dir,
            output_path=ev_dir / "blocked2.evr",
            report=gwi_report,
            sign_key="test-key",
            ledger_file=swm_home / "ledger.jsonl",
            require_elenchus=True,
        )

    # Explicitly adjudicate each finding with substantive human rationale -> READY_ADJUDICATED
    adjudicate_elenchus_finding(
        repo_dir,
        finding_id=ids[0],
        decision="reject",
        reason="Upstream input validator already rejects non-finite IEEE floats before safe_ratio",
        adjudicator="ilias",
        ledger_file=swm_home / "ledger.jsonl",
    )
    ready = adjudicate_elenchus_finding(
        repo_dir,
        finding_id=ids[1],
        decision="reject",
        reason="Direct safe_ratio coverage lives in integration suite out of this thin diff scope",
        adjudicator="ilias",
        ledger_file=swm_home / "ledger.jsonl",
    )
    assert ready["seal_status"] == "READY_ADJUDICATED"

    sealed = seal_gwi_audit_capsule(
        repo_dir,
        output_path=ev_dir / "goldwood.evr",
        report=gwi_report,
        sign_key="test-key",
        ledger_file=swm_home / "ledger.jsonl",
        require_elenchus=True,
    )
    assert sealed["verification"]["valid"] is True
    assert sealed["elenchus"]["seal_status"] == "READY_ADJUDICATED"
    assert sealed["pistis_chain_event"]["merkle_root"] == sealed["merkle_root"]

    with EvrReader(ev_dir / "goldwood.evr") as reader:
        ver = reader.verify(sign_key="test-key")
        assert ver["valid"] is True
        el_json = json.loads(reader.read_chunk("audit/elenchus_challenge.json").decode("utf-8"))
        assert el_json["seal_status"] == "READY_ADJUDICATED"
        sql_rows = reader.sql(
            "SELECT cluster_id, signal_class, adjudication, adjudicator, reason "
            "FROM elenchus_dissent ORDER BY cluster_id;"
        )
        assert len(sql_rows) == 2
        assert all(r["adjudication"] == "REJECT_FINDING" for r in sql_rows)
        assert all(r["adjudicator"] == "ilias" for r in sql_rows)
        assert any("Upstream input validator" in r["reason"] for r in sql_rows)

    # Changing goldwood.json semantic score invalidates receipt (`STALE_GWI_RECEIPT`)
    goldwood_file.write_text(
        json.dumps(
            {
                "standardVersion": "1.4.0",
                "profile": "app",
                "project": "gate-repo",
                "generatedAt": "2026-11-17T12:05:00Z",
                "compositeScoreG": 7.10,
                "status": "FAIL",
            }
        ),
        encoding="utf-8",
    )
    gwi_drift = evaluate_elenchus_gate(repo_dir, require_independent=True)
    assert gwi_drift["passed"] is False
    assert gwi_drift["reason_code"] == "STALE_GWI_RECEIPT"

    # Mutating source code immediately triggers STALE_WORKTREE and blocks both sealing and stale adjudication
    (repo_dir / "src" / "engine.py").write_text(
        "def safe_ratio(num: float, den: float) -> float:\n    return num / den\n",
        encoding="utf-8",
    )
    stale_gate = evaluate_elenchus_gate(repo_dir, require_independent=True)
    assert stale_gate["passed"] is False
    assert stale_gate["reason_code"] == "STALE_WORKTREE"
    with pytest.raises(ValueError, match="STALE_WORKTREE"):
        seal_gwi_audit_capsule(
            repo_dir,
            output_path=ev_dir / "stale.evr",
            report=gwi_report,
            sign_key="test-key",
            ledger_file=swm_home / "ledger.jsonl",
            require_elenchus=True,
        )
    with pytest.raises(ValueError, match="stale Elenchus receipt"):
        adjudicate_elenchus_finding(
            repo_dir,
            finding_id=ids[0],
            decision="reject",
            reason="Attempting to adjudicate after modifying code",
            ledger_file=swm_home / "ledger.jsonl",
        )


def test_elenchus_offline_degraded_honesty_cli_and_freshness_preservation_f20(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """F-20: Verify offline degraded honesty, CLI aliases (`skb`/`organon`), `oc triad` parser, and GWI cache freshness."""
    swm_home = tmp_path / "swm-home"
    monkeypatch.setenv("SWM_HOME", str(swm_home))
    monkeypatch.setattr(audit_registry, "AUDIT_REGISTRY_PATH", swm_home / "audit_registry.json")

    repo_dir = tmp_path / "cli-repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)

    # Record initial passing GWI audit
    fake_gwi = {
        "profile": "app",
        "calculation": {"goldwood_g": 9.60, "status": "PASS", "verdict": "PASS"},
        "tiers": {"S_sub": 9.6, "S_perc": 9.6, "S_act": 9.6, "S_exist": 9.6},
        "craft_lenses": {"coupled_goldwood_g": 9.60, "veto_triggered": False},
        "audit_flags": {"strict_lenses": False, "probe_audio": False, "execute_gate": False},
        "blockers": [],
    }
    record_gwi_audit(repo_dir, fake_gwi)
    assert evaluate_subproject_freshness(repo_dir, ".")["freshness"] == "FRESH"

    # Run `skb elenchus run <repo> --offline --json`
    skb_main(["elenchus", "run", str(repo_dir), "--offline", "--json"])
    run_out = json.loads(capsys.readouterr().out)
    assert run_out["panel"]["panel_mode"] == "DEGRADED_SINGLE_LINEAGE"
    assert run_out["panel"]["gate_closable"] is False
    assert run_out["seal_status"] == "DEGRADED_NON_INDEPENDENT"
    assert (repo_dir / "evidence" / "elenchus_challenge.json").is_file()

    # Writing evidence/elenchus_challenge.json must NEVER trigger STALE_EVIDENCE in audit_registry
    assert evaluate_subproject_freshness(repo_dir, ".")["freshness"] == "FRESH"

    # Offline degraded receipt MUST fail closed when --require-elenchus is enforced
    with pytest.raises(ValueError, match="NON_INDEPENDENT"):
        seal_gwi_audit_capsule(
            repo_dir,
            output_path=repo_dir / "evidence" / "fail.evr",
            report=fake_gwi,
            ledger_file=swm_home / "ledger.jsonl",
            require_elenchus=True,
        )

    # `organon challenge status <repo> --json` works seamlessly via alias
    organon_main(["challenge", "status", str(repo_dir), "--json"])
    status_out = json.loads(capsys.readouterr().out)
    assert status_out["gate"]["passed"] is False
    assert status_out["gate"]["reason_code"] == "NON_INDEPENDENT_PANEL"

    # Verify parse_oc_triad_stdout splits parallel Fable 5.1 + GPT-6 Astra prose transcripts cleanly
    sample_triad = (
        "=== CLAUDE FABLE 5.1 (exit=0) ===\n"
        "Review complete.\n"
        "- [P1] src/engine.py:2 — NaN check missing in safe_ratio.\n"
        "- [P0] None\n"
        "Disposition: REQUEST CHANGES\n\n"
        "=== GPT-6 ASTRA (exit=0) ===\n"
        "Independent verification:\n"
        "No defects found in scope (0 P0 / 0 P1 / 0 P2).\n"
        "Disposition: APPROVED\n"
    )
    parsed_triad = parse_oc_triad_stdout(sample_triad)
    assert len(parsed_triad) == 2
    assert parsed_triad[0]["lineage"] == "anthropic"
    assert parsed_triad[0]["verdict"] == "CHALLENGE"
    assert len(parsed_triad[0]["findings"]) == 1
    assert parsed_triad[0]["findings"][0]["file_path"] == "src/engine.py"
    assert parsed_triad[1]["lineage"] == "openai"
    assert parsed_triad[1]["verdict"] == "APPROVE"
    assert len(parsed_triad[1]["findings"]) == 0


def test_elenchus_tampered_receipt_and_non_enforced_stale_sealing_f20(tmp_path: Path, monkeypatch) -> None:
    """F-20 regression (#1, #8): reject tampered receipts and surface STALE when sealing with require_elenchus=False."""
    swm_home = tmp_path / "swm-home"
    monkeypatch.setenv("SWM_HOME", str(swm_home))
    monkeypatch.setattr(audit_registry, "AUDIT_REGISTRY_PATH", swm_home / "audit_registry.json")

    repo_dir = tmp_path / "tamper-repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)
    ev_dir = repo_dir / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    gwi_report = {
        "project_name": "tamper-repo",
        "profile": "app",
        "calculation": {"goldwood_g": 9.64, "status": "PASS", "barrier_product": 1.0, "snrc": 20.0},
        "tiers": {"S_sub": 9.5, "S_perc": 9.6, "S_act": 9.8, "S_exist": 9.6},
        "craft_lenses": {"coupled_goldwood_g": 9.64, "lenses": [{"id": "principal-verification", "score": 9.6}]},
        "blockers": [],
    }
    (ev_dir / "goldwood.json").write_text(
        json.dumps(
            {
                "profile": "app",
                "project": "tamper-repo",
                "compositeScoreG": 9.64,
                "status": "PASS",
                "tiers": gwi_report["tiers"],
                "craftLenses": gwi_report["craft_lenses"],
                "blockers": [],
            }
        ),
        encoding="utf-8",
    )

    reviews = [
        {
            "model_id": "amazon-bedrock/global.anthropic.claude-fable-5-1",
            "verdict": "VETO",
            "summary": "Critical issue",
            "findings": [
                {
                    "severity": "P0",
                    "category": "security",
                    "file_path": "src/engine.py",
                    "line": 1,
                    "symbol": "safe_ratio",
                    "claim": "Unhandled NaN denominator bypasses guard",
                    "evidence": "src/engine.py:2",
                }
            ],
        },
        {
            "model_id": "amazon-bedrock/global.openai.gpt-6-astra",
            "verdict": "CHALLENGE",
            "summary": "Confirmed NaN issue",
            "findings": [
                {
                    "severity": "P0",
                    "category": "security",
                    "file_path": "src/engine.py",
                    "line": 2,
                    "symbol": "safe_ratio",
                    "claim": "safe_ratio permits float('nan') without raising ValueError",
                    "evidence": "src/engine.py:2",
                }
            ],
        },
    ]
    receipt = run_elenchus_challenge(repo_dir, reviews=reviews, ledger_file=swm_home / "ledger.jsonl")
    assert receipt["seal_status"] == "PENDING_ADJUDICATION"

    # Case A: tamper seal_status to READY_UNANIMOUS while P0 cluster remains PENDING
    receipt_path = ev_dir / "elenchus_challenge.json"
    tampered = json.loads(receipt_path.read_text(encoding="utf-8"))
    tampered["seal_status"] = "READY_UNANIMOUS"
    receipt_path.write_text(json.dumps(tampered, indent=2), encoding="utf-8")
    g_tamper = evaluate_elenchus_gate(repo_dir, gwi_report=gwi_report, require_independent=True)
    assert g_tamper["passed"] is False
    assert g_tamper["reason_code"] == "CORRUPTED_OR_CONTRADICTORY_RECEIPT"

    # Case B: tamper adjudication to REJECT_FINDING with 2-char rationale (< 12 chars)
    tampered_short = json.loads(json.dumps(receipt))
    tampered_short["clusters"][0]["adjudication"] = {
        "status": "ADJUDICATED",
        "decision": "REJECT_FINDING",
        "adjudicator": "attacker",
        "reason": "no",
        "adjudicated_at": "2026-11-17T12:00:00Z",
    }
    tampered_short["seal_status"] = "READY_ADJUDICATED"
    g_short = evaluate_elenchus_gate(repo_dir, receipt=tampered_short, gwi_report=gwi_report, require_independent=True)
    assert g_short["passed"] is False
    assert g_short["reason_code"] == "CORRUPTED_OR_CONTRADICTORY_RECEIPT"

    # Case C: spoof panel N_eff on single-lineage receipt
    single_rec = run_elenchus_challenge(
        repo_dir,
        reviews=[reviews[0]],
        write_receipt=False,
        ledger_file=swm_home / "ledger.jsonl",
    )
    single_rec["panel"]["independent_panel"] = True
    single_rec["panel"]["n_eff"] = 2.0
    g_spoof = evaluate_elenchus_gate(repo_dir, receipt=single_rec, gwi_report=gwi_report, require_independent=True)
    assert g_spoof["passed"] is False
    assert g_spoof["reason_code"] == "CORRUPTED_OR_CONTRADICTORY_RECEIPT"

    # Restore valid receipt, adjudicate properly, then mutate code and seal with require_elenchus=False
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    adjudicate_elenchus_finding(
        repo_dir,
        finding_id="ELC-001",
        decision="reject",
        reason="Strict finite validation happens at ingress serialization boundary",
        ledger_file=swm_home / "ledger.jsonl",
    )
    (repo_dir / "src" / "engine.py").write_text(
        "def safe_ratio(num: float, den: float) -> float:\n    return num / den\n",
        encoding="utf-8",
    )
    sealed_non_enforced = seal_gwi_audit_capsule(
        repo_dir,
        output_path=ev_dir / "non_enforced.evr",
        report=gwi_report,
        sign_key="test-key",
        ledger_file=swm_home / "ledger.jsonl",
        require_elenchus=False,
    )
    assert sealed_non_enforced["elenchus"]["gate_passed"] is False
    assert sealed_non_enforced["elenchus"]["seal_status"] == "STALE"
    assert sealed_non_enforced["elenchus"]["gate_reason_code"] == "STALE_WORKTREE"
    with EvrReader(ev_dir / "non_enforced.evr") as reader:
        embedded_el = json.loads(reader.read_chunk("audit/elenchus_challenge.json").decode("utf-8"))
        assert embedded_el["seal_status"] == "STALE"
        assert embedded_el["recorded_seal_status"] == "READY_ADJUDICATED"
        assert embedded_el["gate_verification"]["passed"] is False


def test_elenchus_bulk_all_reject_guard_and_pending_preservation_f20(tmp_path: Path, monkeypatch) -> None:
    """F-20 regression (#9): block ALL+reject after partial ACCEPT_FINDING and preserve adjudicated clusters."""
    swm_home = tmp_path / "swm-home"
    monkeypatch.setenv("SWM_HOME", str(swm_home))
    monkeypatch.setattr(audit_registry, "AUDIT_REGISTRY_PATH", swm_home / "audit_registry.json")

    repo_dir = tmp_path / "bulk-all-repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)
    reviews = [
        {
            "model_id": "amazon-bedrock/global.anthropic.claude-fable-5-1",
            "verdict": "CHALLENGE",
            "summary": "Two issues",
            "findings": [
                {
                    "severity": "P1",
                    "category": "correctness",
                    "file_path": "src/engine.py",
                    "line": 2,
                    "symbol": "safe_ratio",
                    "claim": "NaN bypass in safe_ratio check",
                    "evidence": "src/engine.py:2",
                },
                {
                    "severity": "P1",
                    "category": "test_gap",
                    "file_path": "tests/test_engine.py",
                    "line": 1,
                    "symbol": "test_feat_01",
                    "claim": "test_feat_01 never calls safe_ratio directly",
                    "evidence": "tests/test_engine.py:3",
                },
            ],
        },
        {
            "model_id": "amazon-bedrock/global.openai.gpt-6-astra",
            "verdict": "CHALLENGE",
            "summary": "Two independent issues",
            "findings": [
                {
                    "severity": "P1",
                    "category": "correctness",
                    "file_path": "src/engine.py",
                    "line": 2,
                    "symbol": "safe_ratio",
                    "claim": "Missing math.isfinite guard in safe_ratio",
                    "evidence": "src/engine.py:2",
                }
            ],
        },
    ]
    rec = run_elenchus_challenge(repo_dir, reviews=reviews, ledger_file=swm_home / "ledger.jsonl")
    c_ids = [c["cluster_id"] for c in rec["clusters"]]
    assert len(c_ids) == 2

    adjudicate_elenchus_finding(
        repo_dir,
        finding_id=c_ids[0],
        decision="accept",
        reason="Confirmed real NaN bug",
        ledger_file=swm_home / "ledger.jsonl",
    )

    with pytest.raises(ValueError, match="Bulk 'ALL' override is forbidden"):
        adjudicate_elenchus_finding(
            repo_dir,
            finding_id="ALL",
            decision="reject",
            reason="Trying to bulk override remaining finding after accepting first",
            ledger_file=swm_home / "ledger.jsonl",
        )

    upd = adjudicate_elenchus_finding(
        repo_dir,
        finding_id="ALL",
        decision="accept",
        reason="Accept remaining pending finding",
        ledger_file=swm_home / "ledger.jsonl",
    )
    by_id = {c["cluster_id"]: c for c in upd["clusters"]}
    assert by_id[c_ids[0]]["adjudication"]["reason"] == "Confirmed real NaN bug"
    assert by_id[c_ids[1]]["adjudication"]["reason"] == "Accept remaining pending finding"
    with pytest.raises(ValueError, match="No pending adjudicable"):
        adjudicate_elenchus_finding(
            repo_dir,
            finding_id="ALL",
            decision="reject",
            reason="Nothing pending anymore",
            ledger_file=swm_home / "ledger.jsonl",
        )


def test_elenchus_worktree_hashing_modes_untracked_and_bindings_f20(tmp_path: Path, monkeypatch) -> None:
    """F-20 regression (#2, #4): delimiter collisions, untracked files, chmod +x, blank SHA, and GWI/gate bindings."""
    swm_home = tmp_path / "swm-home"
    monkeypatch.setenv("SWM_HOME", str(swm_home))
    monkeypatch.setattr(audit_registry, "AUDIT_REGISTRY_PATH", swm_home / "audit_registry.json")

    # 1. Path/content delimiter collision resistance
    d1 = tmp_path / "col1"
    d2 = tmp_path / "col2"
    d1.mkdir()
    d2.mkdir()
    (d1 / "a").write_bytes(b"bc")
    (d2 / "ab").write_bytes(b"c")
    assert _compute_worktree_content_sha(d1) != _compute_worktree_content_sha(d2)

    repo_dir = tmp_path / "bind-repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)
    ev_dir = repo_dir / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    gwi_report = {
        "project_name": "bind-repo",
        "profile": "app",
        "calculation": {"goldwood_g": 9.64, "status": "PASS", "barrier_product": 1.0, "snrc": 20.0},
        "tiers": {"S_sub": 9.5, "S_perc": 9.6, "S_act": 9.8, "S_exist": 9.6},
        "craft_lenses": {"coupled_goldwood_g": 9.64, "lenses": [{"id": "principal-verification", "score": 9.6}]},
        "blockers": [],
    }
    gw_path = ev_dir / "goldwood.json"
    gw_path.write_text(
        json.dumps(
            {
                "profile": "app",
                "project": "bind-repo",
                "compositeScoreG": 9.64,
                "status": "PASS",
                "tiers": gwi_report["tiers"],
                "craftLenses": gwi_report["craft_lenses"],
                "blockers": [],
            }
        ),
        encoding="utf-8",
    )
    (repo_dir / ".policy").write_text("strict=true\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_dir), "add", ".policy"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", "chore: add dotfile policy"], check=True)
    tree_sha = audit_registry.compute_subproject_fingerprint(repo_dir, ".")["tree_sha"]
    cur_wt_sha = _compute_worktree_content_sha(repo_dir)
    gr_path = ev_dir / "gate_receipt.json"
    gr_path.write_text(
        json.dumps(
            {
                "passed": True,
                "executed": True,
                "exit_code": 0,
                "fresh_for_tree": True,
                "tree_sha": tree_sha,
                "worktree_sha": cur_wt_sha,
            }
        ),
        encoding="utf-8",
    )

    unanimous = [
        {"model_id": "amazon-bedrock/global.anthropic.claude-fable-5-1", "verdict": "APPROVE", "findings": []},
        {"model_id": "amazon-bedrock/global.openai.gpt-6-astra", "verdict": "APPROVE", "findings": []},
    ]
    rec = run_elenchus_challenge(repo_dir, reviews=unanimous, ledger_file=swm_home / "ledger.jsonl")
    assert rec["seal_status"] == "READY_UNANIMOUS"
    assert evaluate_elenchus_gate(repo_dir, gwi_report=gwi_report)["passed"] is True

    # 2. Blank worktree_sha fails closed
    blank_rec = json.loads(json.dumps(rec))
    blank_rec["bindings"]["worktree_sha"] = ""
    assert evaluate_elenchus_gate(repo_dir, receipt=blank_rec, gwi_report=gwi_report)["reason_code"] == "STALE_WORKTREE"

    # 3. Wrong report project, mismatched score, contradictory schema, and FAIL status all fail closed
    wrong_proj = json.loads(json.dumps(gwi_report))
    wrong_proj["project_name"] = "other-project"
    assert evaluate_elenchus_gate(repo_dir, gwi_report=wrong_proj)["reason_code"] == "GWI_REPORT_MISMATCH"

    shadowed_rep = {
        **json.loads(gw_path.read_text(encoding="utf-8")),
        "project_name": "other-project",
        "calculation": {"status": "FAIL", "goldwood_g": 1.0},
    }
    assert (
        evaluate_elenchus_gate(repo_dir, gwi_report=shadowed_rep)["reason_code"] == "CORRUPTED_OR_CONTRADICTORY_RECEIPT"
    )

    mismatch_score = json.loads(json.dumps(gwi_report))
    mismatch_score["calculation"]["goldwood_g"] = 9.95
    assert evaluate_elenchus_gate(repo_dir, gwi_report=mismatch_score)["reason_code"] == "STALE_GWI_RECEIPT"

    fail_report = json.loads(json.dumps(gwi_report))
    fail_report["calculation"]["status"] = "FAIL"
    assert evaluate_elenchus_gate(repo_dir, gwi_report=fail_report)["reason_code"] == "GWI_NOT_PASS"
    with pytest.raises(ValueError, match="GWI_NOT_PASS"):
        seal_gwi_audit_capsule(
            repo_dir,
            output_path=ev_dir / "fail_report.evr",
            report=fail_report,
            require_elenchus=True,
            ledger_file=swm_home / "ledger.jsonl",
        )

    # 4. Mutating gate_receipt.json invalidates gate
    gr_path.write_text(
        json.dumps(
            {
                "passed": True,
                "executed": False,
                "exit_code": 1,
                "fresh_for_tree": False,
                "tree_sha": tree_sha,
                "worktree_sha": "old-content",
            }
        ),
        encoding="utf-8",
    )
    assert evaluate_elenchus_gate(repo_dir, gwi_report=gwi_report)["reason_code"] == "STALE_GATE_RECEIPT"
    gr_path.unlink()
    assert evaluate_elenchus_gate(repo_dir, gwi_report=gwi_report)["reason_code"] == "MISSING_GATE_RECEIPT"

    # Restore gate_receipt and test missing goldwood.json
    gr_path.write_text(
        json.dumps(
            {
                "passed": True,
                "executed": True,
                "exit_code": 0,
                "fresh_for_tree": True,
                "tree_sha": tree_sha,
                "worktree_sha": cur_wt_sha,
            }
        ),
        encoding="utf-8",
    )
    gw_path.unlink()
    assert evaluate_elenchus_gate(repo_dir, gwi_report=gwi_report)["reason_code"] == "MISSING_GWI_RECEIPT"

    # Restore goldwood.json, check fresh pass, then verify group-write stability vs chmod +x & dotfile changes
    gw_path.write_text(
        json.dumps(
            {
                "profile": "app",
                "project": "bind-repo",
                "compositeScoreG": 9.64,
                "status": "PASS",
                "tiers": gwi_report["tiers"],
                "craftLenses": gwi_report["craft_lenses"],
                "blockers": [],
            }
        ),
        encoding="utf-8",
    )
    assert evaluate_elenchus_gate(repo_dir, gwi_report=gwi_report)["passed"] is True
    engine_py = repo_dir / "src" / "engine.py"
    old_mode = engine_py.stat().st_mode
    os.chmod(engine_py, old_mode ^ 0o020)
    assert evaluate_elenchus_gate(repo_dir, gwi_report=gwi_report)["passed"] is True
    os.chmod(engine_py, old_mode | 0o111)
    assert evaluate_elenchus_gate(repo_dir, gwi_report=gwi_report)["reason_code"] == "STALE_WORKTREE"
    os.chmod(engine_py, old_mode)
    assert evaluate_elenchus_gate(repo_dir, gwi_report=gwi_report)["passed"] is True

    (repo_dir / ".policy").write_text("strict=false\n", encoding="utf-8")
    assert evaluate_elenchus_gate(repo_dir, gwi_report=gwi_report)["reason_code"] == "STALE_WORKTREE"
    (repo_dir / ".policy").write_text("strict=true\n", encoding="utf-8")
    assert evaluate_elenchus_gate(repo_dir, gwi_report=gwi_report)["passed"] is True

    (repo_dir / "main.py").write_text("print('untracked root file')\n", encoding="utf-8")
    assert evaluate_elenchus_gate(repo_dir, gwi_report=gwi_report)["reason_code"] == "STALE_WORKTREE"


def test_elenchus_imports_http_failures_aliases_gwi_gates_and_diff_truncation_f20(tmp_path: Path, monkeypatch) -> None:
    """F-20 regression (#3, #5, #6, #7, #9): --reviews binding, failed HTTP & aliases, GWI early gates, untracked diff/truncation, bold prose."""
    swm_home = tmp_path / "swm-home"
    monkeypatch.setenv("SWM_HOME", str(swm_home))
    monkeypatch.setattr(audit_registry, "AUDIT_REGISTRY_PATH", swm_home / "audit_registry.json")

    repo_dir = tmp_path / "edge-repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)

    # 1. Unknown/unverified aliases never inflate N_eff
    assert compute_effective_panel_size(["custom-a", "custom-b"])["independent_panel"] is False
    assert compute_effective_panel_size(["custom-a", "custom-b"])["n_eff"] == pytest.approx(1.0, abs=1e-4)
    assert (
        compute_effective_panel_size(["amazon-bedrock/global.anthropic.claude-fable-5-1", "custom-a"])[
            "independent_panel"
        ]
        is False
    )

    # 2. Failed HTTP reviewer is excluded from N_eff and URL credentials are redacted
    http_fail = run_elenchus_challenge(
        repo_dir,
        runner="http",
        http_endpoints=[
            "qwen3.6-35b-a3b=http://user:SUPERSECRET@127.0.0.1:1/v1/chat/completions?api_key=TOPSECRET#frag",
        ],
        write_receipt=False,
        ledger_file=swm_home / "ledger.jsonl",
    )
    assert http_fail["panel"]["independent_panel"] is False
    assert http_fail["panel"]["failed_reviewer_count"] == 1
    serialized_fail = json.dumps(http_fail)
    assert "SUPERSECRET" not in serialized_fail
    assert "TOPSECRET" not in serialized_fail
    assert "api_key=" not in serialized_fail

    # 3. Bold-dash & category-tag markdown prose parsing
    bold_parsed = parse_reviewer_output(
        "amazon-bedrock/global.openai.gpt-6-astra",
        (
            "**P1 — Null guard missing in parser** in `src/engine.py:2`\n"
            "**P1 (security)**: Token logged in telemetry helper `src/engine.py:4`\n"
            "**P1 is the priority tier described in docs**\n"
            "Disposition: REQUEST CHANGES\n"
        ),
    )
    assert len(bold_parsed["findings"]) == 2
    assert bold_parsed["findings"][0]["severity"] == "P1"
    assert "Null guard missing" in bold_parsed["findings"][0]["claim"]
    assert bold_parsed["findings"][1]["file_path"] == "src/engine.py"

    # 4. Untracked file appears in diff packet & diff truncation triggers ELC-COVERAGE-001 on tool-less reviews
    (repo_dir / "src" / "new_helper.py").write_text("def added_untracked() -> int:\n    return 42\n", encoding="utf-8")
    pkt_full = build_elenchus_challenge_packet(repo_dir, max_diff_chars=24000)
    assert "new_helper.py" in pkt_full["prompt"]
    assert pkt_full["bindings"]["diff_truncated"] is False

    pkt_trunc = build_elenchus_challenge_packet(repo_dir, max_diff_chars=60)
    assert pkt_trunc["bindings"]["diff_truncated"] is True
    assert pkt_trunc["bindings"]["coverage_complete"] is False
    trunc_syn = synthesize_elenchus_verdict(
        [
            {"model_id": "amazon-bedrock/global.anthropic.claude-fable-5-1", "verdict": "APPROVE", "findings": []},
            {"model_id": "amazon-bedrock/global.openai.gpt-6-astra", "verdict": "APPROVE", "findings": []},
        ],
        pkt_trunc,
        target_dir=repo_dir,
        execution_mode="explicit",
        review_binding_verified=True,
    )
    assert any(c["cluster_id"] == "ELC-COVERAGE-001" for c in trunc_syn["clusters"])
    assert trunc_syn["seal_status"] == "PENDING_ADJUDICATION"

    # 5. Imported --reviews binding verification
    unanimous = [
        {"model_id": "amazon-bedrock/global.anthropic.claude-fable-5-1", "verdict": "APPROVE", "findings": []},
        {"model_id": "amazon-bedrock/global.openai.gpt-6-astra", "verdict": "APPROVE", "findings": []},
    ]
    unbound_file = tmp_path / "unbound_reviews.json"
    unbound_file.write_text(json.dumps(unanimous), encoding="utf-8")
    unbound_rec = run_elenchus_challenge(
        repo_dir,
        reviews_file=unbound_file,
        ledger_file=swm_home / "ledger.jsonl",
    )
    assert unbound_rec["review_binding_verified"] is False
    assert unbound_rec["seal_status"] == "UNBOUND_IMPORT_ADVISORY"
    assert evaluate_elenchus_gate(repo_dir)["reason_code"] == "UNBOUND_REVIEW_IMPORT"

    mismatched_file = tmp_path / "mismatched_reviews.json"
    mismatched_file.write_text(
        json.dumps({"bindings": {"packet_sha256": "0" * 64, "worktree_sha": "deadbeef"}, "reviews": unanimous}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="bound to a different"):
        run_elenchus_challenge(repo_dir, reviews_file=mismatched_file, ledger_file=swm_home / "ledger.jsonl")

    bound_file = tmp_path / "bound_reviews.json"
    bound_file.write_text(
        json.dumps({"bindings": pkt_full["bindings"], "reviews": unanimous}),
        encoding="utf-8",
    )
    bound_rec = run_elenchus_challenge(repo_dir, reviews_file=bound_file, ledger_file=swm_home / "ledger.jsonl")
    assert bound_rec["review_binding_verified"] is True
    assert bound_rec["seal_status"] == "READY_UNANIMOUS"
    assert evaluate_elenchus_gate(repo_dir)["passed"] is True

    # 6. `skb gwi audit --incremental --require-elenchus` & `--subprojects --require-elenchus` fail without receipt
    (repo_dir / "evidence" / "elenchus_challenge.json").unlink()
    clean_repo = tmp_path / "clean-gwi-repo"
    clean_repo.mkdir()
    _init_git_repo(clean_repo)
    fake_gwi = {
        "profile": "app",
        "calculation": {"goldwood_g": 9.60, "status": "PASS", "verdict": "PASS"},
        "tiers": {"S_sub": 9.6, "S_perc": 9.6, "S_act": 9.6, "S_exist": 9.6},
        "craft_lenses": {"coupled_goldwood_g": 9.60, "veto_triggered": False},
        "audit_flags": {"strict_lenses": False, "probe_audio": False, "execute_gate": False},
        "blockers": [],
    }
    record_gwi_audit(clean_repo, fake_gwi)
    assert evaluate_subproject_freshness(clean_repo, ".")["is_fresh_pass"] is True

    with pytest.raises(SystemExit) as exc_inc:
        skb_main(["gwi", "audit", str(clean_repo), "--incremental", "--require-elenchus"])
    assert exc_inc.value.code == 1

    with pytest.raises(SystemExit) as exc_sub:
        skb_main(["gwi", "audit", str(clean_repo), "--subprojects", "--require-elenchus"])
    assert exc_sub.value.code == 1


def test_elenchus_tri_council_v2_hardening_f20(tmp_path: Path, monkeypatch) -> None:
    """F-20 v2 hardening: cluster reconstruction, import revalidation, FAIL goldwood, empty HTTP, auto-http & UTF-8 byte truncation."""
    import io
    import shutil

    import sovereign_kb.elenchus as el_mod

    swm_home = tmp_path / "swm-home"
    monkeypatch.setenv("SWM_HOME", str(swm_home))
    monkeypatch.setattr(audit_registry, "AUDIT_REGISTRY_PATH", swm_home / "audit_registry.json")

    repo_dir = tmp_path / "hardening-repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)
    ledger = swm_home / "ledger.jsonl"

    # 1. Deleting one pending P1 cluster while claiming READY_ADJUDICATED is caught by reconstruction
    two_p1 = [
        {
            "model_id": "amazon-bedrock/global.anthropic.claude-fable-5-1",
            "verdict": "CHALLENGE",
            "findings": [
                {"severity": "P1", "file_path": "src/engine.py", "line": 1, "claim": "First defect in engine"},
                {"severity": "P1", "file_path": "tests/test_engine.py", "line": 1, "claim": "Second defect in test"},
            ],
        },
        {"model_id": "amazon-bedrock/global.openai.gpt-6-astra", "verdict": "APPROVE", "findings": []},
    ]
    rec = run_elenchus_challenge(repo_dir, reviews=two_p1, ledger_file=ledger)
    tampered_del = json.loads(json.dumps(rec))
    tampered_del["clusters"][0]["adjudication"] = {
        "status": "ADJUDICATED",
        "decision": "REJECT_FINDING",
        "adjudicator": "human",
        "reason": "Validated with sufficient engineering rationale",
        "adjudicated_at": "2026-11-17T12:00:00Z",
    }
    tampered_del["clusters"] = tampered_del["clusters"][:1]
    tampered_del["seal_status"] = "READY_ADJUDICATED"
    assert evaluate_elenchus_gate(repo_dir, receipt=tampered_del)["reason_code"] == "CORRUPTED_OR_CONTRADICTORY_RECEIPT"

    # 2. Flipping review_binding_verified on unbound --reviews receipt is still blocked
    unanimous = [
        {"model_id": "amazon-bedrock/global.anthropic.claude-fable-5-1", "verdict": "APPROVE", "findings": []},
        {"model_id": "amazon-bedrock/global.openai.gpt-6-astra", "verdict": "APPROVE", "findings": []},
    ]
    unbound_f = tmp_path / "unbound.json"
    unbound_f.write_text(json.dumps(unanimous), encoding="utf-8")
    rec_unbound = run_elenchus_challenge(repo_dir, reviews_file=unbound_f, ledger_file=ledger)
    flipped = json.loads(json.dumps(rec_unbound))
    flipped["review_binding_verified"] = True
    flipped["seal_status"] = "READY_UNANIMOUS"
    assert evaluate_elenchus_gate(repo_dir, receipt=flipped)["reason_code"] == "UNBOUND_REVIEW_IMPORT"

    # 3. Bound FAIL goldwood.json blocks even when gwi_report=None
    ev_dir = repo_dir / "evidence"
    ev_dir.mkdir(exist_ok=True)
    (ev_dir / "goldwood.json").write_text(
        json.dumps({"project": "hardening-repo", "profile": "app", "status": "FAIL", "compositeScoreG": 1.0}),
        encoding="utf-8",
    )
    run_elenchus_challenge(repo_dir, reviews=unanimous, ledger_file=ledger)
    assert evaluate_elenchus_gate(repo_dir, gwi_report=None)["reason_code"] == "GWI_NOT_PASS"
    (ev_dir / "goldwood.json").unlink()

    # 4. Empty HTTP 200 {"choices": []} is classified FAILED and excluded from N_eff
    monkeypatch.setattr(
        el_mod.urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(b'{"choices": []}'),
    )
    empty_http_rec = run_elenchus_challenge(
        repo_dir,
        runner="http",
        http_endpoints=[
            "amazon-bedrock/global.anthropic.claude-fable-5-1=https://example.com/v1/chat/completions",
            "amazon-bedrock/global.openai.gpt-6-astra=https://example.com/v1/chat/completions",
        ],
        write_receipt=False,
        ledger_file=ledger,
    )
    assert empty_http_rec["panel"]["failed_reviewer_count"] == 2
    assert empty_http_rec["panel"]["n_eff"] == pytest.approx(1.0, abs=1e-4)
    assert empty_http_rec["panel"]["independent_panel"] is False

    # 5. Multi-byte UTF-8 untracked file exceeding 32KB propagates diff_truncated=True AND auto+http fallback preserves ELC-COVERAGE-001
    (repo_dir / "src" / "utf8_big.py").write_text("a = '" + ("α" * 20000) + "'\n", encoding="utf-8")
    pkt_utf8 = build_elenchus_challenge_packet(repo_dir, max_diff_chars=24000)
    assert pkt_utf8["bindings"]["diff_truncated"] is True

    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)
    monkeypatch.setattr(
        el_mod,
        "_query_openai_compat_reviewer",
        lambda mid, *_a, **_k: {"model_id": mid, "verdict": "APPROVE", "execution_status": "OK", "findings": []},
    )
    auto_http = run_elenchus_challenge(
        repo_dir,
        runner="auto",
        http_endpoints=[
            "amazon-bedrock/global.anthropic.claude-fable-5-1=https://example.com/v1",
            "amazon-bedrock/global.openai.gpt-6-astra=https://example.com/v1",
        ],
        ledger_file=ledger,
    )
    assert auto_http["execution_mode"] == "http"
    assert any(c["cluster_id"] == "ELC-COVERAGE-001" for c in auto_http["clusters"])
    assert auto_http["seal_status"] == "PENDING_ADJUDICATION"

    # Deleting ELC-COVERAGE-001 on disk fails reconstruction
    cov_tampered = json.loads(json.dumps(auto_http))
    cov_tampered["clusters"] = []
    cov_tampered["seal_status"] = "READY_UNANIMOUS"
    assert evaluate_elenchus_gate(repo_dir, receipt=cov_tampered)["reason_code"] == "CORRUPTED_OR_CONTRADICTORY_RECEIPT"

    # 6. Hybrid execution mode (OC + HTTP) also triggers ELC-COVERAGE-001 on truncated diffs
    hybrid_syn = synthesize_elenchus_verdict(
        [
            {"model_id": "amazon-bedrock/global.anthropic.claude-fable-5-1", "verdict": "APPROVE", "findings": []},
            {"model_id": "amazon-bedrock/global.openai.gpt-6-astra", "verdict": "APPROVE", "findings": []},
        ],
        pkt_utf8,
        target_dir=repo_dir,
        execution_mode="hybrid",
        review_binding_verified=True,
    )
    assert any(c["cluster_id"] == "ELC-COVERAGE-001" for c in hybrid_syn["clusters"])
    assert hybrid_syn["seal_status"] == "PENDING_ADJUDICATION"

    # 7. Partial import_bindings cardinality bypass fails closed
    clean_rec_repo = tmp_path / "partial-import-repo"
    clean_rec_repo.mkdir()
    _init_git_repo(clean_rec_repo)
    clean_pkt = build_elenchus_challenge_packet(clean_rec_repo)
    partial_reviews = [
        {
            "model_id": "amazon-bedrock/global.anthropic.claude-fable-5-1",
            "verdict": "APPROVE",
            "findings": [],
            "bindings": clean_pkt["bindings"],
        },
        {"model_id": "amazon-bedrock/global.openai.gpt-6-astra", "verdict": "APPROVE", "findings": []},
    ]
    partial_file = tmp_path / "partial_reviews.json"
    partial_file.write_text(json.dumps(partial_reviews), encoding="utf-8")
    partial_rec = run_elenchus_challenge(
        clean_rec_repo, reviews_file=partial_file, write_receipt=False, ledger_file=ledger
    )
    assert partial_rec["review_binding_verified"] is False
    assert partial_rec["seal_status"] == "UNBOUND_IMPORT_ADVISORY"
    tampered_card = json.loads(json.dumps(partial_rec))
    tampered_card["import_bindings"]["item_bindings"] = tampered_card["import_bindings"]["item_bindings"][:1]
    tampered_card["review_binding_verified"] = True
    tampered_card["seal_status"] = "READY_UNANIMOUS"
    assert (
        evaluate_elenchus_gate(clean_rec_repo, receipt=tampered_card)["reason_code"]
        == "CORRUPTED_OR_CONTRADICTORY_RECEIPT"
    )

    # 8. Coverage bypass via deleted or inflated bindings.max_diff_chars fails closed
    def _strip_cov(rec_obj: dict) -> dict:
        cloned = json.loads(json.dumps(rec_obj))
        cloned["bindings"]["diff_truncated"] = False
        cloned["bindings"]["coverage_complete"] = True
        cloned["clusters"] = []
        cloned["metrics"]["total_clusters"] = 0
        cloned["metrics"]["requires_adjudication_count"] = 0
        cloned["metrics"]["incomplete_coverage"] = False
        cloned["seal_status"] = "READY_UNANIMOUS"
        return cloned

    no_budget = _strip_cov(auto_http)
    no_budget["bindings"].pop("max_diff_chars", None)
    assert evaluate_elenchus_gate(repo_dir, receipt=no_budget)["reason_code"] == "CORRUPTED_OR_CONTRADICTORY_RECEIPT"

    inflated_budget = _strip_cov(auto_http)
    inflated_budget["bindings"]["max_diff_chars"] = 1_000_000
    assert (
        evaluate_elenchus_gate(repo_dir, receipt=inflated_budget)["reason_code"] == "CORRUPTED_OR_CONTRADICTORY_RECEIPT"
    )

    small_pkt_rec = run_elenchus_challenge(
        clean_rec_repo,
        reviews=[
            {"model_id": "amazon-bedrock/global.anthropic.claude-fable-5-1", "verdict": "APPROVE", "findings": []},
            {"model_id": "amazon-bedrock/global.openai.gpt-6-astra", "verdict": "APPROVE", "findings": []},
        ],
        write_receipt=False,
        ledger_file=ledger,
    )
    (clean_rec_repo / "src" / "huge.py").write_text("x = 1\n" * 6000, encoding="utf-8")
    trunc_live = run_elenchus_challenge(
        clean_rec_repo,
        reviews=[
            {"model_id": "amazon-bedrock/global.anthropic.claude-fable-5-1", "verdict": "APPROVE", "findings": []},
            {"model_id": "amazon-bedrock/global.openai.gpt-6-astra", "verdict": "APPROVE", "findings": []},
        ],
        write_receipt=False,
        ledger_file=ledger,
    )
    assert trunc_live["bindings"]["diff_truncated"] is True
    inflated_within = _strip_cov(trunc_live)
    inflated_within["bindings"]["max_diff_chars"] = 1_000_000
    full_diff = build_elenchus_challenge_packet(clean_rec_repo, max_diff_chars=1_000_000)["bindings"]
    inflated_within["bindings"]["diff_sha256"] = full_diff["diff_sha256"]
    inflated_within["bindings"]["packet_sha256"] = full_diff["packet_sha256"]
    assert (
        evaluate_elenchus_gate(clean_rec_repo, receipt=inflated_within)["reason_code"]
        == "CORRUPTED_OR_CONTRADICTORY_RECEIPT"
    )
    assert small_pkt_rec["seal_status"] == "READY_UNANIMOUS"

    # 9. Honest truncated -> adjudicated -> PASS flow PLUS execution_mode tamper & post-run goldwood guards
    honest_trunc_dir = tmp_path / "honest-trunc-repo"
    honest_trunc_dir.mkdir()
    _init_git_repo(honest_trunc_dir)
    (honest_trunc_dir / "src" / "large.py").write_text("x = 1\n" * 6000, encoding="utf-8")
    trunc_req = run_elenchus_challenge(
        honest_trunc_dir,
        reviews=[
            {"model_id": "amazon-bedrock/global.anthropic.claude-fable-5-1", "verdict": "APPROVE", "findings": []},
            {"model_id": "amazon-bedrock/global.openai.gpt-6-astra", "verdict": "APPROVE", "findings": []},
        ],
        ledger_file=ledger,
    )
    assert trunc_req["seal_status"] == "PENDING_ADJUDICATION"
    assert [c["cluster_id"] for c in trunc_req["clusters"]] == ["ELC-COVERAGE-001"]

    for fake_mode in ("oc_triad", "offline"):
        mode_tampered = _strip_cov(trunc_req)
        mode_tampered["execution_mode"] = fake_mode
        assert (
            evaluate_elenchus_gate(honest_trunc_dir, receipt=mode_tampered)["reason_code"]
            == "CORRUPTED_OR_CONTRADICTORY_RECEIPT"
        )

    # Pinned git diff formatting makes diff.noprefix=true non-destructive across machines
    subprocess.run(["git", "-C", str(honest_trunc_dir), "config", "diff.noprefix", "true"], check=True)
    adj_cov = adjudicate_elenchus_finding(
        honest_trunc_dir,
        finding_id="ELC-COVERAGE-001",
        decision="reject",
        reason="Repetitive table generated file reviewed directly; logic resides in engine.py",
        ledger_file=ledger,
    )
    assert adj_cov["seal_status"] == "READY_ADJUDICATED"
    assert evaluate_elenchus_gate(honest_trunc_dir)["passed"] is True

    # Appearing goldwood.json after challenge invalidates cleanly instead of skipping packet checks
    (honest_trunc_dir / "evidence" / "goldwood.json").write_text(
        json.dumps({"project": "honest-trunc-repo", "profile": "app", "status": "PASS", "compositeScoreG": 9.6}),
        encoding="utf-8",
    )
    assert evaluate_elenchus_gate(honest_trunc_dir)["reason_code"] == "STALE_GWI_RECEIPT"


def test_elenchus_round4_adversarial_probes_f20(tmp_path: Path, monkeypatch) -> None:
    """
    F-20 Round-4 adversarial regression suite:
      1. Gitlink/submodule mutations invalidate worktree_sha & appear in packet diff.
      2. `.gitattributes` `-diff` on UTF-8 source synthesizes complete diff; binary files emit ELC-COVERAGE-001.
      3. Failed `git diff` exit marks diff_truncated=True / coverage_complete=False + ELC-COVERAGE-001.
      4. `oc triad` TimeoutExpired preserves partial VETO findings + explicit FAILED reviewer record.
      5. Volatile `generatedAt` / `gate_receipt.json` timestamps survive audit -> elenchus -> re-audit -> gate.
      6. Contradictory `status="PASS"` with `goldwood_g=1.0` fails closed as CORRUPTED_OR_CONTRADICTORY_RECEIPT.
      7. Echoed HTTP endpoint API keys in error payloads are scrubbed from receipts.
      8. Malformed `elenchus_challenge.json` surfaces `CORRUPTED` in non-enforced `.evr`; mid-sentence APPROVED parses as CHALLENGE.
    """
    from sovereign_kb import cli as kb_cli
    from sovereign_kb import elenchus as el_mod

    swm_home = tmp_path / "swm-r4"
    monkeypatch.setenv("SWM_HOME", str(swm_home))
    monkeypatch.setattr(audit_registry, "AUDIT_REGISTRY_PATH", swm_home / "audit_registry.json")
    ledger = swm_home / "ledger.jsonl"

    approvals = [
        {"model_id": "amazon-bedrock/global.anthropic.claude-fable-5-1", "verdict": "APPROVE", "findings": []},
        {"model_id": "amazon-bedrock/global.openai.gpt-6-astra", "verdict": "APPROVE", "findings": []},
    ]

    # 1. Nested gitlink / submodule content binding
    sub_root = tmp_path / "sub-parent"
    sub_root.mkdir()
    _init_git_repo(sub_root)
    dep = sub_root / "dep"
    dep.mkdir()
    _init_git_repo(dep)
    subprocess.run(["git", "-C", str(sub_root), "add", "dep"], check=True)
    subprocess.run(
        ["git", "-C", str(sub_root), "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-qm", "gitlink"],
        check=True,
    )
    (dep / "src" / "engine.py").write_text("def operation(): return 'reviewed dirty version'\n", encoding="utf-8")
    pkt_sub = build_elenchus_challenge_packet(sub_root)
    assert "reviewed dirty version" in pkt_sub["prompt"]
    run_elenchus_challenge(sub_root, reviews=approvals, ledger_file=ledger)
    assert evaluate_elenchus_gate(sub_root)["passed"] is True
    (dep / "src" / "engine.py").write_text("def operation(): raise RuntimeError('unreviewed')\n", encoding="utf-8")
    assert evaluate_elenchus_gate(sub_root)["reason_code"] == "STALE_WORKTREE"

    # 2. `.gitattributes -diff` on UTF-8 source vs genuine binary untracked file
    op_root = tmp_path / "opaque-diff"
    op_root.mkdir()
    _init_git_repo(op_root)
    (op_root / ".gitattributes").write_text("src/engine.py -diff\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(op_root), "add", ".gitattributes"], check=True)
    subprocess.run(
        ["git", "-C", str(op_root), "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-qm", "attr"],
        check=True,
    )
    (op_root / "src" / "engine.py").write_text("def unsafe(): return 'HIDDEN_SOURCE_CANARY'\n", encoding="utf-8")
    pkt_op = build_elenchus_challenge_packet(op_root)
    assert "HIDDEN_SOURCE_CANARY" in pkt_op["prompt"]
    assert pkt_op["bindings"]["coverage_complete"] is True
    (op_root / "blob.bin").write_bytes(b"\x00\x01\x02BINARY_CANARY\xff\xfe")
    rec_bin = run_elenchus_challenge(op_root, reviews=approvals, ledger_file=ledger)
    assert rec_bin["bindings"]["diff_truncated"] is True
    assert rec_bin["bindings"]["coverage_complete"] is False
    assert any(c["cluster_id"] == "ELC-COVERAGE-001" for c in rec_bin["clusters"])

    # 3. Non-zero `git diff` return code forces diff_truncated=True / ELC-COVERAGE-001
    fail_diff_root = tmp_path / "fail-diff"
    fail_diff_root.mkdir()
    _init_git_repo(fail_diff_root)
    (fail_diff_root / "src" / "engine.py").write_text("def safe_ratio(a, b):\n    return a / b\n", encoding="utf-8")
    real_git = el_mod._run_git_cmd

    def _fail_diff_git(root: Path, *args: str, timeout: float = 5.0):
        if "diff" in args and "--numstat" not in args:
            return 128, ""
        return real_git(root, *args, timeout=timeout)

    monkeypatch.setattr(el_mod, "_run_git_cmd", _fail_diff_git)
    rec_fd = run_elenchus_challenge(fail_diff_root, reviews=approvals, ledger_file=ledger)
    assert rec_fd["bindings"]["diff_truncated"] is True
    assert rec_fd["bindings"]["coverage_complete"] is False
    assert any(c["cluster_id"] == "ELC-COVERAGE-001" for c in rec_fd["clusters"])
    monkeypatch.setattr(el_mod, "_run_git_cmd", real_git)

    # 4. `oc triad` TimeoutExpired preserves partial VETO + FAILED council reviewer record
    oc_root = tmp_path / "oc-timeout"
    oc_root.mkdir()
    _init_git_repo(oc_root)
    real_sub_run = el_mod.subprocess.run

    def _timeout_oc_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and len(cmd) > 1 and cmd[1] == "triad":
            raise subprocess.TimeoutExpired(
                cmd,
                240,
                output=(
                    "=== CLAUDE FABLE 5.1 (exit=0) ===\n"
                    '{"verdict": "VETO", "findings": [{"severity": "P1", "file_path": "src/engine.py", "line": 1, '
                    '"claim": "Retained council security defect", "evidence": "src/engine.py:1"}]}\n'
                ),
            )
        return real_sub_run(cmd, *args, **kwargs)

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(el_mod.shutil, "which", lambda name: "/bin/echo" if name == "oc" else None)
    monkeypatch.setattr(el_mod.subprocess, "run", _timeout_oc_run)
    monkeypatch.setattr(
        el_mod,
        "_query_openai_compat_reviewer",
        lambda mid, url, prompt, timeout=90.0: {
            "model_id": mid,
            "lineage": classify_model_lineage(mid),
            "verdict": "APPROVE",
            "execution_status": "OK",
            "summary": "Approved",
            "findings": [],
        },
    )
    rec_to = run_elenchus_challenge(
        oc_root,
        runner="auto",
        http_endpoints=["qwen3.6-35b=https://example.com/v1", "gemini-3-pro=https://example.com/v1"],
        ledger_file=ledger,
    )
    assert rec_to["execution_mode"] == "hybrid"
    assert rec_to["panel"]["failed_reviewer_count"] >= 1
    assert "Retained council security defect" in json.dumps(rec_to)
    assert evaluate_elenchus_gate(oc_root)["passed"] is False
    monkeypatch.setattr(el_mod.subprocess, "run", real_sub_run)
    monkeypatch.undo()
    monkeypatch.setenv("SWM_HOME", str(swm_home))
    monkeypatch.setattr(audit_registry, "AUDIT_REGISTRY_PATH", swm_home / "audit_registry.json")

    # 5. Volatile generatedAt & gate_receipt timestamps survive audit -> elenchus -> re-audit -> gate
    reaudit_root = tmp_path / "reaudit-flow"
    reaudit_root.mkdir()
    _init_git_repo(reaudit_root)
    (reaudit_root / ".gitignore").write_text("evidence/\n__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    (reaudit_root / "README.md").write_text(
        "# Reaudit Flow Demo App\n\n"
        "A deterministic verification harness for testing run-bound quality gates, persistent receipts, "
        "and cross-lineage adversarial challenge workflows without external dependencies.\n\n"
        "## Architecture & Verification\n\n"
        "All business logic lives under `src/engine.py` with behavioral assertions in `tests/test_engine.py`, "
        "enforced deterministically via `scripts/quality_gate.sh` and sealed into `.evr` evidence capsules.\n",
        encoding="utf-8",
    )
    (reaudit_root / "pyproject.toml").write_text(
        '[project]\nname = "reaudit-flow"\nversion = "0.1.0"\nrequires-python = ">=3.10"\ndependencies = []\n',
        encoding="utf-8",
    )
    (reaudit_root / "scripts").mkdir(exist_ok=True)
    qg = reaudit_root / "scripts" / "quality_gate.sh"
    qg.write_text(
        f'#!/bin/bash\nset -euo pipefail\n"{sys.executable}" -m pytest -q tests/test_engine.py\n', encoding="utf-8"
    )
    qg.chmod(0o755)
    subprocess.run(["git", "-C", str(reaudit_root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(reaudit_root), "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-qm", "full"],
        check=True,
    )
    kb_cli.main(["gwi", "audit", str(reaudit_root), "--execute-gate", "--write-evidence"])
    run_elenchus_challenge(reaudit_root, reviews=approvals, ledger_file=ledger)
    # Expire gate_receipt cache (>600s) and verify re-audit + --require-elenchus still succeeds cleanly
    gr_p = reaudit_root / "evidence" / "gate_receipt.json"
    gr_data = json.loads(gr_p.read_text(encoding="utf-8"))
    gr_data["timestamp_epoch"] = 0
    gr_p.write_text(json.dumps(gr_data, indent=2) + "\n", encoding="utf-8")
    kb_cli.main(["gwi", "audit", str(reaudit_root), "--execute-gate", "--write-evidence", "--require-elenchus"])
    assert evaluate_elenchus_gate(reaudit_root)["passed"] is True

    # 6. Contradictory status="PASS" with goldwood_g=1.0 fails closed
    bad_g_root = tmp_path / "bad-score"
    bad_g_root.mkdir()
    _init_git_repo(bad_g_root)
    (bad_g_root / "evidence").mkdir()
    bad_rep = {"project_name": "bad-score", "profile": "app", "calculation": {"goldwood_g": 1.0, "status": "PASS"}}
    (bad_g_root / "evidence" / "goldwood.json").write_text(json.dumps(bad_rep), encoding="utf-8")
    run_elenchus_challenge(bad_g_root, reviews=approvals, ledger_file=ledger)
    assert evaluate_elenchus_gate(bad_g_root, gwi_report=bad_rep)["reason_code"] == "CORRUPTED_OR_CONTRADICTORY_RECEIPT"
    with pytest.raises(ValueError, match="CORRUPTED_OR_CONTRADICTORY_RECEIPT"):
        seal_gwi_audit_capsule(
            bad_g_root,
            output_path=bad_g_root / "evidence" / "blocked.evr",
            report=bad_rep,
            require_elenchus=True,
            anchor_pistis=False,
        )

    # 7. Echoed credential outside URL string is scrubbed from diagnostics
    cred_root = tmp_path / "cred-scrub"
    cred_root.mkdir()
    _init_git_repo(cred_root)
    with monkeypatch.context() as mp:
        mp.setattr(
            el_mod.urllib.request,
            "urlopen",
            lambda req, timeout=90.0: io.BytesIO(
                b'{"error": {"message": "Incorrect API key provided: CREDENTIAL_CANARY"}}'
            ),
        )
        rev_cred = el_mod._query_openai_compat_reviewer(
            "gpt-6-astra", "https://example.com/v1?api_key=CREDENTIAL_CANARY", "review"
        )
    rec_cred = run_elenchus_challenge(cred_root, reviews=[rev_cred, approvals[0]], ledger_file=ledger)
    assert "CREDENTIAL_CANARY" not in json.dumps(rec_cred)
    assert "CREDENTIAL_CANARY" not in (cred_root / "evidence" / "elenchus_challenge.json").read_text(encoding="utf-8")

    # 8. Malformed elenchus_challenge.json surfaces CORRUPTED in non-enforced .evr & mid-sentence APPROVED stays CHALLENGE
    corrupt_root = tmp_path / "corrupt-non-enforced"
    corrupt_root.mkdir()
    _init_git_repo(corrupt_root)
    (corrupt_root / "evidence").mkdir()
    (corrupt_root / "evidence" / "elenchus_challenge.json").write_text("{malformed-json", encoding="utf-8")
    good_rep = {
        "project_name": "corrupt-non-enforced",
        "profile": "app",
        "calculation": {"goldwood_g": 9.64, "status": "PASS", "barrier_product": 1.0, "snrc": 20.0},
        "tiers": {"S_sub": 9.5, "S_perc": 9.6, "S_act": 9.8, "S_exist": 9.6},
        "craft_lenses": {"coupled_goldwood_g": 9.64, "lenses": [{"id": "principal-verification", "score": 9.6}]},
    }
    sealed_c = seal_gwi_audit_capsule(
        corrupt_root,
        output_path=corrupt_root / "evidence" / "c.evr",
        report=good_rep,
        require_elenchus=False,
        anchor_pistis=False,
    )
    assert sealed_c["elenchus"]["seal_status"] == "CORRUPTED"
    assert sealed_c["elenchus"]["gate_passed"] is False
    echo_parsed = parse_reviewer_output(
        "amazon-bedrock/global.openai.gpt-6-astra",
        "Prompt asked to state APPROVED if 0 blockers remain, but evaluation was skipped.",
    )
    assert echo_parsed["verdict"] == "CHALLENGE"


def test_elenchus_round5_closure_f20(tmp_path: Path, monkeypatch) -> None:
    """
    F-20 Round-5 closure regression suite:
      1. Strict GWI audit + `--require-elenchus` seal leaves lock-less `pyproject.toml` repos read-only (no `uv.lock`).
      2. Basic auth b64 tokens, bare `token=...` stderr, and echoed secrets in HTTP 200 choices are scrubbed.
      3. Quoted/conditional prose approvals parse as CHALLENGE; standalone/bold Disposition lines parse as APPROVE.
      4. Git `textconv` drivers are bypassed (`--no-textconv`) so source changes appear verbatim in packets.
      5. Filenames with trailing spaces preserve exact paths in `_compute_worktree_content_sha`.
      6. Non-numeric `panel.n_eff` surfaces `CORRUPTED` in non-enforced `.evr` sealing without raising `ValueError`.
    """
    import base64

    from sovereign_kb import cli as kb_cli
    from sovereign_kb import elenchus as el_mod

    swm_home = tmp_path / "swm-r5"
    monkeypatch.setenv("SWM_HOME", str(swm_home))
    monkeypatch.setattr(audit_registry, "AUDIT_REGISTRY_PATH", swm_home / "audit_registry.json")
    ledger = swm_home / "ledger.jsonl"

    approvals = [
        {"model_id": "amazon-bedrock/global.anthropic.claude-fable-5-1", "verdict": "APPROVE", "findings": []},
        {"model_id": "amazon-bedrock/global.openai.gpt-6-astra", "verdict": "APPROVE", "findings": []},
    ]

    # 1. Lock-less pyproject.toml repo stays read-only across strict audit -> elenchus -> evr seal-gwi --require-elenchus
    lockless = tmp_path / "lockless-app"
    lockless.mkdir()
    _init_git_repo(lockless)
    (lockless / ".gitignore").write_text("evidence/\n__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    (lockless / "README.md").write_text(
        "# Lockless App\n\nMinimal deterministic Python package verifying read-only strict GWI audit "
        "followed by cross-lineage Elenchus challenge and `--require-elenchus` `.evr` capsule sealing "
        "when neither `.venv` nor `uv.lock` is committed.\n",
        encoding="utf-8",
    )
    (lockless / "AGENTS.md").write_text(
        "# AGENTS.md\n\n## Feature Registry\n- `FEAT-01`: Safe ratio (`src/engine.py`)\n",
        encoding="utf-8",
    )
    (lockless / "pyproject.toml").write_text(
        '[project]\nname = "lockless-app"\nversion = "0.1.0"\nrequires-python = ">=3.10"\ndependencies = []\n',
        encoding="utf-8",
    )
    (lockless / "scripts").mkdir(exist_ok=True)
    qg = lockless / "scripts" / "quality_gate.sh"
    qg.write_text(
        f'#!/bin/bash\nset -euo pipefail\n"{sys.executable}" -m pytest -q tests/test_engine.py\n', encoding="utf-8"
    )
    qg.chmod(0o755)
    subprocess.run(["git", "-C", str(lockless), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(lockless), "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-qm", "lockless"],
        check=True,
    )
    assert not (lockless / "uv.lock").exists()
    kb_cli.main(["gwi", "audit", str(lockless), "--strict", "--strict-lenses", "--execute-gate", "--write-evidence"])
    assert not (lockless / "uv.lock").exists()
    run_elenchus_challenge(lockless, reviews=approvals, ledger_file=ledger)
    sealed_ll = seal_gwi_audit_capsule(lockless, require_elenchus=True, anchor_pistis=False)
    assert sealed_ll["verification"]["valid"] is True
    assert sealed_ll["elenchus"]["gate_passed"] is True
    assert not (lockless / "uv.lock").exists()
    st_ll = subprocess.run(
        ["git", "-C", str(lockless), "status", "--porcelain"], capture_output=True, text=True, check=True
    )
    assert st_ll.stdout.strip() == ""

    # 2. Basic auth b64 token, bare token= in oc triad stderr, and HTTP 200 echoed API key are all scrubbed
    tok_b64 = base64.b64encode(b"probeuser:PROBE_PASSWORD_XYZ").decode("ascii")
    with monkeypatch.context() as mp:
        mp.setattr(
            el_mod.urllib.request,
            "urlopen",
            lambda req, timeout=90.0: io.BytesIO(
                json.dumps({"error": {"message": f"Rejected Authorization: Basic {tok_b64}"}}).encode("utf-8")
            ),
        )
        rev_basic = el_mod._query_openai_compat_reviewer(
            "gpt-6-astra", "https://probeuser:PROBE_PASSWORD_XYZ@example.com/v1", "review"
        )
    assert tok_b64 not in json.dumps(rev_basic)
    assert "PROBE_PASSWORD_XYZ" not in json.dumps(rev_basic)

    with monkeypatch.context() as mp:
        mp.setattr(
            el_mod.urllib.request,
            "urlopen",
            lambda req, timeout=90.0: io.BytesIO(
                json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        {
                                            "verdict": "CHALLENGE",
                                            "summary": "Server reports key PROBE_PASSWORD_XYZ",
                                            "findings": [],
                                        }
                                    )
                                }
                            }
                        ]
                    }
                ).encode("utf-8")
            ),
        )
        rev_ok_echo = el_mod._query_openai_compat_reviewer(
            "gpt-6-astra", "https://example.com/v1?api_key=PROBE_PASSWORD_XYZ", "review"
        )
    assert "PROBE_PASSWORD_XYZ" not in json.dumps(rev_ok_echo)

    oc_err_repo = tmp_path / "oc-stderr-scrub"
    oc_err_repo.mkdir()
    _init_git_repo(oc_err_repo)
    real_sub = el_mod.subprocess.run

    def _stderr_tok_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and len(cmd) > 1 and cmd[1] == "triad":
            return subprocess.CompletedProcess(cmd, 3, stdout="", stderr="astra: rate limit token=SECRETTOK9")
        return real_sub(cmd, *args, **kwargs)

    with monkeypatch.context() as mp:
        mp.delenv("PYTEST_CURRENT_TEST", raising=False)
        mp.setattr(el_mod.shutil, "which", lambda name: "/bin/echo" if name == "oc" else None)
        mp.setattr(el_mod.subprocess, "run", _stderr_tok_run)
        rec_tok = run_elenchus_challenge(oc_err_repo, runner="oc", ledger_file=ledger)
    assert "SECRETTOK9" not in json.dumps(rec_tok)

    # 3. Quoted / conditional prose approvals fail closed as CHALLENGE; genuine Disposition lines parse as APPROVE
    for bad_prose in (
        "The prior review said Verdict: APPROVED, but this review is incomplete.",
        "Expected result: APPROVED if tests pass; tests were never run.",
        "Final: APPROVED",
    ):
        assert parse_reviewer_output("gpt-6-astra", bad_prose)["verdict"] == "CHALLENGE", bad_prose
    for good_prose in (
        "Disposition: APPROVED",
        "**Disposition**: APPROVED",
        "Verdict — APPROVED",
        "# APPROVED",
        "RESULT = **APPROVED**",
    ):
        assert parse_reviewer_output("gpt-6-astra", good_prose)["verdict"] == "APPROVE", good_prose

    # 4. Git `textconv` driver is bypassed via `--no-textconv`
    tc_repo = tmp_path / "textconv-repo"
    tc_repo.mkdir()
    _init_git_repo(tc_repo)
    conv = tmp_path / "const_conv.sh"
    conv.write_text('#!/bin/sh\nprintf "redacted by textconv\\n"\n', encoding="utf-8")
    conv.chmod(0o755)
    (tc_repo / ".gitattributes").write_text("src/engine.py diff=redact\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tc_repo), "config", "diff.redact.textconv", str(conv)], check=True)
    subprocess.run(["git", "-C", str(tc_repo), "add", ".gitattributes"], check=True)
    subprocess.run(
        ["git", "-C", str(tc_repo), "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-qm", "textconv"],
        check=True,
    )
    (tc_repo / "src" / "engine.py").write_text("UNREVIEWED_TEXTCONV_CANARY = 1\n", encoding="utf-8")
    pkt_tc = build_elenchus_challenge_packet(tc_repo)
    assert "UNREVIEWED_TEXTCONV_CANARY" in pkt_tc["prompt"]
    assert "redacted by textconv" not in pkt_tc["prompt"]

    # 5. Trailing spaces in tracked filenames remain distinct in _compute_worktree_content_sha
    sp_repo = tmp_path / "space-filename"
    sp_repo.mkdir()
    _init_git_repo(sp_repo)
    spaced_f = sp_repo / "src" / "engine space.py "
    spaced_f.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(sp_repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(sp_repo), "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-qm", "spaced"],
        check=True,
    )
    sha_before = _compute_worktree_content_sha(sp_repo)
    spaced_f.write_text("VALUE = 2\n", encoding="utf-8")
    assert _compute_worktree_content_sha(sp_repo) != sha_before

    # 6. Non-numeric panel.n_eff fails closed in gate and surfaces CORRUPTED in optional .evr seal
    bad_neff_repo = tmp_path / "bad-neff-json"
    bad_neff_repo.mkdir()
    _init_git_repo(bad_neff_repo)
    (bad_neff_repo / "evidence").mkdir()
    (bad_neff_repo / "evidence" / "elenchus_challenge.json").write_text(
        json.dumps({"bindings": {}, "panel": {"n_eff": "invalid"}}),
        encoding="utf-8",
    )
    good_rep = {
        "project_name": "bad-neff-json",
        "profile": "app",
        "calculation": {"goldwood_g": 9.64, "status": "PASS", "barrier_product": 1.0, "snrc": 20.0},
        "tiers": {"S_sub": 9.5, "S_perc": 9.6, "S_act": 9.8, "S_exist": 9.6},
        "craft_lenses": {"coupled_goldwood_g": 9.64, "lenses": [{"id": "principal-verification", "score": 9.6}]},
    }
    sealed_bad = seal_gwi_audit_capsule(
        bad_neff_repo,
        output_path=bad_neff_repo / "evidence" / "opt.evr",
        report=good_rep,
        require_elenchus=False,
        anchor_pistis=False,
    )
    assert sealed_bad["elenchus"]["seal_status"] == "CORRUPTED"
    assert sealed_bad["elenchus"]["gate_passed"] is False
    assert sealed_bad["elenchus"]["n_eff"] == 0.0


def test_elenchus_round6_adversarial_closure_f20(tmp_path: Path, monkeypatch) -> None:
    """
    Round-6 adversarial regressions:
      1. Fenced code blocks, blockquotes, unpunctuated/conditional tails, and non-benign trailing lines reject APPROVED.
      2. JSON-quoted secrets + findings[].file_path/symbol/raw_text/model_id + HTTP FAILED responses scrub secrets.
      3. POSIX filenames with trailing newlines or backslashes are hashed and tracked cleanly (STALE_WORKTREE detection).
      4. 10**400 / inf OverflowError and malformed raw_reviews[].findings fail closed as CORRUPTED without crashing optional .evr.
    """
    import http.server
    import json
    import os
    import subprocess
    import threading

    from sovereign_kb.elenchus import (
        _compute_gwi_semantic_sha256,
        _compute_worktree_content_sha,
        _detect_gwi_schema_contradiction,
        _normalize_git_rel_path,
        _query_openai_compat_reviewer,
        _redact_sensitive_text,
        _scrub_review_dict,
        adjudicate_elenchus_finding,
        evaluate_elenchus_gate,
        parse_reviewer_output,
        run_elenchus_challenge,
        synthesize_elenchus_verdict,
    )
    from sovereign_kb.evr import seal_gwi_audit_capsule

    # 1. Fenced, blockquoted, unpunctuated, and trailing non-benign APPROVED prose hardening
    bad_prose_cases = [
        "```markdown\nDisposition: APPROVED\n```\nNeeds follow-up.",
        "~~~\nVerdict: APPROVED\n~~~\nPending review.",
        "```\nDisposition: APPROVED",
        "> Disposition: APPROVED\nWait for regression test.",
        "Disposition: APPROVED once unit tests pass",
        "Disposition: APPROVED for docs only",
        "Disposition: APPROVED — subject to fixing edge case",
        "Disposition: APPROVED (provisional)",
        "Verdict: APPROVED — untested migration",
        "Disposition: APPROVED — partial coverage only",
        "Disposition: APPROVED\nConditional on running database tests.",
        "Disposition: APPROVED\n\nHowever, coverage is incomplete.",
        "Disposition: APPROVED\n\n> Provisional pending audit",
        "Disposition: APPROVED\n1 P0 / 0 P1",
        "Disposition: APPROVED — do not merge",
        "Disposition: APPROVED — requires regression tests",
        "Disposition: APPROVED — 1 P0 / 0 P1",
        "Disposition: APPROVED (docs)",
        "APPROVED-ish",
        'Example:\n```json\n{"verdict": "APPROVE", "findings": []}\n```\nThe actual review is incomplete.',
        'The actual review is incomplete; do not merge. Example output format:\n```json\n{"verdict": "APPROVE", "findings": []}\n```',
        'Example output:\n{"verdict": "APPROVE", "findings": []}',
        'Disposition: APPROVED — requires regression tests\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Disposition: APPROVED — requires regression tests\n{"verdict":"APPROVE","findings":[]}',
        'Example output:\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Example output:\nDisposition: APPROVED\n{"verdict":"APPROVE","findings":[]}',
        "Example output format:\nDisposition: APPROVED",
        'APPROVED once tests pass\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'APPROVED once tests pass\n{"verdict":"APPROVE","findings":[]}',
        '```text\nExample output:\n{"verdict":"APPROVE","findings":[]}\n```',
        '```json\nExample output:\n{"verdict":"APPROVE","findings":[]}\n```',
    ]
    for bad_txt in bad_prose_cases:
        parsed_bad = parse_reviewer_output("gpt-6-astra", bad_txt)
        assert parsed_bad["verdict"] == "CHALLENGE", f"False APPROVE for: {bad_txt!r}"

    p1_with_json_approve = (
        "- [P1] app.py:1 — Authentication bypass permits unauthenticated requests.\n"
        "Disposition: APPROVED\n"
        '```json\n{"verdict":"APPROVE","findings":[]}\n```'
    )
    parsed_p1_conflict = parse_reviewer_output("gpt-6-astra", p1_with_json_approve)
    assert parsed_p1_conflict["verdict"] == "CHALLENGE"
    assert len(parsed_p1_conflict["findings"]) == 1
    assert "Authentication bypass" in parsed_p1_conflict["findings"][0]["claim"]

    json_p1_approve = parse_reviewer_output(
        "gpt-6-astra", '{"verdict":"APPROVE","findings":[{"severity":"P1","claim":"Auth bypass"}]}'
    )
    assert json_p1_approve["verdict"] == "CHALLENGE" and len(json_p1_approve["findings"]) == 1

    for veto_txt in (
        "Verdict: REJECTED\n\nDisposition: APPROVED",
        "Disposition: FAIL\n\nDisposition: APPROVED",
        "Decision: BLOCK\n\nDisposition: APPROVED",
        'My verdict is VETO.\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
    ):
        parsed_veto = parse_reviewer_output("claude-fable-5-1", veto_txt)
        assert parsed_veto["verdict"] == "VETO", f"Contradictory prose parsed as {parsed_veto['verdict']}: {veto_txt!r}"

    embedded_fence_approve = (
        "Disposition: APPROVED\n\n```json\n"
        + json.dumps(
            {
                "verdict": "APPROVE",
                "summary": "All checks verified.",
                "findings": [
                    {
                        "severity": "P2",
                        "category": "correctness",
                        "file_path": "a.py",
                        "line": 1,
                        "claim": 'Note on `My verdict is VETO.\n```json\n{"verdict": "APPROVE"}\n```` prose',
                        "evidence": "a.py:1",
                    }
                ],
            }
        )
        + "\n```"
    )
    parsed_emb = parse_reviewer_output("claude-fable-5-1", embedded_fence_approve)
    assert parsed_emb["verdict"] == "APPROVE" and len(parsed_emb["findings"]) == 1

    title_only = parse_reviewer_output(
        "claude-fable-5-1",
        '{"verdict": "CHALLENGE", "findings": [{"severity": "P0", "title": "RCE in handler", "line": 10}]}',
    )
    assert title_only["verdict"] == "CHALLENGE" and len(title_only["findings"]) == 1
    assert title_only["findings"][0]["claim"] == "RCE in handler"

    # Verify unfenced trailing JSON after noisy stdout braces extracts verdict & findings accurately
    noisy_trailing = (
        "$ python3 -c 'print({\"x\": 1})'\n"
        '{"noise": true}\n'
        "Disposition: CHALLENGE\n\n"
        '{"verdict": "CHALLENGE", "summary": "Caught trailing", "findings": '
        '[{"severity": "P1", "category": "correctness", "file_path": "a.py", "line": 3, "claim": "Bug", "evidence": "a.py:3"}]}'
    )
    parsed_noisy = parse_reviewer_output("gpt-6-astra", noisy_trailing)
    assert parsed_noisy["verdict"] == "CHALLENGE"
    assert len(parsed_noisy["findings"]) == 1 and parsed_noisy["findings"][0]["claim"] == "Bug"
    assert _normalize_git_rel_path("/./a\\b\r") == "a\\b\r"

    good_prose_cases = [
        "Disposition: APPROVED\n\n0 P0 / 0 P1\nFindings: None",
        "```text\nExample template\n```\n\nDisposition: APPROVED (0 P0 / 0 P1)",
        "> Quoted example:\n> Disposition: APPROVED\n\nVerdict: APPROVED\nNo blockers detected.",
    ]
    for good_txt in good_prose_cases:
        parsed_good = parse_reviewer_output("gpt-6-astra", good_txt)
        assert parsed_good["verdict"] == "APPROVE", f"False CHALLENGE for: {good_txt!r}"

    # Round-11 cross-boundary conditionals, hedge aliases & CommonMark §4.5 fence rules
    r11_bad_json_cases = [
        'APPROVED. Once tests pass.\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'APPROVED\nOnce tests pass\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Disposition: APPROVED\nProvided the migration is tested.\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'I approve this change. Once tests pass.\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Approval contingent on fixing the parser\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'LGTM once tests pass\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'I don\'t approve until it\'s tested\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
    ]
    for r11_bad in r11_bad_json_cases:
        parsed_r11 = parse_reviewer_output("gpt-6-astra", r11_bad)
        assert parsed_r11["verdict"] == "CHALLENGE", f"False APPROVE for R11 conditional: {r11_bad!r}"

    indented_pseudo_fence = (
        "Review notes:\n\n"
        "    ```text\n\n"
        "[P1] app.py:1 — Authentication bypass permits unauthenticated requests.\n\n"
        "    ```\n\n"
        '```json\n{"verdict":"APPROVE","findings":[]}\n```'
    )
    parsed_indented = parse_reviewer_output("gpt-6-astra", indented_pseudo_fence)
    assert parsed_indented["verdict"] == "CHALLENGE"
    assert len(parsed_indented["findings"]) == 1
    assert "Authentication bypass" in parsed_indented["findings"][0]["claim"]

    backtick_info_fence = (
        "Review notes:\n\n"
        "```text `example`\n"
        "[P1] app.py:1 — Authentication bypass permits unauthenticated requests.\n"
        "```\n\n"
        "Disposition: APPROVED"
    )
    parsed_bt_info = parse_reviewer_output("gpt-6-astra", backtick_info_fence)
    assert parsed_bt_info["verdict"] == "CHALLENGE"
    assert len(parsed_bt_info["findings"]) == 1

    top_disposition_heading = (
        "Disposition: APPROVED\n\n"
        "### Audit Summary\n"
        "All 12 files verified clean.\n\n"
        '```json\n{"verdict":"APPROVE","findings":[]}\n```'
    )
    parsed_top_disp = parse_reviewer_output("gpt-6-astra", top_disposition_heading)
    assert parsed_top_disp["verdict"] == "APPROVE"

    # Round-12 cross-boundary When/However, post-heading caveats, descriptive headings, and byte-0 4-space fences
    for r12_bad in (
        'I approve this change. When tests pass.\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'I approve this change. However, the parser needs fixing.\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'I approve this.\nHowever, the parser is broken.\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Disposition: APPROVED\n\n### Audit Summary\nOnce tests pass.\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'I approve this change.\n\n### Conditions\nTests must pass.\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
    ):
        parsed_r12 = parse_reviewer_output("gpt-6-astra", r12_bad)
        assert parsed_r12["verdict"] == "CHALLENGE", f"False APPROVE for R12 conditional: {r12_bad!r}"

    desc_heading_approve = (
        "### Target 1 — Cross-sentence / cross-line conditional approval\n"
        "All checks verified.\n\n"
        "Disposition: APPROVED\n"
        '```json\n{"verdict":"APPROVE","findings":[]}\n```'
    )
    assert parse_reviewer_output("claude-fable-5-1", desc_heading_approve)["verdict"] == "APPROVE"

    for c_indent in range(4):
        byte0_pseudo = (
            "    ```text\n\n"
            "[P1] app.py:1 — Authentication bypass permits unauthenticated requests.\n\n"
            + (" " * c_indent)
            + "```\n\nDisposition: APPROVED\n\n"
            '```json\n{"verdict":"APPROVE","findings":[]}\n```'
        )
        parsed_b0 = parse_reviewer_output("gpt-6-astra", byte0_pseudo)
        assert parsed_b0["verdict"] == "CHALLENGE", f"closing indent={c_indent} bypassed"
        assert len(parsed_b0["findings"]) == 1, f"closing indent={c_indent} dropped finding"

    # 2. JSON-quoted, escaped JSON, multi-word quoted, ENV-prefixed, vendor token & HTTP FAILED scrubbing
    d0_pw = json.dumps({"password": 'FAKE_R8_PREFIX"FAKE_R8_SUFFIX'})
    d1_pw = json.dumps(d0_pw)[1:-1]
    d2_pw = json.dumps(d1_pw)[1:-1]
    extra_secret_samples = [
        '{"api_key": "supersecret9876", "token": "tok_live_abc1234", "authorization": "Bearer hdr_sec999"}',
        r"{\"api_key\": \"R7_ESCAPED_SECRET_789\"}",
        'password: "correct horse battery staple"',
        r'password: "correct\horse battery staple"',
        r"{\"password\": \"correct\\horse battery staple\"}",
        d1_pw,
        d2_pw,
        "export GITLAB_TOKEN=abcd1234efgh OPENAI_API_KEY=xyzz9876ijkl",
        "tokens: ghp_1234567890abcdef1234 AKIAIOSFODNN7EXAMPLE",
    ]
    scrubbed_all = "\n".join(_redact_sensitive_text(s) for s in extra_secret_samples)
    for leaked in (
        "supersecret9876",
        "tok_live_abc1234",
        "hdr_sec999",
        "R7_ESCAPED_SECRET_789",
        "correct horse",
        "staple",
        "correct\\horse",
        "FAKE_R8_PREFIX",
        "FAKE_R8_SUFFIX",
        "abcd1234efgh",
        "xyzz9876ijkl",
        "ghp_123456",
        "AKIAIOSFODNN",
    ):
        assert leaked not in scrubbed_all

    escaped_fp_finding = parse_reviewer_output(
        "gpt-6-astra",
        json.dumps(
            {
                "verdict": "CHALLENGE",
                "summary": r'Note password: "correct\horse battery staple"',
                "findings": [
                    {
                        "severity": "P1",
                        "category": "security",
                        "file_path": d1_pw,
                        "line": 10**400,
                        "claim": "Escaped secret in path",
                        "evidence": "test",
                    }
                ],
            }
        ),
    )
    esc_dump = json.dumps(escaped_fp_finding)
    for leaked in ("FAKE_R8_PREFIX", "FAKE_R8_SUFFIX", "correct", "horse", "battery", "staple"):
        assert leaked not in esc_dump
    assert escaped_fp_finding["findings"][0]["line"] == 10_000_000

    rev_dirty = {
        "model_id": "custom/model?token=url_tok_777",
        "verdict": "CHALLENGE",
        "summary": 'Leak {"token": "sum_tok_123"}',
        "raw_text": "raw Bearer raw_tok_456",
        "findings": [
            {
                "severity": "P1",
                "category": "security",
                "file_path": "src/app.py?key=fpath_sec_888",
                "line": 12,
                "symbol": "auth_sk_test123456",
                "claim": 'Claim {"password": "pw_secret_999"}',
                "evidence": "https://user_secret_1:pass_secret_2@example.com/hook?token=ev_tok_333",
            }
        ],
    }
    scrubbed_rev = _scrub_review_dict(rev_dirty, extra_secrets=["sk_test123456"])
    serialized_rev = json.dumps(scrubbed_rev)
    for leaked in (
        "url_tok_777",
        "sum_tok_123",
        "raw_tok_456",
        "fpath_sec_888",
        "sk_test123456",
        "pw_secret_999",
        "user_secret_1",
        "pass_secret_2",
        "ev_tok_333",
    ):
        assert leaked not in serialized_rev

    class _ErrHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body_len = int(self.headers.get("Content-Length", "0") or "0")
            req_txt = self.rfile.read(body_len).decode("utf-8", errors="replace")
            if "CHECK_HTTP_200" in req_txt:
                payload = {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "verdict": "CHALLENGE",
                                        "summary": r'password: "correct\horse battery staple"',
                                        "findings": [
                                            {
                                                "severity": "P1",
                                                "category": "security",
                                                "file_path": d1_pw,
                                                "line": 7,
                                                "claim": "Escaped path secret",
                                                "evidence": "line 7",
                                            }
                                        ],
                                    }
                                )
                            }
                        }
                    ]
                }
            else:
                payload = {"error": {"message": f"bad {d1_pw} " + r'password: "correct\horse battery staple"'}}
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ErrHandler)
    thr = threading.Thread(target=srv.serve_forever, daemon=True)
    thr.start()
    try:
        port = srv.server_address[1]
        http_res = _query_openai_compat_reviewer(
            "custom/endpoint?token=mid_leak_654",
            f"http://http_user_secret:http_pass_secret@127.0.0.1:{port}/v1/chat/completions?key=qs_secret_987",
            "Verify worktree",
            timeout=5.0,
        )
        http_ok_res = _query_openai_compat_reviewer(
            "gpt-6-astra",
            f"http://127.0.0.1:{port}/v1/chat/completions",
            "CHECK_HTTP_200",
            timeout=5.0,
        )
    finally:
        srv.shutdown()
        srv.server_close()
        thr.join(timeout=2.0)

    assert http_res["execution_status"] == "FAILED"
    http_dump = json.dumps(http_res) + "\n" + json.dumps(http_ok_res)
    for leaked in (
        "mid_leak_654",
        "http_user_secret",
        "http_pass_secret",
        "qs_secret_987",
        "FAKE_R8_PREFIX",
        "FAKE_R8_SUFFIX",
        "correct",
        "horse",
        "battery",
        "staple",
    ):
        assert leaked not in http_dump

    # Helper to init git repo + valid goldwood report
    def _init_repo(repo_dir: Path, extra_files: dict[str, str] | None = None) -> dict[str, object]:
        repo_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "-C", str(repo_dir), "init"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "ci@example.com"], check=True)
        subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "CI"], check=True)
        (repo_dir / "README.md").write_text("# Sample\n", encoding="utf-8")
        (repo_dir / "app.py").write_text("def ok() -> int:\n    return 1\n", encoding="utf-8")
        if extra_files:
            for rel, content in extra_files.items():
                (repo_dir / rel).write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(repo_dir), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo_dir), "commit", "-m", "init"], check=True, capture_output=True)
        gwi_obj: dict[str, object] = {
            "project": repo_dir.name,
            "project_name": repo_dir.name,
            "status": "PASS",
            "compositeScoreG": 9.5,
            "sovereignGateG": 9.0,
            "tiers": {"S_sub": 9.5, "S_perc": 9.5, "S_act": 9.5, "S_exist": 9.5},
            "calculation": {
                "goldwood_g": 9.5,
                "status": "PASS",
                "psi_values": [1.0, 1.0, 1.0, 1.0],
                "barrier_product": 1.0,
                "snrc": 1.0,
            },
            "craft_lenses": {"coupled_goldwood_g": 9.5, "veto_triggered": False, "lenses": []},
        }
        (repo_dir / "evidence").mkdir(parents=True, exist_ok=True)
        (repo_dir / "evidence" / "goldwood.json").write_text(json.dumps(gwi_obj, indent=2), encoding="utf-8")
        return gwi_obj

    clean_reviews = [
        {"model_id": "claude-fable-5-1", "verdict": "APPROVE", "summary": "Verified clean", "findings": []},
        {"model_id": "gpt-6-astra", "verdict": "APPROVE", "summary": "Verified clean", "findings": []},
    ]

    # 3. POSIX special filenames ('trailing.py\n', 'carriage\r.py', and 'back\\slash.py') preserve STALE_WORKTREE sensitivity
    if os.name != "nt":
        posix_repo = tmp_path / "posix_names"
        gwi_posix = _init_repo(
            posix_repo, {"trailing.py\n": "v = 1\n", "carriage\r.py": "c = 1\n", "back\\slash.py": "w = 1\n"}
        )
        monkeypatch.setenv("SOVEREIGN_LEDGER_FILE", str(tmp_path / "ledger.jsonl"))
        rec_posix = run_elenchus_challenge(
            posix_repo,
            intent="Verify POSIX filename hashing",
            reviews=clean_reviews,
            write_receipt=True,
            ledger_file=tmp_path / "ledger.jsonl",
        )
        assert evaluate_elenchus_gate(posix_repo, gwi_report=gwi_posix)["passed"] is True

        wt0 = _compute_worktree_content_sha(posix_repo)
        (posix_repo / "trailing.py\n").write_text("v = 2\n", encoding="utf-8")
        assert _compute_worktree_content_sha(posix_repo) != wt0
        gate_trail = evaluate_elenchus_gate(posix_repo, receipt=rec_posix, gwi_report=gwi_posix)
        assert gate_trail["passed"] is False
        assert gate_trail["reason_code"] == "STALE_WORKTREE"

        (posix_repo / "trailing.py\n").write_text("v = 1\n", encoding="utf-8")
        assert evaluate_elenchus_gate(posix_repo, receipt=rec_posix, gwi_report=gwi_posix)["passed"] is True

        (posix_repo / "carriage\r.py").write_text("c = 2\n", encoding="utf-8")
        assert _compute_worktree_content_sha(posix_repo) != wt0
        gate_cr = evaluate_elenchus_gate(posix_repo, receipt=rec_posix, gwi_report=gwi_posix)
        assert gate_cr["passed"] is False
        assert gate_cr["reason_code"] == "STALE_WORKTREE"

        (posix_repo / "carriage\r.py").write_text("c = 1\n", encoding="utf-8")
        assert evaluate_elenchus_gate(posix_repo, receipt=rec_posix, gwi_report=gwi_posix)["passed"] is True

        (posix_repo / "back\\slash.py").write_text("w = 2\n", encoding="utf-8")
        assert _compute_worktree_content_sha(posix_repo) != wt0
        gate_bs = evaluate_elenchus_gate(posix_repo, receipt=rec_posix, gwi_report=gwi_posix)
        assert gate_bs["passed"] is False
        assert gate_bs["reason_code"] == "STALE_WORKTREE"

        # Verify clean commit diff collection (`git show -z --name-only`) preserves exact names
        from sovereign_kb.elenchus import _collect_bounded_diff

        subprocess.run(["git", "-C", str(posix_repo), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(posix_repo), "commit", "-m", "update special"], check=True, capture_output=True
        )
        _, touched_clean, _ = _collect_bounded_diff(posix_repo, dirty=False)
        assert "back\\slash.py" in touched_clean

        # Verify -uall expands brand-new untracked subdirectories without ELC-COVERAGE-001 false truncation
        untracked_sub = tmp_path / "untracked_subdir_repo"
        gwi_sub = _init_repo(untracked_sub)
        (untracked_sub / "newpkg").mkdir()
        (untracked_sub / "newpkg" / "mod.py").write_text("VALUE = 42\n", encoding="utf-8")
        sub_rec = run_elenchus_challenge(
            untracked_sub,
            intent="Verify untracked subdir leaf expansion",
            reviews=clean_reviews,
            write_receipt=True,
            ledger_file=tmp_path / "ledger.jsonl",
        )
        assert sub_rec["bindings"]["diff_truncated"] is False
        assert sub_rec["seal_status"] == "READY_UNANIMOUS"
        assert evaluate_elenchus_gate(untracked_sub, receipt=sub_rec, gwi_report=gwi_sub)["passed"] is True

    # 4. OverflowError (10**400 / inf) & malformed nested raw_reviews[].findings fail closed cleanly
    contra = _detect_gwi_schema_contradiction({"status": "PASS", "compositeScoreG": 10**400})
    assert isinstance(contra, str) and "finite" in contra
    sem_digest = _compute_gwi_semantic_sha256(
        {"status": "PASS", "compositeScoreG": 10**400, "tiers": {"S_sub": float("inf")}}
    )
    assert len(sem_digest) == 64

    mal_repo = tmp_path / "malformed_closure"
    gwi_mal = _init_repo(mal_repo)
    monkeypatch.setenv("SOVEREIGN_LEDGER_FILE", str(tmp_path / "ledger.jsonl"))
    base_rec = run_elenchus_challenge(
        mal_repo,
        intent="Verify fail-closed schema",
        reviews=clean_reviews,
        write_receipt=True,
        ledger_file=tmp_path / "ledger.jsonl",
    )
    # Huge reviewer-supplied line=10**400 clamps to 10_000_000 rather than self-corrupting fresh receipt
    big_line_rec = run_elenchus_challenge(
        mal_repo,
        intent="Verify bounded finding line",
        reviews=[
            {"model_id": "claude-fable-5-1", "verdict": "APPROVE", "findings": []},
            {
                "model_id": "gpt-6-astra",
                "verdict": "CHALLENGE",
                "findings": [
                    {
                        "severity": "P2",
                        "category": "correctness",
                        "file_path": "app.py",
                        "line": 10**400,
                        "claim": "Line clamp note",
                        "evidence": "app.py",
                    }
                ],
            },
        ],
        write_receipt=True,
        ledger_file=tmp_path / "ledger.jsonl",
    )
    assert big_line_rec["clusters"][0]["line"] == 10_000_000
    gate_big = evaluate_elenchus_gate(mal_repo, receipt=big_line_rec, gwi_report=gwi_mal)
    assert gate_big["passed"] is False and gate_big["seal_status"] == "PENDING_ADJUDICATION"
    adj_big = adjudicate_elenchus_finding(
        mal_repo,
        big_line_rec["clusters"][0]["cluster_id"],
        "reject",
        reason="Clamped line number verified non-blocking in probe.",
        ledger_file=tmp_path / "ledger.jsonl",
    )
    assert adj_big["seal_status"] == "READY_ADJUDICATED"
    assert evaluate_elenchus_gate(mal_repo, receipt=adj_big, gwi_report=gwi_mal)["passed"] is True
    (mal_repo / "evidence" / "elenchus_challenge.json").write_text(
        json.dumps({**base_rec, "clusters": 123}), encoding="utf-8"
    )
    try:
        adjudicate_elenchus_finding(mal_repo, "ELC-001", "accept", reason="valid rationale")
        raise AssertionError("Expected ValueError on malformed clusters in adjudicate_elenchus_finding")
    except ValueError as exc:
        assert "Malformed" in str(exc)
    (mal_repo / "evidence" / "elenchus_challenge.json").write_text(json.dumps(base_rec, indent=2), encoding="utf-8")

    null_parsed = parse_reviewer_output("gpt-6-astra", '{"verdict": "APPROVE", "findings": null}')
    assert null_parsed["verdict"] == "CHALLENGE" and null_parsed["execution_status"] == "FAILED"

    for bad_find in (None, {"claim": "x"}, 1, ["bad"]):
        try:
            synthesize_elenchus_verdict(
                [{"model_id": "gpt-6-astra", "verdict": "APPROVE", "findings": bad_find}],
                {"target_dir": str(mal_repo), "repo_root": str(mal_repo), "intent": "Verify"},
            )
            raise AssertionError(f"Expected ValueError for findings={bad_find!r}")
        except ValueError:
            pass

    mutated_payloads: list[dict[str, object]] = []
    for mut_fn in (
        lambda d: d["panel"].__setitem__("n_eff", 10**400),
        lambda d: d["panel"].__setitem__("n_eff", float("inf")),
        lambda d: d["bindings"].__setitem__("max_diff_chars", float("inf")),
        lambda d: d["bindings"].__setitem__("max_diff_chars", 10**400),
        lambda d: d["bindings"].__setitem__("max_diff_chars", "24000"),
        lambda d: d["bindings"].__setitem__("max_diff_chars", 24000.5),
        lambda d: d["raw_reviews"][0].__setitem__("findings", None),
        lambda d: d["raw_reviews"][0].__setitem__("findings", {"severity": "P0"}),
        lambda d: d["raw_reviews"][0].__setitem__("findings", 1),
        lambda d: d["raw_reviews"][0].__setitem__("findings", [{"severity": "P1", "line": float("inf"), "claim": "x"}]),
        lambda d: d["raw_reviews"][0].__setitem__("findings", [{"severity": "P1", "line": 10**400, "claim": "x"}]),
        lambda d: d.__setitem__("clusters", 123),
        lambda d: d.__setitem__("metrics", "bad"),
        lambda d: d.__setitem__("review_binding_verified", "false"),
        lambda d: d["panel"].__setitem__("independent_panel", "false"),
        lambda d: d["bindings"].__setitem__("diff_truncated", "false"),
    ):
        cloned = json.loads(json.dumps(base_rec))
        mut_fn(cloned)
        mutated_payloads.append(cloned)

    for idx_m, bad_rec in enumerate(mutated_payloads):
        gate_bad = evaluate_elenchus_gate(mal_repo, receipt=bad_rec, gwi_report=gwi_mal, require_independent=True)
        assert gate_bad["passed"] is False, f"Mutation #{idx_m} unexpectedly passed"
        assert gate_bad["seal_status"] == "CORRUPTED", f"Mutation #{idx_m} -> {gate_bad}"

        # Optional sealing must NEVER crash; must embed CORRUPTED metadata cleanly
        evr_out = tmp_path / f"opt_{idx_m}.evr"
        sealed_opt = seal_gwi_audit_capsule(
            mal_repo,
            report=gwi_mal,
            output_path=evr_out,
            anchor_pistis=False,
            require_elenchus=False,
            elenchus_receipt=bad_rec,
        )
        assert sealed_opt["elenchus"]["seal_status"] == "CORRUPTED"
        assert sealed_opt["elenchus"]["gate_passed"] is False

        # Enforced sealing MUST fail closed with CORRUPTED_OR_CONTRADICTORY_RECEIPT
        try:
            seal_gwi_audit_capsule(
                mal_repo,
                report=gwi_mal,
                output_path=tmp_path / f"req_{idx_m}.evr",
                anchor_pistis=False,
                require_elenchus=True,
                elenchus_receipt=bad_rec,
            )
            raise AssertionError(f"Mutation #{idx_m} unexpectedly passed require_elenchus=True")
        except ValueError as exc:
            assert "CORRUPTED_OR_CONTRADICTORY_RECEIPT" in str(exc)

    # Round-12 malformed gate_receipt.json strict scalar enforcement
    from sovereign_kb.audit_registry import compute_subproject_fingerprint

    fp_mal = compute_subproject_fingerprint(mal_repo, ".")
    wt_mal = _compute_worktree_content_sha(mal_repo)
    for bad_gr_fields in (
        {"passed": "false", "executed": True, "exit_code": 0, "fresh_for_tree": True},
        {"passed": True, "executed": "false", "exit_code": 0, "fresh_for_tree": True},
        {"passed": True, "executed": True, "exit_code": False, "fresh_for_tree": True},
        {"passed": True, "executed": True, "exit_code": 0.0, "fresh_for_tree": True},
        {"passed": True, "executed": True, "exit_code": 0, "fresh_for_tree": "false"},
    ):
        gr_payload = {**bad_gr_fields, "tree_sha": fp_mal["tree_sha"], "worktree_sha": wt_mal}
        (mal_repo / "evidence" / "gate_receipt.json").write_text(json.dumps(gr_payload), encoding="utf-8")
        rec_gr = run_elenchus_challenge(
            mal_repo,
            intent="Verify",
            reviews=clean_reviews,
            write_receipt=False,
        )
        gate_gr = evaluate_elenchus_gate(mal_repo, receipt=rec_gr, gwi_report=gwi_mal, require_independent=True)
        assert gate_gr["passed"] is False, f"Malformed gate_receipt passed: {bad_gr_fields!r}"
        assert gate_gr["reason_code"] == "INVALID_GATE_RECEIPT", f"Unexpected reason: {gate_gr}"
        try:
            seal_gwi_audit_capsule(
                mal_repo,
                report=gwi_mal,
                output_path=tmp_path / "bad_gr.evr",
                anchor_pistis=False,
                require_elenchus=True,
                elenchus_receipt=rec_gr,
            )
            raise AssertionError(f"require_elenchus=True sealed with malformed gate_receipt: {bad_gr_fields!r}")
        except ValueError as exc:
            assert "INVALID_GATE_RECEIPT" in str(exc)

    # Round-13 end-to-end conditional / same-line caveat / colon / heading regressions
    valid_gr = {
        "passed": True,
        "executed": True,
        "exit_code": 0,
        "fresh_for_tree": True,
        "tree_sha": fp_mal["tree_sha"],
        "worktree_sha": wt_mal,
    }
    (mal_repo / "evidence" / "gate_receipt.json").write_text(json.dumps(valid_gr), encoding="utf-8")
    r13_bad_prefixes = [
        'I approve this change. Tests must pass.\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'I approve this change: however, the parser needs fixing.\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### Conditions for approval\nTests must pass.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'I approve this change. Still, the parser is broken.\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### Audit Summary\nThe parser needs fixing.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'I approve this change.\n\n**Conditions:**\nCI green.\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Disposition: APPROVED\n### Notes\nParser flaky.\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### Conditions for approval ###\nCI green.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Conditions for approval\n-----------------------\nCI green.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### Audit Summary\nThe parser must be fixed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### Audit Summary\nWe must fix the parser.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'I approve this change.\n\nCaveat: CI is red.\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '**Caveat:** the migration lacks a rollback.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### The parser must be fixed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### The parser must be fixed. ###\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### We must fix the parser.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### Caveat: CI is red.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### Conditions for approval: CI green.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Disposition: APPROVED\n\n### The parser must be fixed.\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### Audit Summary\nCI is red.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### The parser **must** be fixed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### The parser **must** be fixed. ###\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'The parser **must** be fixed.\n---------------------------\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'The parser\n**must** be fixed.\n---------------------------\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Conditions for\napproval\n--------------\nCI green.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### CI is **red**.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### We must _fix_ the parser.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### The parser [must](#p1) be __fixed__.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### The parser <strong>must</strong> be fixed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### The parser \\*must\\* be fixed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### `CI` is red.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### TODO: fix the parser\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### FIXME: parser\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### Pending: parser fix\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### Known issues\nParser crashes.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### Remaining work\nFix the parser.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### The parser should be fixed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### Fix required before merge\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Disposition: APPROVED (0 P0, 0 P1 — parser is broken)\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '***APPROVED***\n\nParser crashes on empty input.\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### Audit Summary\nThe parser **must**\nbe fixed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### [P1] Parser crash on empty input\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### [**P1**] Parser crash on empty input\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### <strong>[P1]</strong> Parser crash on empty input\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Disposition: APPROVED (0 P0 / 0 P1 — parser crashes on empty input)\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### TODO: 0 P0 / 0 P1 — parser crashes on empty input\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'TODO — fix parser\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Tests are failing.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'One P1 remaining.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### Issues\nParser crash\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### The parser `must` be fixed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '- [P1] No input validation guards empty payloads, causing a crash issue.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '- **P1**: No auth check on endpoint creates a security issue.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '- [P1] Zero-length buffer triggers memory defect.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '- [P1] 0 P0 / 1 P1 blocker in parser.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| P1 | Parser crash on empty input |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'A P1 is still open.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 still open.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| File | Line | Severity | Claim |\n|---|---|---|---|\n| src/app.py | 12 | P1 | Parser crash on empty input |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Claim | Severity |\n|---|---|\n| Parser crash on empty input | P1 |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| P1 | src/app.py | Parser crash on empty input | ✅ Reproduced |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| P1 | src/app.py | 0-byte input causes a crash |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| P1 | src/app.py | Verified by reproduction |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| 1 | P1 | Parser crash on empty input | OK to defer |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| 1 | P1 | Parser crash | Closed-form regression |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Severity | File | Claim | Reproduced |\n|---|---|---|---|\n| P1 | src/app.py | Parser crash | ✅ |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '- [P1]\n  No input validation guards empty payloads, causing a crash issue.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 is unaddressed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'The P1 persists.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 has not been fixed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 isn\'t fixed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '**P1**\nParser crash on empty input in src/app.py:1\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '- **[P1]**\n  No input validation guards empty payloads, causing a crash issue.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### **P1**\n\nParser crash on empty input.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Check | Status |\n|---|---|\n| Lint | PASS |\n\n- [P1] src/app.py:1 — Parser crash on empty input (repro: echo "" | app)\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Severity | File | Symbol | Claim |\n|---|---|---|---|\n| P1 | src/app.py | PASS | Missing auth check allows unauthenticated writes |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Severity | File | Status | Claim |\n|---|---|---|---|\n| P1 | src/app.py | PASS | Missing auth check allows unauthenticated writes |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| P1 — security | Parser crash on empty input |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'File | Severity | Claim\n-|-\nsrc/app.py | P1 | Single-dash delimiter crash\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Finding | Location |\n|---|---|\n| [P1] Parser crash on empty input | src/app.py |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| P1 — Parser crash causing denial of service | src/app.py:12 |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| **P1**: Parser crash on empty input | src/app.py |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| [P1] Single-column table crash |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '**P1 (security boundary violation in auth handler)**\nParser crash on empty input.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '**P1**\n### Parser crash on empty input\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '**P1**\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 is still not fixed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 has yet to be fixed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 was never fixed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 fix is pending.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '**P1**\n\n\n\nParser crash on empty input.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '**P1**\n\n\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### P1\n\n\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Finding | Status |\n|---|---|\n| [P1] Parser crash on empty input | PASS |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Finding | Status |\n|---|---|\n| **P1**: Missing auth check allows unauthenticated writes | PASS |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| P1 (auth) crash on empty input | src/app.py |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 reopened.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 regression remains.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 fix was not applied.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 has not landed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 is partially fixed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### P1\n\n\n\nFindings:\nMissing authentication allows unauthenticated writes.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '**P1**\n### Findings\nMissing authentication allows unauthenticated writes.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### [P1] Findings:\nMissing authentication allows unauthenticated writes.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '```json\n{"verdict":"APPROVE","findings":[{"severity":"P1","file_path":"src/engine.py","evidence":"Unauthenticated writes reproduce without an auth check."}]}\n```',
        '```json\n{"verdict":"APPROVE","findings":[{"severity":"P1"}]}\n```',
        'P1 fix hasn\'t landed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'The P1 fix wasn\'t applied.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 fix was never merged.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 fix hasn\u2019t landed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        "P1 fix hasn\u2019t landed.\n\nDisposition: APPROVED",
        "Disposition: APPROVED \u2014 can\u2019t merge yet",
        'Tests weren\'t run.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        "Tests weren\u2019t run.\n\nDisposition: APPROVED",
        '| Component | P0 | P1 | P2 |\n|---|---|---|---|\n| Parser | 0 | 1 | 0 |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Check | P0/P1 | Status |\n|---|---|---|\n| Parser | 1 | PASS |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Component | P0 | P1 | P2 |\n|---|---|---|---|\n| Parser | 0 | `1` | 0 |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Check | `P0/P1` | Status |\n|---|---|---|\n| Parser | 1 | PASS |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Check | `P0` / `P1` | `Status` |\n|---|---|---|\n| Parser | `1` (open) | `PASS` |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Severity | File | Claim |\n|---|---|---|\n| `P1` | `src/app.py` | Parser crash on empty input |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Component | Open P1 |\n|---|---|\n| Parser | one |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Component | Open P1 |\n|---|---|\n| Parser | 1. |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'The P1 remains blocked by the missing migration.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Pending verification.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Reviewers approve and seal if the owner agrees.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Tests have not been run.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Tests had never been run.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Tests have not yet been run.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 fix has not been applied.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 fix had not been merged.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'The P1 fix has not yet been landed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '0 P0 / 0 P1; however the migration is untested.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
    ]
    for r13_txt in r13_bad_prefixes:
        p13 = parse_reviewer_output("gpt-6-astra", r13_txt)
        assert p13["verdict"] == "CHALLENGE", f"False APPROVE for R13 caveat: {r13_txt!r}"
        rec13 = run_elenchus_challenge(
            mal_repo,
            intent="Verify",
            reviews=[
                {"model_id": "claude-fable-5-1", "verdict": "APPROVE", "summary": "ok", "findings": []},
                p13,
            ],
            write_receipt=False,
        )
        assert rec13["seal_status"] == "PENDING_ADJUDICATION"
        try:
            seal_gwi_audit_capsule(
                mal_repo,
                report=gwi_mal,
                output_path=tmp_path / "r13_blocked.evr",
                anchor_pistis=False,
                require_elenchus=True,
                elenchus_receipt=rec13,
            )
            raise AssertionError(f"require_elenchus=True sealed for R13 caveat: {r13_txt!r}")
        except ValueError as exc:
            assert "PENDING_ADJUDICATION" in str(exc)

    for fmt_p1_txt in (
        '### [**P1**] Parser crash on empty input\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### <strong>[P1]</strong> Parser crash on empty input\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '- [P1] No input validation guards empty payloads, causing a crash issue.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '- **P1**: No auth check on endpoint creates a security issue.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '- [P1] Zero-length buffer triggers memory defect.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '- [P1] 0 P0 / 1 P1 blocker in parser.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| P1 | Parser crash on empty input |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| File | Line | Severity | Claim |\n|---|---|---|---|\n| src/app.py | 12 | P1 | Parser crash on empty input |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Claim | Severity |\n|---|---|\n| Parser crash on empty input | P1 |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| P1 | src/app.py | Parser crash on empty input | ✅ Reproduced |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| P1 | src/app.py | 0-byte input causes a crash |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| P1 | src/app.py | Verified by reproduction |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| 1 | P1 | Parser crash on empty input | OK to defer |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| 1 | P1 | Parser crash | Closed-form regression |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Severity | File | Claim | Reproduced |\n|---|---|---|---|\n| P1 | src/app.py | Parser crash | ✅ |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '- [P1]\n  No input validation guards empty payloads, causing a crash issue.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '**P1**\nParser crash on empty input in src/app.py:1\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '- **[P1]**\n  No input validation guards empty payloads, causing a crash issue.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### **P1**\n\nParser crash on empty input.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Check | Status |\n|---|---|\n| Lint | PASS |\n\n- [P1] src/app.py:1 — Parser crash on empty input (repro: echo "" | app)\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Severity | File | Symbol | Claim |\n|---|---|---|---|\n| P1 | src/app.py | PASS | Missing auth check allows unauthenticated writes |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Severity | File | Status | Claim |\n|---|---|---|---|\n| P1 | src/app.py | PASS | Missing auth check allows unauthenticated writes |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| P1 — security | Parser crash on empty input |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'File | Severity | Claim\n-|-\nsrc/app.py | P1 | Single-dash delimiter crash\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Finding | Location |\n|---|---|\n| [P1] Parser crash on empty input | src/app.py |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| P1 — Parser crash causing denial of service | src/app.py:12 |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| **P1**: Parser crash on empty input | src/app.py |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| [P1] Single-column table crash |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '**P1 (security boundary violation in auth handler)**\nParser crash on empty input.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '**P1**\n### Parser crash on empty input\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '**P1**\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '**P1**\n\n\n\nParser crash on empty input.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '**P1**\n\n\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### P1\n\n\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Finding | Status |\n|---|---|\n| [P1] Parser crash on empty input | PASS |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Finding | Status |\n|---|---|\n| **P1**: Missing auth check allows unauthenticated writes | PASS |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| P1 (auth) crash on empty input | src/app.py |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### P1\n\n\n\nFindings:\nMissing authentication allows unauthenticated writes.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '**P1**\n### Findings\nMissing authentication allows unauthenticated writes.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### [P1] Findings:\nMissing authentication allows unauthenticated writes.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '```json\n{"verdict":"APPROVE","findings":[{"severity":"P1","file_path":"src/engine.py","evidence":"Unauthenticated writes reproduce without an auth check."}]}\n```',
        '```json\n{"verdict":"APPROVE","findings":[{"severity":"P1"}]}\n```',
        '| Component | P0 | P1 | P2 |\n|---|---|---|---|\n| Parser | 0 | 1 | 0 |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Check | P0/P1 | Status |\n|---|---|---|\n| Parser | 1 | PASS |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Component | P0 | P1 | P2 |\n|---|---|---|---|\n| Parser | 0 | `1` | 0 |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Check | `P0/P1` | Status |\n|---|---|---|\n| Parser | 1 | PASS |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Check | `P0` / `P1` | `Status` |\n|---|---|---|\n| Parser | `1` (open) | `PASS` |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Severity | File | Claim |\n|---|---|---|\n| `P1` | `src/app.py` | Parser crash on empty input |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Component | Open P1 |\n|---|---|\n| Parser | one |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Component | Open P1 |\n|---|---|\n| Parser | 1. |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
    ):
        parsed_fmt = parse_reviewer_output("gpt-6-astra", fmt_p1_txt)
        assert parsed_fmt["verdict"] == "CHALLENGE"
        assert len(parsed_fmt["findings"]) == 1 and parsed_fmt["findings"][0]["severity"] == "P1"

    parsed_p0_blanks = parse_reviewer_output(
        "gpt-6-astra",
        '**P0**\n\n\n\nRCE via eval in handler.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
    )
    assert parsed_p0_blanks["verdict"] == "VETO"
    assert len(parsed_p0_blanks["findings"]) == 1 and parsed_p0_blanks["findings"][0]["severity"] == "P0"

    parsed_p0_cnt = parse_reviewer_output(
        "gpt-6-astra",
        '| Component | P0 | P1 | P2 |\n|---|---|---|---|\n| Parser | 1 | 0 | 0 |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
    )
    assert parsed_p0_cnt["verdict"] == "VETO"
    assert len(parsed_p0_cnt["findings"]) == 1 and parsed_p0_cnt["findings"][0]["severity"] == "P0"

    parsed_p0_bt_cnt = parse_reviewer_output(
        "gpt-6-astra",
        '| Component | `P0` | `P1` | `P2` |\n|---|---|---|---|\n| Parser | `1` | `0` | `0` |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
    )
    assert parsed_p0_bt_cnt["verdict"] == "VETO"
    assert len(parsed_p0_bt_cnt["findings"]) == 1 and parsed_p0_bt_cnt["findings"][0]["severity"] == "P0"

    parsed_inline_tbl = parse_reviewer_output(
        "gpt-6-astra",
        '| **P1**: Parser crash on empty input | src/app.py |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
    )
    assert "Parser crash on empty input" in parsed_inline_tbl["findings"][0]["claim"]

    parsed_tbl_loc = parse_reviewer_output(
        "gpt-6-astra",
        '| File | Line | Severity | Claim |\n|---|---|---|---|\n| src/app.py | 12 | P1 | Parser crash on empty input |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
    )
    assert parsed_tbl_loc["findings"][0]["file_path"] == "src/app.py"
    assert parsed_tbl_loc["findings"][0]["line"] == 12

    assert _strip_inline_formatting("snake__case") == "snake__case"

    gwi_lens_clean = json.loads(json.dumps(gwi_mal))
    gwi_lens_clean["craft_lenses"] = {"lenses": [{"veto_triggered": False}]}
    gwi_lens_veto = json.loads(json.dumps(gwi_mal))
    gwi_lens_veto["craft_lenses"] = {"lenses": [{"veto_triggered": True}]}
    assert _compute_gwi_semantic_sha256(gwi_lens_clean) != _compute_gwi_semantic_sha256(gwi_lens_veto)

    gwi_contra = json.loads(json.dumps(gwi_mal))
    gwi_contra["craft_lenses"] = {"coupled_goldwood_g": 9.5, "veto_triggered": False}
    gwi_contra["craftLenses"] = {"coupled_goldwood_g": 9.5, "veto_triggered": True}
    rec_contra_clean = run_elenchus_challenge(
        mal_repo,
        intent="Verify",
        reviews=[
            {"model_id": "claude-fable-5-1", "verdict": "APPROVE", "summary": "ok", "findings": []},
            {"model_id": "gpt-6-astra", "verdict": "APPROVE", "summary": "ok", "findings": []},
        ],
        write_receipt=False,
    )
    gate_contra_rep = evaluate_elenchus_gate(mal_repo, receipt=rec_contra_clean, gwi_report=gwi_contra)
    assert (
        gate_contra_rep["passed"] is False
        and gate_contra_rep["seal_status"] == "CORRUPTED"
        and gate_contra_rep["reason_code"] == "CORRUPTED_OR_CONTRADICTORY_RECEIPT"
    )
    try:
        seal_gwi_audit_capsule(
            mal_repo,
            report=gwi_contra,
            output_path=tmp_path / "contra_rep.evr",
            anchor_pistis=False,
            require_elenchus=True,
            elenchus_receipt=rec_contra_clean,
        )
        raise AssertionError("Sealed contradictory craft_lenses/craftLenses veto on gwi_report")
    except ValueError:
        pass

    saved_gw = (
        (mal_repo / "evidence" / "goldwood.json").read_text(encoding="utf-8")
        if (mal_repo / "evidence" / "goldwood.json").is_file()
        else ""
    )
    try:
        (mal_repo / "evidence" / "goldwood.json").write_text(json.dumps(gwi_contra), encoding="utf-8")
        rec_contra_disk = run_elenchus_challenge(
            mal_repo,
            intent="Verify",
            reviews=[
                {"model_id": "claude-fable-5-1", "verdict": "APPROVE", "summary": "ok", "findings": []},
                {"model_id": "gpt-6-astra", "verdict": "APPROVE", "summary": "ok", "findings": []},
            ],
            write_receipt=False,
        )
        gate_contra_disk = evaluate_elenchus_gate(mal_repo, receipt=rec_contra_disk, gwi_report=gwi_contra)
        assert (
            gate_contra_disk["passed"] is False
            and gate_contra_disk["seal_status"] == "CORRUPTED"
            and gate_contra_disk["reason_code"] == "CORRUPTED_OR_CONTRADICTORY_RECEIPT"
        )
    finally:
        if saved_gw:
            (mal_repo / "evidence" / "goldwood.json").write_text(saved_gw, encoding="utf-8")
        elif (mal_repo / "evidence" / "goldwood.json").is_file():
            (mal_repo / "evidence" / "goldwood.json").unlink()

    for r14_good in (
        'Blockers: None\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Keyword-free caveats after an approval claim with a JSON `APPROVE` block still pass.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### Target 1 — Cross-sentence / cross-line conditional approval\nAll checks verified.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### Conditions-handling regression suite\nAll checks verified.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'TODO: None\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Known issues: None\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '_Disposition_: _APPROVED_\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Verified `CI` and `tests` are green.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'all → APPROVE when followed by `Disposition: APPROVED` + JSON.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '- [P1] No blocking issues found.\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '0 P1 remaining.\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| # | Item | Status |\n|---|---|---|\n| 1 | P1 | ✅ Verified closed |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| 1 | P1 | Fixed |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Check | P0/P1 | Status |\n|---|---|---|\n| Parser | 0 | PASS |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 is fixed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 has been resolved.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1 (auth) is fixed.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### P1 (blocking)\nNone found.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Component | P0 | P1 | P2 |\n|---|---|---|---|\n| Parser | 0 | 0 | 1 |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Fail‑closed (false‑CHALLENGE, never false‑APPROVE), so safe, but noisy.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '### P1\nFindings:\nNone found.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '| Check | `P0/P1` | `Status` |\n|---|---|---|\n| Parser | `0` | `PASS` |\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        '"[P1] Parser crash on empty input"\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'P1/P0 findings remain adjudication-blocked; zero counts and quoted citations approve and seal successfully.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Six approve — all outside the Round-25 contract and all defensible, but noted as P2 observations below.\n\n**Disposition: APPROVED**\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'Temporary directory deleted; zero pending verification processes.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
        'No changes required; 0 tests failed; 0 security vulnerabilities in src/sovereign_kb/elenchus.py.\n\nDisposition: APPROVED\n```json\n{"verdict":"APPROVE","findings":[]}\n```',
    ):
        assert parse_reviewer_output("claude-fable-5-1", r14_good)["verdict"] == "APPROVE"

    import sovereign_kb.audit_registry as ar_mod
    from sovereign_kb.elenchus import _record_triad_from_elenchus

    alt_reg = tmp_path / "r23_alt_reg.json"
    monkeypatch.setattr(ar_mod, "AUDIT_REGISTRY_PATH", alt_reg)
    rec_alt_panel = run_elenchus_challenge(
        mal_repo,
        intent="Verify",
        reviews=[
            {"model_id": "qwen3.6-35b-a3b", "verdict": "APPROVE", "summary": "ok", "findings": []},
            {"model_id": "gemini-3-pro", "verdict": "APPROVE", "summary": "ok", "findings": []},
        ],
        write_receipt=False,
    )
    _record_triad_from_elenchus(mal_repo, ".", rec_alt_panel)
    alt_data = json.loads(alt_reg.read_text(encoding="utf-8"))
    alt_proj = next(iter(alt_data["projects"].values()))
    assert alt_proj["triad"]["fable_model"] is None
    assert alt_proj["triad"]["astra_model"] is None
    assert alt_proj["triad"]["panel_models"] == ["qwen3.6-35b-a3b", "gemini-3-pro"]
    sp_alt_models = alt_proj["subprojects"]["."]["models"]
    assert "qwen3.6-35b-a3b" in sp_alt_models and "gemini-3-pro" in sp_alt_models
    assert not any("claude-fable-5-1" in m or "gpt-6-astra" in m for m in sp_alt_models)

    fail_reg = tmp_path / "r24_fail_reg.json"
    monkeypatch.setattr(ar_mod, "AUDIT_REGISTRY_PATH", fail_reg)
    rec_partial_fail = run_elenchus_challenge(
        mal_repo,
        intent="Verify",
        reviews=[
            {
                "model_id": "claude-fable-5-1",
                "verdict": "CHALLENGE",
                "execution_status": "FAILED",
                "summary": "timeout",
                "findings": [],
            },
            {
                "model_id": "gpt-6-astra",
                "verdict": "APPROVE",
                "execution_status": "OK",
                "summary": "ok",
                "findings": [],
            },
            {
                "model_id": "qwen3.6-35b-a3b",
                "verdict": "APPROVE",
                "execution_status": "OK",
                "summary": "ok",
                "findings": [],
            },
        ],
        write_receipt=False,
    )
    _record_triad_from_elenchus(mal_repo, ".", rec_partial_fail)
    fail_data = json.loads(fail_reg.read_text(encoding="utf-8"))
    fail_proj = next(iter(fail_data["projects"].values()))
    assert fail_proj["triad"]["fable_model"] is None
    assert fail_proj["triad"]["astra_model"] == "gpt-6-astra"
    assert fail_proj["triad"]["gx10_model"] == "qwen3.6-35b-a3b"
    assert fail_proj["triad"]["panel_models"] == ["gpt-6-astra", "qwen3.6-35b-a3b"]
    sp_fail_models = fail_proj["subprojects"]["."]["models"]
    assert not any("claude-fable-5-1" in m for m in sp_fail_models)

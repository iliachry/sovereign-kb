"""Regression tests for confirmed Elenchus gate bugs (status normalization, adjudication override, receipt-stable diff)."""

from __future__ import annotations

import http.client
import json
import subprocess
from pathlib import Path

import pytest

import sovereign_kb.audit_registry as audit_registry
import sovereign_kb.elenchus as el_mod
from sovereign_kb.elenchus import (
    _collect_bounded_diff,
    adjudicate_elenchus_finding,
    compute_effective_panel_size,
    evaluate_elenchus_gate,
    run_elenchus_challenge,
    synthesize_elenchus_verdict,
)

FABLE = "amazon-bedrock/global.anthropic.claude-fable-5-1"
ASTRA = "amazon-bedrock/global.openai.gpt-6-astra"


@pytest.fixture
def iso_env(tmp_path: Path, monkeypatch) -> Path:
    swm_home = tmp_path / "swm"
    monkeypatch.setenv("SWM_HOME", str(swm_home))
    monkeypatch.setenv("SOVEREIGN_LEDGER_FILE", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setattr(audit_registry, "AUDIT_REGISTRY_PATH", swm_home / "audit_registry.json")
    return tmp_path


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init_repo(repo: Path, ignore_evidence: bool = True) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    if ignore_evidence:
        (repo / ".gitignore").write_text("evidence/\n", encoding="utf-8")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")


def _approve_all() -> list[dict]:
    return [
        {"model_id": FABLE, "verdict": "APPROVE", "findings": []},
        {"model_id": ASTRA, "verdict": "APPROVE", "findings": []},
    ]


@pytest.mark.parametrize("status", ["TIMEOUT", "ERROR", "CANCELLED", "garbage"])
def test_non_ok_execution_status_is_failed_not_approval(tmp_path: Path, status: str) -> None:
    pk = {"target_dir": str(tmp_path), "repo_root": str(tmp_path), "intent": "x", "bindings": {}}
    revs = [
        {"model_id": FABLE, "verdict": "APPROVE", "findings": []},
        {"model_id": ASTRA, "execution_status": status, "verdict": "APPROVE", "findings": []},
    ]
    syn = synthesize_elenchus_verdict(revs, pk, target_dir=tmp_path)
    assert [r["execution_status"] for r in syn["reviewer_summaries"]] == ["OK", "FAILED"]
    assert syn["seal_status"] != "READY_UNANIMOUS"
    assert syn["panel"]["n_eff"] == pytest.approx(1.0)

    panel = compute_effective_panel_size([{"model_id": FABLE}, {"model_id": ASTRA, "execution_status": status}])
    assert panel["failed_reviewer_count"] == 1
    assert panel["gate_closable"] is False


def test_missing_or_ok_execution_status_still_counts_ok() -> None:
    panel = compute_effective_panel_size([{"model_id": FABLE}, {"model_id": ASTRA, "execution_status": " ok "}])
    assert panel["failed_reviewer_count"] == 0
    assert panel["n_eff"] == pytest.approx(2.0)


def test_single_cluster_reject_cannot_silently_overwrite_accept(iso_env: Path) -> None:
    repo = iso_env / "repo"
    _init_repo(repo)
    ledger = iso_env / "ledger.jsonl"
    f = {"severity": "P0", "file_path": "a.py", "line": 1, "claim": "x is the wrong constant and breaks everything"}
    revs = [
        {"model_id": FABLE, "verdict": "VETO", "findings": [f]},
        {"model_id": ASTRA, "verdict": "VETO", "findings": [f]},
    ]
    rec = run_elenchus_challenge(repo, intent="t", reviews=revs, ledger_file=ledger)
    cid = rec["clusters"][0]["cluster_id"]
    r = adjudicate_elenchus_finding(repo, cid, "accept", ledger_file=ledger)
    assert r["seal_status"] == "BLOCKED_DEFECT_CONFIRMED"

    with pytest.raises(ValueError, match="already adjudicated ACCEPT_FINDING"):
        adjudicate_elenchus_finding(repo, cid, "reject", reason="I changed my mind on this", ledger_file=ledger)
    stored = json.loads((repo / "evidence" / "elenchus_challenge.json").read_text(encoding="utf-8"))
    assert stored["seal_status"] == "BLOCKED_DEFECT_CONFIRMED"
    assert evaluate_elenchus_gate(repo)["passed"] is False

    r = adjudicate_elenchus_finding(
        repo, cid, "reject", reason="I changed my mind on this", ledger_file=ledger, override=True
    )
    assert r["seal_status"] == "READY_ADJUDICATED"
    adj = r["clusters"][0]["adjudication"]
    assert adj["decision"] == "REJECT_FINDING"
    assert adj["override"] is True
    assert adj["overridden_decision"] == "ACCEPT_FINDING"
    assert [h["decision"] for h in adj["history"]] == ["ACCEPT_FINDING"]


def test_cli_exposes_override_flag() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers()
    el_mod.register_elenchus_subparser(sub)
    ns = ap.parse_args(["elenchus", "adjudicate", ".", "--finding", "ELC-001", "--override"])
    assert ns.override is True


def test_receipt_artifacts_do_not_perturb_bound_diff(iso_env: Path) -> None:
    repo = iso_env / "repo"
    _init_repo(repo, ignore_evidence=False)
    (repo / "evidence").mkdir()
    (repo / "evidence" / "elenchus_challenge.json").write_text("{}\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "receipt")
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")

    before, _, _ = _collect_bounded_diff(repo, True)
    (repo / "evidence" / "elenchus_challenge.json").write_text('{"changed": 1}\n', encoding="utf-8")
    (repo / "evidence" / "goldwood.json").write_text("{}\n", encoding="utf-8")
    (repo / "evidence" / "cap.evr").write_bytes(b"\x00evr")
    after, touched, _ = _collect_bounded_diff(repo, True)
    assert before == after
    assert "x = 2" in after
    assert not any("evidence" in t for t in touched)
    (repo / "evidence" / "goldwood.json").unlink()  # an invalid GWI receipt would fail the gate on its own

    rec = run_elenchus_challenge(repo, intent="t", reviews=_approve_all(), ledger_file=iso_env / "ledger.jsonl")
    assert rec["seal_status"] == "READY_UNANIMOUS"
    gate = evaluate_elenchus_gate(repo)
    assert gate["reason_code"] == "OK", gate


def test_http_incomplete_read_marks_reviewer_failed(monkeypatch) -> None:
    def _boom(*_a, **_k):
        raise http.client.IncompleteRead(b"partial")

    monkeypatch.setattr(el_mod.urllib.request, "urlopen", _boom)
    rev = el_mod._query_openai_compat_reviewer(FABLE, "https://example.com/v1/chat/completions", "p", timeout=1)
    assert rev["execution_status"] == "FAILED"
    assert rev["verdict"] == "CHALLENGE"

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import sovereign_kb.audit_registry as audit_registry
from sovereign_kb.audit_registry import evaluate_subproject_freshness, record_gwi_audit
from sovereign_kb.bridge import record_gwi_outcome_feedback
from sovereign_kb.gwi import evaluate_target
from sovereign_kb.organon import (
    build_showcase_payload,
    collect_organon_status,
    main_cli,
    merge_audit_registries,
    sync_organon_state_remote,
)
from sovereign_kb.rsi import split_benchmark_suite


def test_execute_gate_receipt_and_veto(tmp_path: Path) -> None:
    """F-13: Run-bound --execute-gate receipt verification, worktree_sha invalidation, and GWI-APP-U05 veto."""
    import subprocess

    app_dir = tmp_path / "sample-app"
    app_dir.mkdir()
    (app_dir / ".gitignore").write_text("evidence/\nartifacts/\n", encoding="utf-8")
    (app_dir / "README.md").write_text("# Sample App\n", encoding="utf-8")
    (app_dir / "pyproject.toml").write_text('[project]\nname = "sample-app"\nversion = "0.1.0"\n', encoding="utf-8")
    scripts_dir = app_dir / "scripts"
    scripts_dir.mkdir()
    gate_sh = scripts_dir / "quality_gate.sh"
    gate_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    gate_sh.chmod(0o755)

    # Initialize a clean committed Git repository so we test both clean-tree receipt reuse and dirty/hash invalidation
    subprocess.run(["git", "-C", str(app_dir), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(app_dir), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(app_dir), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(app_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(app_dir), "commit", "-q", "-m", "initial"], check=True)

    rep_pass = evaluate_target(app_dir, profile="app", execute_gate=True)
    assert rep_pass["audit_flags"]["execute_gate"] is True
    assert rep_pass["gate_execution"]["executed"] is True
    assert rep_pass["gate_execution"]["cached"] is False
    assert rep_pass["gate_execution"]["passed"] is True
    assert rep_pass["gate_execution"]["fresh_for_tree"] is True
    assert (app_dir / "evidence" / "gate_receipt.json").is_file()
    assert not any(b.get("id") == "GWI-APP-U05" for b in rep_pass.get("blockers", []))

    # Second call on the same clean tree reuses the cached receipt (`cached: True`)
    rep_cached = evaluate_target(app_dir, profile="app", execute_gate=True)
    assert rep_cached["gate_execution"]["cached"] is True
    assert rep_cached["gate_execution"]["passed"] is True

    # Tamper with gate_receipt.json's worktree_sha on the clean tree -> forces re-execution (`cached: False`)
    receipt_file = app_dir / "evidence" / "gate_receipt.json"
    tampered = json.loads(receipt_file.read_text(encoding="utf-8"))
    tampered["worktree_sha"] = "0000000000000000"
    receipt_file.write_text(json.dumps(tampered), encoding="utf-8")
    rep_reexec = evaluate_target(app_dir, profile="app", execute_gate=True)
    assert rep_reexec["gate_execution"]["cached"] is False
    assert rep_reexec["gate_execution"]["passed"] is True

    # Mutate working-tree file WITHOUT deleting gate_receipt.json -> dirty + worktree_sha change forces re-execution
    gate_sh.write_text("#!/usr/bin/env bash\necho 'unit test failure' >&2\nexit 1\n", encoding="utf-8")
    rep_fail = evaluate_target(app_dir, profile="app", execute_gate=True)
    assert rep_fail["gate_execution"]["cached"] is False
    assert rep_fail["gate_execution"]["passed"] is False
    assert any(b.get("id") == "GWI-APP-U05" for b in rep_fail.get("blockers", []))
    assert rep_fail["tiers"]["S_act"] <= 6.20
    assert rep_fail["calculation"]["status"] == "FAIL"

    # A gate that mutates source during execution is rejected (`source_mutated_during_gate: True`, `passed: False`)
    gate_sh.write_text(
        "#!/usr/bin/env bash\necho '# mutated' >> README.md\nexit 0\n",
        encoding="utf-8",
    )
    rep_mut = evaluate_target(app_dir, profile="app", execute_gate=True)
    assert rep_mut["gate_execution"]["source_mutated_during_gate"] is True
    assert rep_mut["gate_execution"]["passed"] is False
    assert any(b.get("id") == "GWI-APP-U05" for b in rep_mut.get("blockers", []))

    # A gate that mutates a Unicode-named tracked file (`café.py`) during execution is also rejected via `git ls-files -z`
    cafe_py = app_dir / "café.py"
    cafe_py.write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(app_dir), "add", "café.py"], check=True)
    gate_sh.write_text("#!/usr/bin/env bash\necho 'x = 2' >> café.py\nexit 0\n", encoding="utf-8")
    rep_cafe = evaluate_target(app_dir, profile="app", execute_gate=True)
    assert rep_cafe["gate_execution"]["source_mutated_during_gate"] is True
    assert rep_cafe["gate_execution"]["passed"] is False

    # Verify subproject (`packages/app`) gate execution preserves `(git_root, subpath)` scope and caches cleanly
    sub_app = app_dir / "packages" / "app"
    (sub_app / "scripts").mkdir(parents=True)
    (sub_app / "README.md").write_text("# Sub App\n", encoding="utf-8")
    (sub_app / "pyproject.toml").write_text('[project]\nname = "sub-app"\nversion = "0.1.0"\n', encoding="utf-8")
    sub_gate = sub_app / "scripts" / "quality_gate.sh"
    sub_gate.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    sub_gate.chmod(0o755)
    gate_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(app_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(app_dir), "commit", "-q", "-m", "add subproject"], check=True)
    rep_sub1 = evaluate_target(sub_app, profile="app", execute_gate=True)
    assert rep_sub1["gate_execution"]["passed"] is True
    assert rep_sub1["gate_execution"]["source_mutated_during_gate"] is False
    assert rep_sub1["gate_execution"]["fresh_for_tree"] is True
    rep_sub2 = evaluate_target(sub_app, profile="app", execute_gate=True)
    assert rep_sub2["gate_execution"]["cached"] is True
    assert rep_sub2["gate_execution"]["passed"] is True

    # Verify gate executes BEFORE profile diagnostics so newly written artifacts/e2e.diagnostics.json are read immediately
    gate_sh.write_text(
        "#!/usr/bin/env bash\nmkdir -p artifacts\n"
        'echo \'{"consoleErrors": ["runtime err"], "pageErrors": ["uncaught err"]}\' > artifacts/e2e.diagnostics.json\nexit 0\n',
        encoding="utf-8",
    )
    rep_diag = evaluate_target(app_dir, profile="app", execute_gate=True)
    assert rep_diag["gate_execution"]["passed"] is True
    assert any(b.get("id") == "GWI-APP-R01" for b in rep_diag.get("blockers", []))


def test_strict_audit_registry_cache_keys(tmp_path: Path, monkeypatch) -> None:
    reg_file = tmp_path / "audit_registry.json"
    monkeypatch.setattr(audit_registry, "AUDIT_REGISTRY_PATH", reg_file)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "README.md").write_text("# Repo\n", encoding="utf-8")
    ev_dir = repo_dir / "evidence"
    ev_dir.mkdir()
    (ev_dir / "metrics.json").write_text('{"ok": true}\n', encoding="utf-8")

    fake_report = {
        "profile": "app",
        "calculation": {"goldwood_g": 9.60, "status": "PASS", "verdict": "PASS"},
        "tiers": {"S_sub": 9.6, "S_perc": 9.6, "S_act": 9.6, "S_exist": 9.6},
        "craft_lenses": {"coupled_goldwood_g": 9.60, "veto_triggered": False},
        "audit_flags": {"strict_lenses": False, "probe_audio": False, "execute_gate": False},
        "blockers": [],
    }
    rec = record_gwi_audit(repo_dir, fake_report)
    assert rec["evidence_sha"] != "none"

    # Fresh under default flags
    f1 = evaluate_subproject_freshness(repo_dir, ".")
    assert f1["freshness"] == "FRESH"
    assert f1["is_fresh_pass"] is True

    # Lower strictness when --strict-lenses or --execute-gate is requested
    f_strict = evaluate_subproject_freshness(repo_dir, ".", require_strict_lenses=True)
    assert f_strict["freshness"] == "LOWER_STRICTNESS"
    assert f_strict["is_fresh_pass"] is False

    # Profile mismatch when paper profile is requested on an app record
    f_prof = evaluate_subproject_freshness(repo_dir, ".", requested_profile="paper")
    assert f_prof["freshness"] == "PROFILE_MISMATCH"
    assert f_prof["is_fresh_pass"] is False

    # Mutate nested evidence/playwright/report.json or artifacts/walkthrough-report.json -> STALE_EVIDENCE
    pw_dir = ev_dir / "playwright"
    pw_dir.mkdir()
    (pw_dir / "report.json").write_text('{"passed": 12}\n', encoding="utf-8")
    art_dir = repo_dir / "artifacts"
    art_dir.mkdir()
    (art_dir / "walkthrough-report.json").write_text('{"consoleErrors": []}\n', encoding="utf-8")
    record_gwi_audit(repo_dir, fake_report)
    assert evaluate_subproject_freshness(repo_dir, ".")["freshness"] == "FRESH"
    (art_dir / "walkthrough-report.json").write_text('{"consoleErrors": ["err"]}\n', encoding="utf-8")
    f_ev = evaluate_subproject_freshness(repo_dir, ".")
    assert f_ev["freshness"] == "STALE_EVIDENCE"
    assert f_ev["is_fresh_pass"] is False

    # Missing evidence_sha in registry fails closed -> STALE_EVIDENCE
    reg_obj = json.loads(reg_file.read_text(encoding="utf-8"))
    reg_obj["projects"]["repo"]["subprojects"]["."]["evidence_sha"] = ""
    reg_file.write_text(json.dumps(reg_obj), encoding="utf-8")
    assert evaluate_subproject_freshness(repo_dir, ".")["freshness"] == "STALE_EVIDENCE"


def test_merge_audit_registries_and_remote_sync_failure(tmp_path: Path, monkeypatch) -> None:
    """F-16: Verify timezone-aware cross-node audit registry merge and SSH error/invalid-JSON handling."""
    # 10:00+02:00 is 08:00 UTC; 09:00+00:00 is 09:00 UTC (1 hour later!)
    local_reg = {
        "schema_version": "1.1.0",
        "projects": {
            "demo-repo": {
                "repo_root": "/tmp/demo-repo",
                "subprojects": {
                    ".": {
                        "status": "PASS",
                        "goldwood_g": 9.5,
                        "audited_at": "2026-11-04T10:00:00+02:00",
                    },
                    "equal-ts": {
                        "status": "PASS",
                        "goldwood_g": 9.4,
                        "audited_at": "2026-11-04T09:00:00+00:00",
                    },
                },
            }
        },
    }
    remote_reg = {
        "schema_version": "1.1.0",
        "projects": {
            "demo-repo": {
                "repo_root": "/tmp/demo-repo",
                "subprojects": {
                    ".": {
                        "status": "FAIL",
                        "goldwood_g": 6.2,
                        "audited_at": "2026-11-04T09:00:00+00:00",
                    },
                    "equal-ts": {
                        "status": "FAIL",
                        "goldwood_g": 6.0,
                        "audited_at": "2026-11-04T09:00:00+00:00",
                    },
                    "packages/web": {
                        "status": "PASS",
                        "goldwood_g": 9.3,
                        "audited_at": "2026-11-04T09:00:00+00:00",
                    },
                },
            }
        },
    }
    merged = merge_audit_registries(local_reg, remote_reg)
    subs = merged["projects"]["demo-repo"]["subprojects"]
    assert subs["."]["status"] == "FAIL"
    assert subs["equal-ts"]["status"] == "FAIL"
    assert "packages/web" in subs

    import subprocess

    import sovereign_kb.organon as organon_mod

    monkeypatch.setattr(
        organon_mod.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            args=a, returncode=255, stdout="", stderr="ssh connection refused"
        ),
    )
    sync_res = sync_organon_state_remote("nonexistent-invalid-host-12345.local", quiet=True)
    assert sync_res["synced"] is False
    assert "ssh connection refused" in str(sync_res.get("error", ""))

    # Reject SSH stdout containing banner + JSON instead of silently falling back to local_reg
    reg_file = tmp_path / "audit_registry.json"
    reg_file.write_text(json.dumps(local_reg), encoding="utf-8")
    monkeypatch.setattr(organon_mod, "AUDIT_REGISTRY_PATH", reg_file)
    monkeypatch.setattr(
        organon_mod.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            args=a, returncode=0, stdout='WELCOME BANNER\n{"projects": {}}', stderr=""
        ),
    )
    sync_banner = sync_organon_state_remote("peer-with-banner.local", quiet=True)
    assert sync_banner["synced"] is False
    assert "invalid JSON" in str(sync_banner.get("error", ""))


def test_gwi_outcome_feedback_into_rsi_probes(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / ".kb").mkdir(parents=True)
    report = {
        "profile": "app",
        "calculation": {"goldwood_g": 7.80, "status": "FAIL"},
        "craft_lenses": {"coupled_goldwood_g": 7.80},
        "audit_flags": {"strict_lenses": True, "probe_audio": False, "execute_gate": True},
        "blockers": [
            {
                "id": "GWI-APP-L07",
                "severity": "P1",
                "dimension": "Law L7 Regularity",
                "message": "Distinct font sizes exceeded canonical budget",
            }
        ],
    }
    res = record_gwi_outcome_feedback(tmp_path / "demo-repo", report, commit_sha="abc1234", vault_dir=vault)
    assert res["probes_added"] == 1
    probes_file = vault / ".kb" / "gwi_outcome_probes.json"
    assert probes_file.is_file()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE documents (rel_path TEXT, doc_type TEXT)")
    conn.execute("INSERT INTO documents VALUES ('02-operating-system/subtractive-design-laws.md', 'operating-system')")
    _, heldout = split_benchmark_suite(conn, vault, probes=[])
    conn.close()
    assert any("GWI-APP-L07" in str(p.get("query", "")) for p in heldout)


def test_organon_status_and_showcase_cli(capsys) -> None:
    st = collect_organon_status(all_nodes=False)
    assert "episteme_kb" in st
    assert "techne_swm" in st
    assert "phronesis_gwi" in st
    assert "pistis_chain" in st

    showcase = build_showcase_payload()
    assert len(showcase["workflows"]) == 6

    main_cli(["showcase", "--json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert len(parsed["workflows"]) == 6

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


def test_organon_open_core_portability_without_swm_or_mekb(tmp_path: Path, monkeypatch, capsys) -> None:
    """F-16: Verify Open-Core Organon works cleanly out-of-the-box without `swm`, `me-kb`, or `KB_REMOTE_PEER`."""
    import subprocess

    import pytest

    from sovereign_kb.cli import main as skb_main

    # 1. Initialize a clean standalone `skb` vault and configure Open-Core standalone env
    user_vault = tmp_path / "open-core-notes"
    skb_main(["init", str(user_vault), "--no-embed"])
    capsys.readouterr()

    monkeypatch.setenv("SWM_BIN", "")
    monkeypatch.setenv("ORGANON_IGNORE_DEFAULT_ME_KB", "1")
    monkeypatch.delenv("ME_KB_DIR", raising=False)
    monkeypatch.setenv("KB_VAULT_DIR", str(user_vault))
    monkeypatch.setenv("KB_REMOTE_PEER", "")

    # 2. `organon status` reports Open-Core edition and binds to `user_vault`
    st = collect_organon_status(all_nodes=False)
    assert Path(st["episteme_kb"]["vault_dir"]).resolve() == user_vault.resolve()
    assert st["techne_swm"]["installed"] is False
    assert st["techne_swm"]["edition"] == "open-core"
    assert st["techne_swm"]["version"] == "open-core"
    assert st["techne_swm"]["git_sha"] == "not-installed"

    main_cli(["status"])
    human_status = capsys.readouterr().out
    assert "Open-Core Edition" in human_status

    # 3. `organon kb status` delegates in-process to `skb` against `user_vault` without requiring `~/.local/bin/kb`
    main_cli(["kb", "status"])
    kb_out = capsys.readouterr().out
    assert str(user_vault) in kb_out

    # 4. `organon swm --help` exits 0 with Open-Core overview; `organon swm queue --json` exits 2 cleanly (no FileNotFoundError)
    main_cli(["swm", "--help"])
    swm_help = capsys.readouterr().out
    assert "Open-Core" in swm_help
    assert "goldwood.tech/organon" in swm_help

    with pytest.raises(SystemExit) as exc_swm:
        main_cli(["swm", "queue", "--json"])
    assert exc_swm.value.code == 2
    swm_json = json.loads(capsys.readouterr().out)
    assert swm_json["installed"] is False
    assert swm_json["edition"] == "open-core"

    # 5. `organon task <id>` assembles the Episteme Context Pack locally in preview mode (`exit 0`), and exits `2` on `--execute`
    main_cli(["task", "42"])
    task_out = capsys.readouterr().out
    assert "Episteme Context Pack" in task_out
    assert "Open-Core Mode" in task_out

    with pytest.raises(SystemExit) as exc_exec:
        main_cli(["task", "42", "--execute"])
    assert exc_exec.value.code == 2
    capsys.readouterr()

    # 6. `organon finish <repo> --skip-push` succeeds in single-node Open-Core mode when `KB_REMOTE_PEER` is unset
    import sys

    sample_repo = tmp_path / "open-core-app"
    sample_repo.mkdir()
    (sample_repo / ".gitignore").write_text("evidence/\n.pytest_cache/\n__pycache__/\n", encoding="utf-8")
    (sample_repo / "README.md").write_text(
        "# Open-Core Sample Service\n\n"
        "Deterministic reference service verifying single-node Open-Core `organon finish` execution, "
        "run-bound GWI 9+ behavioral receipts, and RFC 6962 Merkle attestation without remote SSH peers.\n\n"
        "## Quickstart\n\n```bash\n./scripts/quality_gate.sh\n```\n",
        encoding="utf-8",
    )
    (sample_repo / "AGENTS.md").write_text(
        "# AGENTS.md\n\n## Feature Registry\n"
        "- `FEAT-01`: Deterministic addition (`src/app.py`)\n"
        "- `FEAT-02`: Deterministic subtraction (`src/app.py`)\n"
        "- `FEAT-03`: Deterministic multiplication (`src/app.py`)\n",
        encoding="utf-8",
    )
    (sample_repo / "pyproject.toml").write_text(
        '[project]\nname = "open-core-sample"\nversion = "0.1.0"\nrequires-python = ">=3.10"\ndependencies = []\n',
        encoding="utf-8",
    )
    (sample_repo / "src").mkdir()
    (sample_repo / "src" / "app.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n\n"
        "def sub(a: int, b: int) -> int:\n    return a - b\n\n"
        "def mul(a: int, b: int) -> int:\n    return a * b\n",
        encoding="utf-8",
    )
    (sample_repo / "tests").mkdir()
    (sample_repo / "tests" / "test_app.py").write_text(
        'def test_feat_01() -> None:\n    """FEAT-01"""\n    assert 1 + 1 == 2\n\n'
        'def test_feat_02() -> None:\n    """FEAT-02"""\n    assert 3 - 1 == 2\n\n'
        'def test_feat_03() -> None:\n    """FEAT-03"""\n    assert 2 * 3 == 6\n',
        encoding="utf-8",
    )
    (sample_repo / "scripts").mkdir()
    gate = sample_repo / "scripts" / "quality_gate.sh"
    gate.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{sys.executable} -m pytest -q\n", encoding="utf-8")
    gate.chmod(0o755)
    subprocess.run(["git", "-C", str(sample_repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(sample_repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(sample_repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(sample_repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(sample_repo), "commit", "-q", "-m", "init"], check=True)

    monkeypatch.setenv("SWM_HOME", str(tmp_path / "swm-home"))
    monkeypatch.setattr("sovereign_kb.organon._get_cluster_env", lambda _k: "")
    main_cli(["finish", str(sample_repo), "--skip-push"])
    fin_out = capsys.readouterr().out
    assert "Single-node mode" in fin_out
    assert "Anchored " in fin_out


def test_organon_vault_fail_closed_cwd_walkup_and_starter_vault_immutability(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Verify starter_vault immutability, fail-closed invalid vault configs, cwd walk-up parity, and SWM version gating."""
    import shutil
    import subprocess
    import sys

    import pytest

    from sovereign_kb.audit_registry import resolve_organon_vault_state, resolve_swm_bin
    from sovereign_kb.cli import main as skb_main
    from sovereign_kb.organon import _resolve_openproject_bin

    starter_dir = (Path(audit_registry.__file__).resolve().parent / "starter_vault").resolve()
    assert not (starter_dir / ".kb").exists()

    # 1. Clean unconfigured HOME + non-vault workdir containing README.md
    clean_home = tmp_path / "clean-home"
    work_dir = clean_home / "some-app"
    work_dir.mkdir(parents=True)
    (work_dir / "README.md").write_text("# App Repo\n", encoding="utf-8")

    monkeypatch.setenv("HOME", str(clean_home))
    monkeypatch.setenv("SWM_HOME", str(clean_home / ".swm"))
    monkeypatch.delenv("ME_KB_DIR", raising=False)
    monkeypatch.delenv("KB_VAULT_DIR", raising=False)
    monkeypatch.delenv("OPENPROJECT_QUERY_BIN", raising=False)
    monkeypatch.setenv("KB_REMOTE_PEER", "")
    monkeypatch.setattr("sovereign_kb.organon._get_cluster_env", lambda _k: "")
    monkeypatch.chdir(work_dir)

    # Even if ~/Projects/software-machine source checkout exists on disk, missing `swm` binary => Open-Core version
    fake_swm_src = clean_home / "Projects" / "software-machine" / "src" / "software_machine"
    fake_swm_src.mkdir(parents=True)
    (fake_swm_src / "__init__.py").write_text('__version__ = "0.9.9"\n', encoding="utf-8")
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda cmd: None if cmd == "swm" else real_which(cmd))
    monkeypatch.delenv("SWM_BIN", raising=False)

    v_path, v_source = resolve_organon_vault_state()
    assert v_source == "unconfigured"
    assert v_path != starter_dir

    st = collect_organon_status(all_nodes=False)
    assert st["episteme_kb"]["configured"] is False
    assert st["episteme_kb"]["vault_source"] == "unconfigured"
    assert st["techne_swm"]["installed"] is False
    assert st["techne_swm"]["edition"] == "open-core"
    assert st["techne_swm"]["version"] == "open-core"
    assert st["techne_swm"]["git_sha"] == "not-installed"
    assert _resolve_openproject_bin() is None

    # Invalid explicit SWM_BIN warns once per unique path on stderr and returns None
    monkeypatch.setenv("SWM_BIN", str(clean_home / "nonexistent-swm"))
    assert resolve_swm_bin() is None
    assert "Warning: SWM_BIN=" in capsys.readouterr().err
    assert resolve_swm_bin() is None
    assert capsys.readouterr().err == ""
    monkeypatch.setenv("SWM_BIN", "")

    # Unconfigured verify & task succeed gracefully without mutating starter_vault or work_dir
    main_cli(["verify"])
    ver_out = capsys.readouterr().out
    assert "[skip]" in ver_out

    main_cli(["task", "42"])
    t_out = capsys.readouterr().out
    assert "contract=v1.4.0, hits=0" in t_out

    # Unconfigured `organon kb capture` fails closed without polluting starter_vault or work_dir
    with pytest.raises(SystemExit) as exc_unconf_kb:
        main_cli(["kb", "capture", "should not write anywhere"])
    assert exc_unconf_kb.value.code == 1
    capsys.readouterr()
    assert not (starter_dir / ".kb").exists()
    assert not (work_dir / ".kb").exists()

    # 2. Explicitly configured invalid vault (`KB_VAULT_DIR` and `config.json`) MUST fail closed
    sample_repo = tmp_path / "fail-closed-app"
    sample_repo.mkdir()
    (sample_repo / ".gitignore").write_text("evidence/\n.pytest_cache/\n__pycache__/\n", encoding="utf-8")
    (sample_repo / "README.md").write_text(
        "# Fail Closed Sample\n\n"
        "Deterministic reference service verifying fail-closed Organon vault checks across `verify` and `finish` "
        "when an explicit vault path does not exist or is uninitialized.\n\n"
        "## Gate\n\n```bash\n./scripts/quality_gate.sh\n```\n",
        encoding="utf-8",
    )
    (sample_repo / "AGENTS.md").write_text(
        "# AGENTS.md\n\n## Feature Registry\n- `FEAT-01`: Health check (`src/app.py`)\n",
        encoding="utf-8",
    )
    (sample_repo / "pyproject.toml").write_text(
        '[project]\nname = "fc-app"\nversion = "0.1.0"\nrequires-python = ">=3.10"\ndependencies = []\n',
        encoding="utf-8",
    )
    (sample_repo / "src").mkdir()
    (sample_repo / "src" / "app.py").write_text("def ok() -> bool:\n    return True\n", encoding="utf-8")
    (sample_repo / "tests").mkdir()
    (sample_repo / "tests" / "test_app.py").write_text(
        'def test_feat_01() -> None:\n    """FEAT-01"""\n    assert True\n',
        encoding="utf-8",
    )
    (sample_repo / "scripts").mkdir()
    gate = sample_repo / "scripts" / "quality_gate.sh"
    gate.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{sys.executable} -m pytest -q\n", encoding="utf-8")
    gate.chmod(0o755)
    subprocess.run(["git", "-C", str(sample_repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(sample_repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(sample_repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(sample_repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(sample_repo), "commit", "-q", "-m", "init"], check=True)

    monkeypatch.setenv("KB_VAULT_DIR", str(tmp_path / "does-not-exist-env"))
    with pytest.raises(SystemExit) as exc_ver_env:
        main_cli(["verify"])
    assert exc_ver_env.value.code == 1
    with pytest.raises(SystemExit) as exc_task_env:
        main_cli(["task", "42"])
    assert exc_task_env.value.code == 1
    with pytest.raises(SystemExit) as exc_fin_env:
        main_cli(["finish", str(sample_repo), "--skip-push"])
    assert exc_fin_env.value.code == 1
    capsys.readouterr()

    monkeypatch.delenv("KB_VAULT_DIR", raising=False)
    cfg_file = clean_home / ".config" / "sovereign-kb" / "config.json"
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(json.dumps({"vault_dir": str(tmp_path / "does-not-exist-cfg")}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc_ver_cfg:
        main_cli(["verify"])
    assert exc_ver_cfg.value.code == 1
    with pytest.raises(SystemExit) as exc_fin_cfg:
        main_cli(["finish", str(sample_repo), "--skip-push"])
    assert exc_fin_cfg.value.code == 1
    capsys.readouterr()

    # 3. Multi-vault cwd walk-up parity: config.json -> vault_a, chdir(vault_b) -> both `skb` and `organon` pick vault_b
    vault_a = tmp_path / "vault-a"
    vault_b = tmp_path / "vault-b"
    skb_main(["init", str(vault_a), "--no-embed"])
    skb_main(["init", str(vault_b), "--no-embed"])
    cfg_file.write_text(json.dumps({"vault_dir": str(vault_a)}), encoding="utf-8")
    capsys.readouterr()

    monkeypatch.chdir(vault_b)
    resolved_v, resolved_src = resolve_organon_vault_state()
    assert resolved_src == "cwd"
    assert resolved_v == vault_b.resolve()

    main_cli(["kb", "capture", "Decision recorded inside vault B via cwd walkup."])
    capsys.readouterr()
    b_logs = list((vault_b / "04-episodic").glob("*-log.md"))
    a_logs = list((vault_a / "04-episodic").glob("*-log.md"))
    assert any("Decision recorded inside vault B" in p.read_text(encoding="utf-8") for p in b_logs)
    assert not any("Decision recorded inside vault B" in p.read_text(encoding="utf-8") for p in a_logs)
    assert not (starter_dir / ".kb").exists()

    # 4. Explicit configuration, symlink alias, case-variant alias, or `skb init` targeting `starter_vault` is rejected
    from sovereign_kb.audit_registry import is_bundled_starter_vault_path
    from sovereign_kb.retrieval import retrieve_from_vault

    starter_entries_before = sorted(p.relative_to(starter_dir) for p in starter_dir.rglob("*"))
    starter_symlink = tmp_path / "starter-alias"
    starter_symlink.symlink_to(starter_dir)
    monkeypatch.chdir(work_dir)

    alias_targets: list[Path] = [starter_dir, starter_symlink, starter_symlink / "02-operating-system"]
    upper_starter = starter_dir.with_name("STARTER_VAULT")
    if upper_starter.exists():
        upper_symlink = tmp_path / "starter-upper-alias"
        upper_symlink.symlink_to(upper_starter)
        alias_targets.extend([upper_starter, upper_symlink, upper_symlink / "nested-new-dir"])

    for alias_p in alias_targets:
        assert is_bundled_starter_vault_path(alias_p) is True

    for env_k in ("ME_KB_DIR", "KB_VAULT_DIR"):
        for alias_p in alias_targets:
            monkeypatch.delenv("ME_KB_DIR", raising=False)
            monkeypatch.delenv("KB_VAULT_DIR", raising=False)
            monkeypatch.setenv(env_k, str(alias_p))
            _, f_src = resolve_organon_vault_state()
            assert f_src == "forbidden_starter"
            assert collect_organon_status(all_nodes=False)["episteme_kb"]["configured"] is False
            for bad_args in (["verify"], ["task", "42"], ["finish", str(sample_repo), "--skip-push"], ["kb", "status"]):
                with pytest.raises(SystemExit) as exc_st:
                    main_cli(bad_args)
                assert exc_st.value.code == 1
            capsys.readouterr()

    monkeypatch.delenv("ME_KB_DIR", raising=False)
    monkeypatch.delenv("KB_VAULT_DIR", raising=False)
    for alias_p in alias_targets:
        cfg_file.write_text(json.dumps({"vault_dir": str(alias_p)}), encoding="utf-8")
        _, cfg_src = resolve_organon_vault_state()
        assert cfg_src == "forbidden_starter"
        for bad_args in (["verify"], ["task", "42"], ["finish", str(sample_repo), "--skip-push"], ["kb", "status"]):
            with pytest.raises(SystemExit) as exc_cfg_st:
                main_cli(bad_args)
            assert exc_cfg_st.value.code == 1
        with pytest.raises(SystemExit) as exc_skb_direct:
            skb_main(["--vault", str(alias_p), "status"])
        assert exc_skb_direct.value.code == 1
        with pytest.raises(SystemExit) as exc_skb_init:
            skb_main(["init", str(alias_p), "--no-embed"])
        assert exc_skb_init.value.code == 1
        with pytest.raises(SystemExit) as exc_org_init:
            main_cli(["kb", "init", str(alias_p), "--no-embed"])
        assert exc_org_init.value.code == 1
        ret_pack = retrieve_from_vault(alias_p, "guard check")
        assert ret_pack["declarative_chunks"] == []
        capsys.readouterr()

    # Verify resolve_active_vault_dir(), default_triad_ledger_path(), append_triad_provenance_event(), record_gwi_outcome_feedback(), and canary never touch starter_vault
    from sovereign_kb.audit_registry import resolve_active_vault_dir
    from sovereign_kb.bridge import (
        append_triad_provenance_event,
        default_triad_ledger_path,
        run_triad_end_to_end_canary,
    )

    cfg_file.unlink(missing_ok=True)
    home_default_link = clean_home / "sovereign-kb-vault"
    home_default_link.symlink_to(starter_dir)
    assert resolve_organon_vault_state()[1] == "forbidden_starter"
    assert not is_bundled_starter_vault_path(resolve_active_vault_dir())
    assert not is_bundled_starter_vault_path(default_triad_ledger_path(starter_dir))
    append_triad_provenance_event("gwi.evaluated", {"probe": 1}, ledger_file=starter_dir / "explicit.jsonl")
    monkeypatch.setenv("SWM_HOME", str(starter_dir))
    assert not is_bundled_starter_vault_path(default_triad_ledger_path(starter_dir))
    append_triad_provenance_event("gwi.evaluated", {"probe": 2}, ledger_file=starter_dir / "explicit.jsonl")
    record_gwi_outcome_feedback(work_dir, {"blockers": [{"id": "GWI-APP-L07"}]}, vault_dir=starter_dir)
    monkeypatch.setenv("SWM_HOME", str(clean_home / ".swm"))
    assert run_triad_end_to_end_canary()["canary_passed"] is True
    assert run_triad_end_to_end_canary(starter_dir)["canary_passed"] is True

    starter_entries_after = sorted(p.relative_to(starter_dir) for p in starter_dir.rglob("*"))
    assert starter_entries_after == starter_entries_before
    assert not (starter_dir / ".kb").exists()
    assert not (starter_dir / ".git").exists()

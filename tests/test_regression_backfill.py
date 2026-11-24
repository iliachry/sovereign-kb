"""Backfilled regression tests for fix commits that originally shipped without one.

Each test names the fix it guards; the commit adding them carries `Regression-test-for:` trailers
so `af.failure_to_gate` can credit the fix (see `gwi_antifragility.measure_failure_to_gate`).
"""

from __future__ import annotations

import datetime
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sovereign_kb import cli, gwi_science, readme_check, rsi
from sovereign_kb.cli import _parse_skill_description, is_doc_vector_eligible
from sovereign_kb.gwi import evaluate_app_profile, evaluate_paper_profile
from sovereign_kb.gwi_lenses import _lens_typography_editor


def test_0bc7ae3_readme_exists_unquotes_url_encoded_paths() -> None:
    files = {"docs/My Guide.md"}
    assert readme_check._exists("docs/My%20Guide.md", files, set())
    assert not readme_check._exists("docs/Other%20Guide.md", files, set())


def test_e276f21_pytest_collect_sums_per_file_counts(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    out = "tests/test_a.py: 3\ntests/test_b.py: 2\n"

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(gwi_science.subprocess, "run", fake_run)
    count, detail = gwi_science._pytest_collect(tmp_path)
    assert count == 5, detail


def test_c727792_skill_description_reads_folded_block_scalar(tmp_path: Path) -> None:
    md = tmp_path / "SKILL.md"
    md.write_text(
        "---\nname: demo\ndescription: >-\n  First line of the\n  folded description.\nversion: 1\n---\n# Demo\n",
        encoding="utf-8",
    )
    assert _parse_skill_description(md, use_pyyaml=False) == "First line of the folded description."


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def test_71ad00d_readme_surface_accepts_documented_command_without_pyproject(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("# Tool\n", encoding="utf-8")
    (repo / "app.py").write_text("import argparse\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "app.py").write_text(
        "import argparse\np = argparse.ArgumentParser()\nsub = p.add_subparsers()\nsub.add_parser('export')\n",
        encoding="utf-8",
    )
    readme = "# Tool\n\nRun `tool export` to write the archive.\n"
    (repo / "README.md").write_text(readme, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add export")
    issues = readme_check.surface_issues(repo, base, "HEAD", readme, set())
    assert not any("export" in i for i in issues), issues


def test_1a41f31_resizebox_gate_sees_through_latex_comments(tmp_path: Path) -> None:
    (tmp_path / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n"
        "\\resizebox{\\columnwidth}{!}{% shrink to fit\n"
        "\\begin{tabular}{ll}a & b\\\\\\end{tabular}}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    report = evaluate_paper_profile(tmp_path)
    assert any(b["id"] == "GWI-PAP-P02" for b in report.get("blockers", [])), report.get("blockers")


def test_1bd7466_typography_lens_reads_main_tex_beyond_first_ten_files(tmp_path: Path) -> None:
    for i in range(12):
        (tmp_path / f"a{i:02d}.tex").write_text(f"\\section{{Part {i}}}\n", encoding="utf-8")
    (tmp_path / "main.tex").write_text(
        "\\documentclass{article}\n\\usepackage{microtype}\n\\usepackage{booktabs}\n", encoding="utf-8"
    )
    ctx = SimpleNamespace(
        probe_findings=[],
        probe_measurements=[],
        report={"profile": "paper"},
        resolved=tmp_path,
        root=tmp_path,
        score=9.5,
        unmeasured_count=0,
        veto_triggered=False,
    )
    _lens_typography_editor(ctx)
    assert any(f.startswith("PASS [ty.widows_float_drift]") for f in ctx.probe_findings), ctx.probe_findings


def test_0aa3b28_old_episodic_logs_skip_vector_embedding() -> None:
    today = datetime.date.today()
    old = (today - datetime.timedelta(days=90)).isoformat()
    recent = (today - datetime.timedelta(days=5)).isoformat()
    assert is_doc_vector_eligible("00-identity/README-me.md")
    assert is_doc_vector_eligible(f"04-episodic/{recent}-log.md")
    assert not is_doc_vector_eligible(f"04-episodic/{old}-log.md")
    assert not is_doc_vector_eligible("04-episodic/notes.md", updated_date=old)


def _app_repo(root: Path) -> None:
    (root / "AGENTS.md").write_text("# Agents\n\n- FEAT-01: export\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def export():\n    return 1\n", encoding="utf-8")


def test_0aa3b28_hollow_test_stubs_raise_u04(tmp_path: Path) -> None:
    _app_repo(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_feat_01_export():\n    pass\n", encoding="utf-8")
    ids = {b["id"] for b in evaluate_app_profile(tmp_path).get("blockers", [])}
    assert "GWI-APP-U04" in ids


def test_0aa3b28_gate_script_with_unconfigured_runner_raises_s03(tmp_path: Path) -> None:
    _app_repo(tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "quality_gate.sh").write_text("#!/bin/sh\nnpx vitest run\n", encoding="utf-8")
    ids = {b["id"] for b in evaluate_app_profile(tmp_path).get("blockers", [])}
    assert "GWI-APP-S03" in ids


def test_bc89b21_usage_model_switch_takes_first_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: tmp_path))
    logs = tmp_path / ".gemini" / "antigravity" / "brain" / "c1" / ".system_generated" / "logs"
    logs.mkdir(parents=True)
    today = datetime.date.today().isoformat()
    rows = [
        {
            "type": "USER_INPUT",
            "content": "Changed `Model Selection` from claude-fable to claude-opus-x. Remember to rest",
        },
        {"type": "PLANNER_RESPONSE", "input_tokens": 10, "output_tokens": 5, "created_at": f"{today}T10:00:00Z"},
    ]
    (logs / "transcript.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    report = json.dumps(cli._scan_native_usage(2))
    assert '"modelName": "claude-opus-x"' in report
    assert '"modelName": "rest"' not in report


def _ev(j: float) -> dict[str, Any]:
    return {"objective_j": j, "mrr_at_5": j, "ndcg_at_5": j, "recall_at_1": j, "ledger_intrusions": 0}


def test_bc89b21_tuning_reverts_when_heldout_regresses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = tmp_path / "vault"
    (vault / ".kb").mkdir(parents=True)
    train = [{"query": "t", "expected": "a.md"}]
    held = [{"query": "h", "expected": "b.md", "split": "heldout"}]
    base = rsi.load_retrieval_policy(vault)
    heldout_calls: list[dict] = []

    def fake_eval(conn, v, fn, policy, probes):
        changed = any(policy.get(k) != base.get(k) for k in base if k != "updated_at")
        if probes is held:
            heldout_calls.append(policy)
            return _ev(0.2 if changed else 0.5)
        return _ev(0.9 if changed else 0.5)

    monkeypatch.setattr(rsi, "build_benchmark_suite", lambda c, v: train + held)
    monkeypatch.setattr(rsi, "split_benchmark_suite", lambda c, v, probes=None: (train, held))
    monkeypatch.setattr(rsi, "evaluate_retrieval_benchmark", fake_eval)
    res = rsi.tune_retrieval_policy(None, vault, lambda *a, **k: [])
    assert res["candidates_evaluated"] > 2
    assert len(heldout_calls) == 2  # baseline + the single winner; never during the search
    assert res["delta_j"] == 0.0 and res["heldout_delta_j"] == 0.0
    assert {k: res["policy"][k] for k in base if k != "updated_at"} == {k: base[k] for k in base if k != "updated_at"}


def test_readme_surface_accepts_nested_group_command(tmp_path: Path) -> None:
    """A new Typer group command (`app tenants rotate`) counts as documented when README shows the nested form."""
    import subprocess

    from sovereign_kb.readme_check import check

    repo = tmp_path / "svc"
    (repo / "src" / "svc").mkdir(parents=True)

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "pyproject.toml").write_text('[project]\nname = "svc"\n[project.scripts]\nsvc = "svc.cli:main"\n')
    cli = repo / "src" / "svc" / "cli.py"
    cli.write_text('@tenants_app.command("list")\ndef tenants_list():\n    pass\n')
    (repo / "README.md").write_text("# svc\n\n```bash\nsvc tenants list\n```\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")

    cli.write_text(cli.read_text() + '\n@tenants_app.command("rotate")\ndef tenants_rotate():\n    pass\n')
    git("commit", "-qam", "add rotate")
    issues, _ = check(repo, base, git("rev-parse", "HEAD"), runtime=False)
    assert any("new CLI command `rotate`" in i for i in issues)

    (repo / "README.md").write_text("# svc\n\n```bash\nsvc tenants list\nsvc tenants rotate acme\n```\n")
    git("commit", "-qam", "docs")
    issues, _ = check(repo, base, git("rev-parse", "HEAD"), runtime=False)
    assert not any("rotate" in i for i in issues)

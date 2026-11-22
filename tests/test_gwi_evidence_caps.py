"""F-13 regressions: GWI must not certify UI quality it never measured (2026-11-23 GWI hardening).

Covers: Python-generated HTML dashboards counted as rendered UI (UX lens cap), the strict
Release-Evidence (R) cap, stale Tri-Council approvals, probe-coverage weighting of lens
scores, the extracted ``gwi_lenses`` dispatch table, and the blockers-section verdict line.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sovereign_kb import gwi_lenses
from sovereign_kb.gwi import (
    CRAFT_LENSES,
    _discover_ui_files,
    evaluate_craft_lenses,
    evaluate_target,
    format_audit_report,
    lens_probe_coverage,
    triad_review_state,
)
from sovereign_kb.gwi_core import VERGA_UNMEASURED_CAP

_DASHBOARD_PY = '''"""Tiny dashboard that renders a full HTML document."""

PAGE = """<!DOCTYPE html>
<html lang="en"><head><style>:root { color-scheme: light dark; }</style></head>
<body><main aria-label="dashboard"><h1>Status</h1></main></body></html>
"""


def render() -> str:
    return PAGE
'''


def _python_dashboard_repo(root: Path) -> Path:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "dashboard.py").write_text(_DASHBOARD_PY, encoding="utf-8")
    (root / "pkg" / "core.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    return root


def _cli_only_repo(root: Path) -> Path:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "cli.py").write_text("def main() -> int:\n    return 0\n", encoding="utf-8")
    return root


def _lens(report: dict, lens_id: str) -> dict:
    return next(lr for lr in report["craft_lenses"]["lenses"] if lr["id"] == lens_id)


def test_discover_ui_files_includes_only_html_emitting_python_dashboards(tmp_path: Path) -> None:
    repo = _python_dashboard_repo(tmp_path / "repo")
    (repo / "pkg" / "dashboard_utils.py").write_text("def fmt(x: float) -> str:\n    return f'{x:.2f}'\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_dashboard.py").write_text("HTML = '<!DOCTYPE html>'\n")

    rendered, py_dashboards = _discover_ui_files(repo)

    assert rendered == [repo / "pkg" / "dashboard.py"]
    assert repo / "pkg" / "dashboard_utils.py" in py_dashboards


def test_python_html_dashboard_without_screenshots_is_capped_under_strict_lenses(tmp_path: Path) -> None:
    repo = _python_dashboard_repo(tmp_path / "repo")

    strict = evaluate_target(repo, profile="app", strict_lenses=True)
    ux = _lens(strict, "ux-ergonomics")
    assert any(f.startswith("UNMEASURED [ux.viewport_sheet]") for f in ux["probe_findings"])
    assert ux["unmeasured_probes"] >= 1
    assert ux["score"] <= VERGA_UNMEASURED_CAP

    lenient = evaluate_target(repo, profile="app", strict_lenses=False)
    assert _lens(lenient, "ux-ergonomics")["score"] > VERGA_UNMEASURED_CAP


def test_release_evidence_capped_only_for_rendered_ui_under_strict(tmp_path: Path) -> None:
    ui_repo = _python_dashboard_repo(tmp_path / "ui")
    cli_repo = _cli_only_repo(tmp_path / "cli")

    def r_dim(rep: dict) -> float:
        return next(d["score"] for d in rep["dimensions"] if d["id"] == "R")

    strict_ui = evaluate_target(ui_repo, profile="app", strict_lenses=True)
    assert r_dim(strict_ui) == pytest.approx(VERGA_UNMEASURED_CAP)
    assert any("GWI-APP-R05" in n for n in strict_ui["notes"])

    assert r_dim(evaluate_target(ui_repo, profile="app", strict_lenses=False)) == pytest.approx(9.4)
    assert r_dim(evaluate_target(cli_repo, profile="app", strict_lenses=True)) == pytest.approx(9.4)


def test_blockers_section_does_not_claim_release_ready_below_gate(tmp_path: Path) -> None:
    report = evaluate_target(_python_dashboard_repo(tmp_path / "repo"), profile="app", strict_lenses=True)
    assert report["calculation"]["status"] != "PASS"
    # Isolate the "0 blockers" branch: the fixture's missing-governance blockers would otherwise be listed.
    report["blockers"] = []
    report["craft_lenses"]["veto_triggered"] = False
    text = format_audit_report(report)
    assert "Ready for 9+ Sovereign Release" not in text
    assert "below the 9.0 gate" in text
    assert "○ UNMEASURED [GWI-APP-R05]" in text


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def test_triad_review_state_reports_never_current_and_stale(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("1")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "one")
    reviewed = _git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("2")
    _git(repo, "commit", "-qam", "two")
    head = _git(repo, "rev-parse", "HEAD")

    assert triad_review_state({}, head, repo)["state"] == "NEVER"

    approved = {"ever_audited": True, "disposition": "APPROVED", "commit_sha": reviewed[:7], "audited_at": "t"}
    current = triad_review_state(approved, reviewed, repo)
    assert current["state"] == "CURRENT" and current["label"].startswith("APPROVED @")

    stale = triad_review_state(approved, head, repo)
    assert stale["state"] == "STALE"
    assert stale["commits_since"] == 1
    assert stale["label"].startswith("STALE (+1 commits)")


def test_lens_probe_coverage_counts_measured_and_unmeasured() -> None:
    cov = lens_probe_coverage(["PASS [a]: x", "WARN [b]: y", "VETO [c]: z", "UNMEASURED [d]: w", "note"])
    assert cov == {"measured": 3, "unmeasured": 1, "ratio": 0.75}
    assert lens_probe_coverage([]) == {"measured": 0, "unmeasured": 0, "ratio": 0.0}


def test_lens_dispatch_covers_every_registered_lens() -> None:
    assert set(gwi_lenses.LENS_EVALUATORS) == set(CRAFT_LENSES)


@pytest.mark.parametrize(
    ("findings", "strict", "expected_drop"),
    [
        (["PASS [x.one]: ok"], True, 0.30),  # 1 measured probe -> 2 short of 3 -> -0.30
        (["PASS [x.a]: ok", "PASS [x.b]: ok", "PASS [x.c]: ok"], True, 0.0),
        (["PASS [x.one]: ok"], False, 0.0),  # weighting applies only under --strict-lenses
    ],
)
def test_thin_lens_evidence_is_penalised_under_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, findings: list[str], strict: bool, expected_drop: float
) -> None:
    def fake(ctx) -> None:  # noqa: ANN001 - SimpleNamespace context
        ctx.probe_findings.extend(findings)

    for lid in list(gwi_lenses.LENS_EVALUATORS):
        monkeypatch.setitem(gwi_lenses.LENS_EVALUATORS, lid, fake)
    repo = _cli_only_repo(tmp_path / "repo")
    report = {"profile": "app", "tiers": {"S_sub": 9.6, "S_perc": 9.6, "S_act": 9.6, "S_exist": 9.6}}

    out = evaluate_craft_lenses(repo, report, strict_lenses=strict)

    assert out["lenses"], "expected at least one active lens"
    for lr in out["lenses"]:
        assert lr["score"] == pytest.approx(9.6 - expected_drop)
        assert lr["probe_coverage"]["measured"] == len(findings)


def test_lens_with_zero_measured_probes_is_capped_under_strict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for lid in list(gwi_lenses.LENS_EVALUATORS):
        monkeypatch.setitem(gwi_lenses.LENS_EVALUATORS, lid, lambda ctx: None)
    repo = _cli_only_repo(tmp_path / "repo")
    report = {"profile": "app", "tiers": {"S_sub": 9.6, "S_perc": 9.6, "S_act": 9.6, "S_exist": 9.6}}

    out = evaluate_craft_lenses(repo, report, strict_lenses=True)

    assert all(lr["score"] == pytest.approx(VERGA_UNMEASURED_CAP) for lr in out["lenses"])

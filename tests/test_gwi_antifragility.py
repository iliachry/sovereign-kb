"""Tests for the `af.*` antifragility probes in the principal-verification lens."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from sovereign_kb import gwi_antifragility as af


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init(repo: Path) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    return repo


def _commit(repo: Path, msg: str, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


def _ctx(root: Path) -> SimpleNamespace:
    return SimpleNamespace(root=root, probe_findings=[], probe_measurements=[], score=9.5)


def _status(ctx: SimpleNamespace, pid: str) -> str:
    hits = [f for f in ctx.probe_findings if f"[{pid}]" in f]
    assert len(hits) == 1, ctx.probe_findings
    return hits[0].split(" ", 1)[0]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_x.py", True),
        ("pkg/test_x.py", True),
        ("src/a.test.ts", True),
        ("web/__tests__/a.js", True),
        ("x_test.go", True),
        ("src/gwi.py", False),
        ("src/contest.py", False),
    ],
)
def test_is_test_path(path: str, expected: bool) -> None:
    assert af.is_test_path(path) is expected


def test_failure_to_gate_counts_only_fix_commits(tmp_path: Path) -> None:
    repo = _init(tmp_path / "r")
    _commit(repo, "feat: start", {"src/a.py": "x = 1\n"})
    _commit(repo, "fix(a): with test", {"src/a.py": "x = 2\n", "tests/test_a.py": "def test_a():\n    assert 1\n"})
    _commit(repo, "fix: no test", {"src/a.py": "x = 3\n"})
    _commit(repo, "Fixed off-by-one", {"src/a.py": "x = 4\n"})
    _commit(repo, "docs: prefix not a fix", {"README.md": "fix things\n"})
    m = af.measure_failure_to_gate(repo)
    assert m["fix_commits"] == 3
    assert m["fix_commits_with_test"] == 1
    assert m["ratio"] == pytest.approx(1 / 3, abs=1e-4)
    assert len(m["uncovered"]) == 2


def test_failure_to_gate_warns_below_target_and_lowers_score(tmp_path: Path) -> None:
    repo = _init(tmp_path / "r")
    _commit(repo, "feat: start", {"src/a.py": "x = 1\n"})
    for i in range(3):
        _commit(repo, f"fix: patch {i}", {"src/a.py": f"x = {i + 10}\n"})
    ctx = _ctx(repo)
    af.apply_antifragility_probes(ctx)
    assert _status(ctx, "af.failure_to_gate") == "WARN"
    assert ctx.score < 9.5


def test_failure_to_gate_passes_when_fixes_carry_tests(tmp_path: Path) -> None:
    repo = _init(tmp_path / "r")
    _commit(repo, "feat: start", {"src/a.py": "x = 1\n"})
    for i in range(3):
        _commit(
            repo, f"fix: patch {i}", {"src/a.py": f"x = {i}\n", f"tests/test_{i}.py": "def test():\n    assert 1\n"}
        )
    ctx = _ctx(repo)
    af.apply_antifragility_probes(ctx)
    assert _status(ctx, "af.failure_to_gate") == "PASS"


def test_failure_to_gate_small_sample_is_info(tmp_path: Path) -> None:
    repo = _init(tmp_path / "r")
    _commit(repo, "fix: only one", {"src/a.py": "x = 1\n"})
    ctx = _ctx(repo)
    af.apply_antifragility_probes(ctx)
    assert _status(ctx, "af.failure_to_gate") == "INFO"
    assert not any(m["id"] == "af.failure_to_gate" for m in ctx.probe_measurements)


def test_rule_retirement_flags_monotone_accretion(tmp_path: Path) -> None:
    repo = _init(tmp_path / "r")
    _commit(repo, "feat: rules", {"src/rules.py": "R = ['GWI-ABC-01', 'GWI-ABC-02']\n"})
    ids = ", ".join(f"'GWI-ABC-{i:02d}'" for i in range(1, 9))
    _commit(repo, "feat: more rules", {"src/rules.py": f"R = [{ids}]\n"})
    m = af.measure_rule_retirement(repo)
    assert (m["added"], m["retired"], m["rule_ids_now"]) == (6, 0, 8)
    ctx = _ctx(repo)
    af.apply_antifragility_probes(ctx)
    assert _status(ctx, "af.rule_retirement") == "WARN"


def test_rule_retirement_passes_when_rules_are_retired(tmp_path: Path) -> None:
    repo = _init(tmp_path / "r")
    old = ", ".join(f"'GWI-OLD-{i:02d}'" for i in range(1, 4))
    _commit(repo, "feat: rules", {"src/rules.py": f"R = [{old}]\n"})
    new = ", ".join(f"'GWI-NEW-{i:02d}'" for i in range(1, 7))
    _commit(repo, "refactor: replace rules", {"src/rules.py": f"R = [{new}]\n"})
    m = af.measure_rule_retirement(repo)
    assert (m["added"], m["retired"]) == (6, 3)
    ctx = _ctx(repo)
    af.apply_antifragility_probes(ctx)
    assert _status(ctx, "af.rule_retirement") == "PASS"


def test_rule_ids_in_tests_are_ignored(tmp_path: Path) -> None:
    repo = _init(tmp_path / "r")
    ids = ", ".join(f"'GWI-ABC-{i:02d}'" for i in range(1, 9))
    _commit(repo, "test: fixtures", {"tests/test_rules.py": f"R = [{ids}]\n"})
    assert af.measure_rule_retirement(repo)["rule_ids_now"] == 0


def test_stress_evidence_detects_property_tests(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_p.py").write_text("from hypothesis import given\n", encoding="utf-8")
    m = af.measure_stress_evidence(tmp_path)
    assert m["property_fuzz_files"] == ["tests/test_p.py"]
    assert m["executed"] is False


def test_stress_evidence_detects_mutation_config(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_a():\n    assert 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[tool.mutmut]\npaths_to_mutate = ['src']\n", encoding="utf-8")
    assert af.measure_stress_evidence(tmp_path)["mutation_evidence"] == ["pyproject.toml mutmut config"]


def test_stress_evidence_warns_on_example_only_suite(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_a():\n    assert 1\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    af.apply_antifragility_probes(ctx)
    assert _status(ctx, "af.stress_evidence") == "WARN"
    assert ctx.score == pytest.approx(9.5 - af.PENALTY_NO_STRESS_EVIDENCE)


def _barbell_project(root: Path, core_src: str) -> None:
    pkg = root / "src" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text(core_src, encoding="utf-8")
    (pkg / "lab.py").write_text("X = 1\n", encoding="utf-8")
    (pkg / "lab_extra.py").write_text("Y = 1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[tool.gwi.barbell]\ncore = ["core"]\nexperimental = ["lab"]\n', encoding="utf-8"
    )


def test_barbell_detects_lazy_core_to_experimental_import(tmp_path: Path) -> None:
    _barbell_project(tmp_path, "def f():\n    from pkg.lab import X\n    return X\n")
    m = af.measure_barbell_isolation(tmp_path)
    assert m["leaks"] == ["core -> lab"]
    ctx = _ctx(tmp_path)
    af.apply_antifragility_probes(ctx)
    assert _status(ctx, "af.barbell_isolation") == "WARN"


def test_barbell_does_not_confuse_name_prefixes(tmp_path: Path) -> None:
    _barbell_project(tmp_path, "from pkg.lab_extra import Y\n")
    assert af.measure_barbell_isolation(tmp_path)["leaks"] == []
    ctx = _ctx(tmp_path)
    af.apply_antifragility_probes(ctx)
    assert _status(ctx, "af.barbell_isolation") == "PASS"


def test_barbell_undeclared_is_info(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    af.apply_antifragility_probes(ctx)
    assert _status(ctx, "af.barbell_isolation") == "INFO"


def test_no_git_history_never_credits_or_penalizes_git_probes(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    af.apply_antifragility_probes(ctx)
    assert _status(ctx, "af.failure_to_gate") == "INFO"
    assert _status(ctx, "af.rule_retirement") == "INFO"
    assert ctx.score == 9.5


def test_failure_to_gate_credits_backfilled_regression_trailer(tmp_path: Path) -> None:
    repo = _init(tmp_path / "r")
    _commit(repo, "feat: start", {"src/a.py": "x = 1\n"})
    _commit(repo, "fix: patch without test", {"src/a.py": "x = 2\n"})
    fix_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--short=9", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    _commit(repo, "fix: other patch", {"src/a.py": "x = 3\n"})
    _commit(
        repo,
        f"test: backfill regression\n\n{af.REGRESSION_TRAILER}: {fix_sha}",
        {"tests/test_a.py": "def test_a():\n    assert 1\n"},
    )
    m = af.measure_failure_to_gate(repo)
    assert (m["fix_commits"], m["covered_same_commit"], m["covered_by_backfill"]) == (2, 0, 1)


def test_failure_to_gate_ignores_trailer_on_commit_without_tests(tmp_path: Path) -> None:
    repo = _init(tmp_path / "r")
    _commit(repo, "fix: patch without test", {"src/a.py": "x = 2\n"})
    fix_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    _commit(repo, f"docs: claim coverage\n\n{af.REGRESSION_TRAILER}: {fix_sha}", {"README.md": "x\n"})
    assert af.measure_failure_to_gate(repo)["covered_by_backfill"] == 0


def test_rule_ids_match_whole_tokens_only(tmp_path: Path) -> None:
    repo = _init(tmp_path / "r")
    _commit(
        repo,
        "feat: ids",
        {"src/ids.py": "A = 'GWI-ABC-01'\nB = f'SKB-RUST-2026-{x}'\nC = 'xGWI-ABC-02'\nD = 'GWI-ABC-03x'\n"},
    )
    assert af._rule_ids_at(repo, "HEAD") == {"GWI-ABC-01"}


def test_rule_added_then_removed_inside_window_counts_as_retired(tmp_path: Path) -> None:
    repo = _init(tmp_path / "r")
    base = ", ".join(f"'GWI-ABC-{i:02d}'" for i in range(1, 6))
    _commit(repo, "feat: rules", {"src/rules.py": f"R = [{base}]\n"})
    _commit(repo, "feat: alias", {"src/rules.py": f"R = [{base}, 'GWI-DUP-01']\n"})
    more = ", ".join(f"'GWI-NEW-{i:02d}'" for i in range(1, 6))
    _commit(repo, "refactor: drop alias", {"src/rules.py": f"R = [{base}, {more}]\n"})
    m = af.measure_rule_retirement(repo)
    assert (m["added"], m["retired"]) == (5, 1)
    assert m["retired_sample"] == ["GWI-DUP-01"]


def test_barbell_minimal_parser_matches_tomllib() -> None:
    text = (
        '[project]\nname = "x"\n\n[tool.gwi.barbell]\n# comment\n'
        'core = ["a", "b"]  # trailing\nexperimental = [\n  "lab",\n]\n\n[tool.ruff]\nline-length = 1\n'
    )
    assert af._parse_barbell_minimal(text) == {"core": ["a", "b"], "experimental": ["lab"]}
    assert af._parse_barbell_minimal("[tool.ruff]\nx = 1\n") is None

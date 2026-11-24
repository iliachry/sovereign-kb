"""Regression tests for GWI source walking and static test counting (2026-11-22 bug hunt)."""

from __future__ import annotations

from pathlib import Path

from sovereign_kb.gwi import _is_test_file, _iter_source_files
from sovereign_kb.gwi_science import _count_python_tests, _static_test_count


def test_walker_ignores_only_dirs_below_root_not_absolute_prefix(tmp_path: Path) -> None:
    # A repo checked out under an ignored-name ancestor (`build/`, `Library/`, `tests/`) must still be scanned.
    for ancestor in ("build", "Library", "tests"):
        repo = tmp_path / ancestor / "app"
        (repo / "src").mkdir(parents=True)
        (repo / "node_modules" / "dep").mkdir(parents=True)
        (repo / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
        (repo / "node_modules" / "dep" / "index.py").write_text("y = 2\n", encoding="utf-8")

        files = _iter_source_files(repo, (".py",))
        assert files == [repo / "src" / "main.py"], ancestor
        # The ancestor named `tests` must not turn every source file into a test file.
        assert not _is_test_file(repo / "src" / "main.py", repo)
        assert _is_test_file(repo / "tests" / "test_x.py", repo)


def test_static_python_test_count_ignores_string_literal_fixtures(tmp_path: Path) -> None:
    src = '''
import textwrap

FIXTURE = """
def test_embedded_in_string() -> None:
    pass
"""

def test_real_one() -> None:
    def test_nested_helper() -> None:
        pass
    assert True

class TestGroup:
    def test_method(self) -> None:
        assert True

class Helper:
    def test_not_collected(self) -> None:
        pass
'''
    assert _count_python_tests(src) == 2
    # Unparseable files fall back to the line regex rather than counting 0.
    assert _count_python_tests("def test_a(:\n") == 1

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_mod.py").write_text(src, encoding="utf-8")
    assert _static_test_count(tmp_path)["python"] == 2


def test_empty_source_scan_is_a_p0_blocker_and_fails(tmp_path: Path) -> None:
    from sovereign_kb.gwi import evaluate_app_profile

    # A docs-only tree (or one whose code all lives in ignored dirs) must never certify.
    empty = tmp_path / "empty"
    (empty / "node_modules" / "dep").mkdir(parents=True)
    (empty / "node_modules" / "dep" / "index.js").write_text("module.exports = 1\n", encoding="utf-8")
    (empty / "README.md").write_text("# empty\n", encoding="utf-8")
    rep = evaluate_app_profile(empty)
    assert "GWI-APP-E01" in [b["id"] for b in rep["blockers"]]
    assert rep["probe_states"]["S"] == "FAIL"
    assert rep["tiers"]["S_sub"] < 7.0

    code = tmp_path / "code"
    (code / "src").mkdir(parents=True)
    (code / "src" / "main.go").write_text("package main\n", encoding="utf-8")
    rep2 = evaluate_app_profile(code)
    assert "GWI-APP-E01" not in [b["id"] for b in rep2["blockers"]]


def test_video_byte_count_reads_a_project_stored_under_node_modules(tmp_path: Path) -> None:
    from sovereign_kb.gwi import evaluate_video_profile

    # The byte count behind Kolmogorov elegance weighs the project's own files even when the path that leads to
    # the project contains `node_modules`, and still leaves out `node_modules/` and `.git/` inside the project.
    kit = tmp_path / "node_modules" / "pkg" / "video-kit"
    (kit / "node_modules" / "dep").mkdir(parents=True)
    (kit / ".git").mkdir()
    (kit / "master.mp4").write_bytes(b"\x00" * 1000)
    (kit / "notes.txt").write_bytes(b"\x00" * 2000)
    (kit / "node_modules" / "dep" / "blob.bin").write_bytes(b"\x00" * 50_000)
    (kit / ".git" / "pack.bin").write_bytes(b"\x00" * 50_000)

    rep = evaluate_video_profile(kit)
    # 3000 project bytes against an essential 1250 (1.25 x the 1000-byte master): exp(-(3000 - 1250) / 1250).
    assert rep["kolmogorov_elegance"] == 0.2466


def test_evidence_fingerprint_reads_a_project_stored_under_build(tmp_path: Path) -> None:
    from sovereign_kb.audit_registry import compute_subproject_fingerprint

    def evidence_sha(root: Path) -> str:
        return str(compute_subproject_fingerprint(root, ".")["evidence_sha"])

    # Stored under `build/`, new evidence must still change the fingerprint (it was the hash of nothing).
    repo = tmp_path / "build" / "app"
    report = repo / "evidence" / "playwright" / "report.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"consoleErrors": 0}', encoding="utf-8")
    clean_run = evidence_sha(repo)
    report.write_text('{"consoleErrors": 7}', encoding="utf-8")
    assert evidence_sha(repo) != clean_run

    # `node_modules/` inside the project still stays out of the fingerprint.
    own_evidence_only = evidence_sha(repo)
    (repo / "evidence" / "node_modules" / "dep").mkdir(parents=True)
    (repo / "evidence" / "node_modules" / "dep" / "package.json").write_text("{}", encoding="utf-8")
    assert evidence_sha(repo) == own_evidence_only


def test_readme_reference_probe_reads_a_project_stored_under_dot_git(tmp_path: Path) -> None:
    from sovereign_kb.readme_check import reference_issues_worktree

    def make(root: Path, readme: str) -> Path:
        (root / "src").mkdir(parents=True)
        (root / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
        (root / "README.md").write_text(readme, encoding="utf-8")
        return root

    # A non-git project whose path contains `.git` still has its files listed (every path was reported dead).
    stored = make(tmp_path / ".git" / "app", "# app\n\nThe code is in `src/main.py`.\n")
    assert reference_issues_worktree(stored) == []

    # A `.git/` directory inside the project is still not something the README may point at.
    plain = make(tmp_path / "app", "# app\n\nSee the [hook](.git/hooks/pre-push) and `src/main.py`.\n")
    (plain / ".git" / "hooks").mkdir(parents=True)
    (plain / ".git" / "hooks" / "pre-push").write_text("#!/bin/sh\n", encoding="utf-8")
    issues = reference_issues_worktree(plain)
    assert len(issues) == 1
    assert ".git/hooks/pre-push" in issues[0]

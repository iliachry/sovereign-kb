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

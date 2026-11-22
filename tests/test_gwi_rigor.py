"""Tests for interpreter selection in the GWI-SCI-06 mutation probe (`gwi_rigor`)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sovereign_kb import gwi_rigor


def test_select_test_python_prefers_target_venv(tmp_path: Path) -> None:
    venv_py = tmp_path / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("", encoding="utf-8")
    assert gwi_rigor.select_test_python(tmp_path) == str(venv_py)


def test_select_test_python_skips_interpreters_without_pytest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gwi_rigor.shutil, "which", lambda name: "/no-pytest/python3")
    monkeypatch.setattr(gwi_rigor, "_has_pytest", lambda py: py == sys.executable)
    assert gwi_rigor.select_test_python(tmp_path) == sys.executable

    monkeypatch.setattr(gwi_rigor, "_has_pytest", lambda py: False)
    assert gwi_rigor.select_test_python(tmp_path) is None


def test_mutation_probe_reports_missing_pytest_instead_of_baseline_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "mod.py").write_text("def f(a, b):\n    return a + b if a > b else a - b\n", encoding="utf-8")
    monkeypatch.setattr(gwi_rigor, "select_test_python", lambda root: None)
    res = gwi_rigor.run_ast_mutation_probe(tmp_path, target_rel_path="mod.py", max_mutants=1)
    assert res["status"] == "PYTEST_UNAVAILABLE"
    assert res["passed"] is False

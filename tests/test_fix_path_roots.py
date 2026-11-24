"""Regression tests for walkers outside the audit that judged ignored names on the absolute path."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from sovereign_kb.cli import main
from sovereign_kb.evr import EvrBuilder


def test_evr_add_path_packs_a_directory_stored_under_an_ignored_name(tmp_path: Path) -> None:
    # Packing a directory whose own path contains `node_modules` (or `.venv`, `.git`, ...) packed nothing.
    pkg = tmp_path / "node_modules" / "pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "a.txt").write_text("a", encoding="utf-8")
    (pkg / "sub" / "b.txt").write_text("b", encoding="utf-8")
    # Ignored names inside the packed directory still stay out.
    (pkg / "node_modules" / "dep").mkdir(parents=True)
    (pkg / "node_modules" / "dep" / "c.txt").write_text("c", encoding="utf-8")
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__" / "m.pyc").write_bytes(b"\x00")

    builder = EvrBuilder(title="Stored under node_modules", polyglot=False)
    builder.add_path(pkg)

    assert sorted(spec.path for spec in builder._specs) == ["a.txt", "sub/b.txt"]


def test_arxiv_packager_bundles_a_paper_stored_under_dot_venv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The generated `scripts/package_arxiv.py` bundled nothing when the paper's own path contained `.venv` or `.git`.
    paper = tmp_path / ".venv" / "paper"
    main(["scaffold", "paper-ieee-arxiv", str(paper), "--name", "paper", "--author", "Test Researcher"])
    capsys.readouterr()
    # `.venv/` inside the paper still stays out of the bundle.
    (paper / ".venv").mkdir()
    (paper / ".venv" / "vendored.tex").write_text("% not part of the paper\n", encoding="utf-8")

    subprocess.run([sys.executable, str(paper / "scripts" / "package_arxiv.py")], check=True, capture_output=True)

    with zipfile.ZipFile(paper / "overleaf_arxiv_bundle.zip") as bundle:
        names = bundle.namelist()
    assert "paper.tex" in names
    assert [name for name in names if name.startswith(".venv/")] == []

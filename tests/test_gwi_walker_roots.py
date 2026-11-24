"""F-13: GWI file walkers judge ignored and test directories relative to the audited root.

Regression for the 2026-11-22 incident: a project kept under `~/Library/Application Support/` scored 0.32/10 while
a byte-identical copy elsewhere scored 9.64. The walkers matched their directory rules against absolute path parts,
so the place a project was stored decided what the audit read.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from sovereign_kb.audit_registry import compute_subproject_fingerprint
from sovereign_kb.gwi import evaluate_app_profile, evaluate_target, evaluate_video_profile
from sovereign_kb.gwi.utils import _is_test_file, _iter_source_files
from sovereign_kb.readme_check import reference_issues_worktree

# Where the incident project lived: `Library` and `build` are both names the source walker ignores.
UNDER_IGNORED_NAMES = ("Library", "Application Support", "build")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_bytes(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size)


def _below(root: Path, paths: list[Path]) -> list[str]:
    return [p.relative_to(root).as_posix() for p in paths]


def _blocker_ids(report: dict[str, Any]) -> set[str]:
    return {b["id"] for b in report["blockers"]}


def _passing_app(root: Path) -> Path:
    """A one-feature app that passes the `app` profile: catalog entry, source, a test that names it, and a gate."""
    _write(root / "AGENTS.md", "# proj\n\n- `F-01`: Greeting (`src/greeting.py`)\n")
    _write(root / "README.md", "# proj\n\nA one-function project for checking that an audit ignores its location.\n")
    _write(root / "src" / "greeting.py", 'def greet(name: str) -> str:\n    return f"hello {name}"\n')
    _write(root / "tests" / "test_greeting.py", "def test_f01_greets_by_name():\n    # Covers F-01\n    assert True\n")
    _write(root / "scripts" / "quality_gate.sh", "#!/usr/bin/env bash\nset -euo pipefail\npytest -q\n")
    return root


def _verdict(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "goldwood_g": report["calculation"]["goldwood_g"],
        "status": report["calculation"]["status"],
        "blockers": sorted(_blocker_ids(report)),
        "coupled_goldwood_g": report["craft_lenses"]["coupled_goldwood_g"],
    }


@pytest.mark.parametrize(
    "location",
    [(*UNDER_IGNORED_NAMES, "proj"), ("corpus",)],
    ids=["stored-under-Library-and-build", "root-itself-named-corpus"],
)
def test_root_yields_its_own_files_whatever_its_location_is_called(tmp_path: Path, location: tuple[str, ...]) -> None:
    """F-13: Ignored names in the path that leads to the root, or as the root's own name, hide nothing inside it."""
    root = tmp_path.joinpath(*location)
    _write(root / "src" / "app.py", "VALUE = 1\n")
    _write(root / "tests" / "test_app.py", "def test_value():\n    assert True\n")

    found = _iter_source_files(root, (".py",))

    assert _below(root, found) == ["src/app.py", "tests/test_app.py"]


@pytest.mark.parametrize("location", [(), UNDER_IGNORED_NAMES], ids=["plain", "under-Library-and-build"])
def test_ignored_directories_inside_the_root_stay_ignored(tmp_path: Path, location: tuple[str, ...]) -> None:
    """F-13: `Library/`, `node_modules/` and `build/` below the root are skipped wherever the root is stored."""
    root = tmp_path.joinpath(*location) / "proj"
    _write(root / "src" / "app.py", "VALUE = 1\n")
    _write(root / "Library" / "PackageCache" / "vendored.py", "VALUE = 2\n")
    _write(root / "node_modules" / "pkg" / "setup.py", "VALUE = 3\n")
    _write(root / "src" / "build" / "generated.py", "VALUE = 4\n")

    found = _iter_source_files(root, (".py",))

    assert _below(root, found) == ["src/app.py"]


@pytest.mark.parametrize(
    "location",
    [("tests", "proj"), ("test", "proj"), ("__tests__", "proj"), ("e2e", "proj"), ("tests",)],
    ids=["under-tests", "under-test", "under-__tests__", "under-e2e", "root-itself-named-tests"],
)
def test_file_is_a_test_only_by_its_path_below_the_root(tmp_path: Path, location: tuple[str, ...]) -> None:
    """F-13: A root stored under, or named like, a test directory does not turn every file into a test."""
    root = tmp_path.joinpath(*location)

    assert _is_test_file(root / "src" / "app.py", root) is False
    assert _is_test_file(root / "tests" / "helpers.py", root) is True
    assert _is_test_file(root / "src" / "app.spec.ts", root) is True


def test_sources_are_not_credited_as_tests_when_root_is_stored_under_tests(tmp_path: Path) -> None:
    """F-13: A project with no test files earns no catalog-coverage credit from being kept under `tests/`."""
    root = tmp_path / "tests" / "proj"
    _write(root / "AGENTS.md", "# proj\n\n- `F-01`: Health check (`src/health.py`)\n")
    _write(root / "README.md", "# proj\n\nA project that ships a health check and no tests at all.\n")
    _write(root / "src" / "health.py", "# F-01\ndef test_connection() -> bool:\n    assert True\n    return True\n")

    report = evaluate_app_profile(root)

    assert "GWI-APP-U02" in _blocker_ids(report)


def test_unseeded_random_is_reported_when_root_is_stored_under_tests(tmp_path: Path) -> None:
    """F-13: The determinism probe still reads the non-test sources of a project kept under `tests/`."""
    root = _passing_app(tmp_path / "tests" / "proj")
    _write(root / "src" / "dice.js", "export const roll = () => Math.random();\n")

    report = evaluate_app_profile(root)

    assert "GWI-APP-D01" in _blocker_ids(report)


def test_hardcoded_secret_is_reported_when_root_is_stored_under_tests(tmp_path: Path) -> None:
    """F-13: The profile's secret scan and the secret-hygiene lens still read sources of a project under `tests/`."""
    root = _passing_app(tmp_path / "tests" / "proj")
    token = "AKIA" + "A" * 16  # joined at run time: this file must never hold a token-shaped literal
    _write(root / "src" / "settings.py", f'AWS_ACCESS_KEY_ID = "{token}"\n')

    report = evaluate_target(root, profile="app")

    assert "GWI-APP-R03" in _blocker_ids(report)
    lens = next(lens for lens in report["craft_lenses"]["lenses"] if lens["id"] == "principal-verification")
    assert any(finding.startswith("VETO [pv.secret_hygiene]") for finding in lens["probe_findings"])


def test_identical_project_gets_the_same_verdict_wherever_it_is_stored(tmp_path: Path) -> None:
    """F-13: The incident itself: byte-identical files score the same under `Library/.../build/` as elsewhere."""
    plain = _passing_app(tmp_path / "plain" / "proj")
    moved = tmp_path.joinpath(*UNDER_IGNORED_NAMES) / "proj"
    shutil.copytree(plain, moved)

    plain_verdict = _verdict(evaluate_target(plain, profile="app"))
    moved_verdict = _verdict(evaluate_target(moved, profile="app"))

    assert plain_verdict["status"] == "PASS"
    assert moved_verdict == plain_verdict


def test_video_project_bytes_are_counted_when_root_is_stored_under_node_modules(tmp_path: Path) -> None:
    """F-13: Kolmogorov elegance weighs the project's own files even when the root sits under `node_modules/`."""
    root = tmp_path / "node_modules" / "pkg" / "video-kit"
    _write_bytes(root / "master.mp4", 1000)
    _write_bytes(root / "notes.txt", 2000)
    _write_bytes(root / "node_modules" / "dep" / "blob.bin", 50_000)
    _write_bytes(root / ".git" / "pack.bin", 50_000)

    report = evaluate_video_profile(root)

    # 3000 project bytes against an essential 1250 (1.25 x the 1000-byte master): exp(-(3000 - 1250) / 1250).
    assert report["kolmogorov_elegance"] == 0.2466


def _evidence_sha(root: Path) -> str:
    return str(compute_subproject_fingerprint(root, ".")["evidence_sha"])


def test_evidence_fingerprint_follows_evidence_when_root_is_stored_under_build(tmp_path: Path) -> None:
    """F-13: The audit-cache fingerprint still reads a project's evidence when the project is kept under `build/`."""
    root = tmp_path / "build" / "proj"
    report = root / "evidence" / "playwright" / "report.json"
    _write(report, '{"consoleErrors": 0}')
    clean_run = _evidence_sha(root)
    _write(report, '{"consoleErrors": 7}')

    assert _evidence_sha(root) != clean_run


def test_evidence_fingerprint_skips_node_modules_inside_the_root(tmp_path: Path) -> None:
    """F-13: Evidence-shaped files below an in-root `node_modules/` stay out of the fingerprint."""
    root = tmp_path / "proj"
    _write(root / "evidence" / "playwright" / "report.json", '{"consoleErrors": 0}')
    own_evidence_only = _evidence_sha(root)
    _write(root / "evidence" / "node_modules" / "dep" / "package.json", "{}")

    assert _evidence_sha(root) == own_evidence_only


def test_readme_references_resolve_when_root_is_stored_under_dot_git(tmp_path: Path) -> None:
    """F-13: The README reference probe still sees a non-git project's files when it is kept under `.git/`."""
    root = _passing_app(tmp_path / ".git" / "proj")
    _write(root / "README.md", "# proj\n\nRun `scripts/quality_gate.sh`; the code is in `src/greeting.py`.\n")

    assert reference_issues_worktree(root) == []


def test_readme_reference_into_an_in_root_dot_git_stays_dead(tmp_path: Path) -> None:
    """F-13: A `.git/` directory inside the root is still not part of the project the README may point at."""
    root = _passing_app(tmp_path / "proj")
    _write(root / ".git" / "hooks" / "pre-push", "#!/bin/sh\n")
    _write(root / "README.md", "# proj\n\nSee the [hook](.git/hooks/pre-push) and `src/greeting.py`.\n")

    issues = reference_issues_worktree(root)

    assert len(issues) == 1
    assert ".git/hooks/pre-push" in issues[0]

"""F-21: a copy-installed `sovereign-kb` must say when its source checkout has moved on (Gate 0).

`uv tool install <checkout>` (without `--editable`) copies the package into the tool's site-packages. Commits to
the checkout after that change nothing the installed `skb`, `gwi`, `organon` and `evr` run until someone
reinstalls. On 2026-11-24 one such copy ran six-day-old code for every `kb gwi audit` on a Mac, and nothing said so.
"""

from __future__ import annotations

import functools
import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sovereign_kb import install_check
from sovereign_kb.install_check import FIX_COMMAND, banner_line, inspect_install

GATE_0 = "Gate 0 (Tool Install)"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ],
        check=True,
        capture_output=True,
    )


def _checkout(tmp_path: Path) -> Path:
    """A committed source checkout with the package at `src/sovereign_kb`, as in this repository."""
    root = tmp_path / "checkout"
    pkg = root / "src" / "sovereign_kb"
    (pkg / "cli").mkdir(parents=True)
    (pkg / "__init__.py").write_text('__version__ = "1.4.0"\n', encoding="utf-8")
    (pkg / "cli" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "cli" / "verify.py").write_text("GATES = 6\n", encoding="utf-8")
    (pkg / "gwi.py").write_text("THRESHOLD = 9.0\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "sovereign-kb"\n', encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def _install(tmp_path: Path, checkout: Path, *, editable: bool = False) -> tuple[Path, Path, Path]:
    """Lay out what `uv tool install [--editable] <checkout>` leaves behind: (package_dir, dist_info, prefix)."""
    prefix = tmp_path / "tools" / "sovereign-kb"
    site = prefix / "lib" / "python3.12" / "site-packages"
    info = site / "sovereign_kb-1.4.0.dist-info"
    info.mkdir(parents=True)
    (info / "INSTALLER").write_text("uv\n", encoding="utf-8")
    direct_url = {"url": checkout.as_uri(), "dir_info": {"editable": editable}}
    (info / "direct_url.json").write_text(json.dumps(direct_url), encoding="utf-8")
    (prefix / "uv-receipt.toml").write_text("[tool]\n", encoding="utf-8")
    if editable:
        # An editable install runs the checkout's own files; only the metadata sits in site-packages.
        return checkout / "src" / "sovereign_kb", info, prefix
    shutil.copytree(checkout / "src" / "sovereign_kb", site / "sovereign_kb")
    return site / "sovereign_kb", info, prefix


def _use_layout(monkeypatch: pytest.MonkeyPatch, package_dir: Path, dist_info: Path, prefix: Path) -> None:
    """Make every caller of `install_check.inspect_install()` (verify, banner, --version) see this layout."""
    monkeypatch.setattr(
        install_check,
        "inspect_install",
        functools.partial(inspect_install, package_dir, dist_info=dist_info, prefix=prefix),
    )


def test_stale_copy_names_source_file_count_and_fix(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    pkg, info, prefix = _install(tmp_path, checkout)
    (checkout / "src" / "sovereign_kb" / "cli" / "verify.py").write_text("GATES = 7\n", encoding="utf-8")
    (checkout / "src" / "sovereign_kb" / "install_check.py").write_text("FRESH = True\n", encoding="utf-8")
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-qm", "move on")

    report = inspect_install(pkg, dist_info=info, prefix=prefix)

    assert report["status"] == "stale"
    assert report["source"] == str(checkout)
    assert report["stale_files"] == ["cli/verify.py", "install_check.py"]
    assert report["uncommitted_files"] == []
    assert report["fix"] == FIX_COMMAND == "uv tool upgrade sovereign-kb --reinstall"
    line = banner_line(report)
    assert line is not None
    assert line.startswith("sovereign-kb: STALE INSTALL")
    assert str(checkout) in line
    assert "2 Python files" in line
    assert line.endswith(f"run: {FIX_COMMAND}")


def test_identical_copy_is_fresh_and_silent(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    pkg, info, prefix = _install(tmp_path, checkout)

    report = inspect_install(pkg, dist_info=info, prefix=prefix)

    assert report["status"] == "fresh"
    assert report["source"] == str(checkout)
    assert report["stale_files"] == [] and report["uncommitted_files"] == []
    assert report["fix"] is None
    assert banner_line(report) is None


def test_editable_install_is_silent_however_far_the_checkout_moves(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    pkg, info, prefix = _install(tmp_path, checkout, editable=True)
    (checkout / "src" / "sovereign_kb" / "gwi.py").write_text("THRESHOLD = 9.5\n", encoding="utf-8")
    _git(checkout, "commit", "-qam", "move on")

    report = inspect_install(pkg, dist_info=info, prefix=prefix)

    assert report["status"] == "editable"
    assert report["source"] == str(checkout)
    assert report["fix"] is None
    assert banner_line(report) is None


def test_missing_source_directory_is_unverifiable_not_fresh(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    pkg, info, prefix = _install(tmp_path, checkout)
    shutil.rmtree(checkout)

    report = inspect_install(pkg, dist_info=info, prefix=prefix)

    assert report["status"] == "unverifiable"
    assert str(checkout) in report["message"]
    assert "no longer exists" in report["message"]
    line = banner_line(report)
    assert line is not None and line.startswith("sovereign-kb: UNVERIFIED INSTALL")


def test_uncommitted_edits_only_warn_until_they_are_committed(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    pkg, info, prefix = _install(tmp_path, checkout)
    # Another session's work in progress: one edited module and one new, untracked module.
    (checkout / "src" / "sovereign_kb" / "gwi.py").write_text("THRESHOLD = 9.5\n", encoding="utf-8")
    (checkout / "src" / "sovereign_kb" / "scratch.py").write_text("x = 1\n", encoding="utf-8")

    report = inspect_install(pkg, dist_info=info, prefix=prefix)

    assert report["status"] == "uncommitted"
    assert report["uncommitted_files"] == ["gwi.py", "scratch.py"]
    assert report["stale_files"] == []
    assert banner_line(report) is None

    _git(checkout, "commit", "-qam", "work in progress lands")
    report = inspect_install(pkg, dist_info=info, prefix=prefix)

    assert report["status"] == "stale"
    assert report["stale_files"] == ["gwi.py"]
    assert report["uncommitted_files"] == ["scratch.py"]


def test_copy_installed_with_uncommitted_edits_is_fresh_while_the_checkout_still_holds_them(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    (checkout / "src" / "sovereign_kb" / "gwi.py").write_text("THRESHOLD = 9.5\n", encoding="utf-8")
    pkg, info, prefix = _install(tmp_path, checkout)

    # Reinstalling now would install the same bytes, so the fix command could not change anything.
    assert inspect_install(pkg, dist_info=info, prefix=prefix)["status"] == "fresh"

    _git(checkout, "checkout", "--", "src/sovereign_kb/gwi.py")
    report = inspect_install(pkg, dist_info=info, prefix=prefix)

    assert report["status"] == "stale"
    assert report["stale_files"] == ["gwi.py"]


def test_a_file_git_ignores_is_no_difference(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    (checkout / ".gitignore").write_text("src/sovereign_kb/local_settings.py\n", encoding="utf-8")
    _git(checkout, "add", ".gitignore")
    _git(checkout, "commit", "-qm", "ignore local settings")
    pkg, info, prefix = _install(tmp_path, checkout)
    # The build leaves ignored files out, so no reinstall could ever bring this one into the copy.
    (checkout / "src" / "sovereign_kb" / "local_settings.py").write_text("DEBUG = True\n", encoding="utf-8")

    assert inspect_install(pkg, dist_info=info, prefix=prefix)["status"] == "fresh"


def test_without_git_every_difference_counts_as_stale(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    pkg, info, prefix = _install(tmp_path, checkout)
    shutil.rmtree(checkout / ".git")
    (checkout / "src" / "sovereign_kb" / "gwi.py").write_text("THRESHOLD = 9.5\n", encoding="utf-8")

    report = inspect_install(pkg, dist_info=info, prefix=prefix)

    assert report["status"] == "stale"
    assert report["stale_files"] == ["gwi.py"]


def test_verify_gate_zero_fails_on_a_stale_copy_and_warns_on_uncommitted_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from sovereign_kb.cli import main

    monkeypatch.setenv("KB_NO_EMBED", "1")
    vault = tmp_path / "vault"
    main(["init", str(vault), "--no-embed"])
    capsys.readouterr()
    checkout = _checkout(tmp_path)
    _use_layout(monkeypatch, *_install(tmp_path, checkout))
    verify = ["--vault", str(vault), "verify", "--allow-lexical-only"]

    main([*verify, "--json"])
    fresh = json.loads(capsys.readouterr().out)
    assert fresh["status"] == "PASS", fresh["violations"]
    assert fresh["install"]["status"] == "fresh"
    assert fresh["warnings"] == []

    (checkout / "src" / "sovereign_kb" / "gwi.py").write_text("THRESHOLD = 9.5\n", encoding="utf-8")
    main([*verify, "--json"])
    uncommitted = json.loads(capsys.readouterr().out)
    assert uncommitted["status"] == "PASS", uncommitted["violations"]
    assert [w["gate"] for w in uncommitted["warnings"]] == [GATE_0]

    _git(checkout, "commit", "-qam", "move on")
    with pytest.raises(SystemExit) as exc:
        main([*verify, "--json"])
    assert exc.value.code == 1
    stale = json.loads(capsys.readouterr().out)
    assert stale["status"] == "FAIL"
    gate0 = [v for v in stale["violations"] if v["gate"] == GATE_0]
    assert len(gate0) == 1
    assert gate0[0]["file"] == str(checkout)
    assert FIX_COMMAND in gate0[0]["issue"]
    assert stale["violations_count"] == len(stale["violations"])

    with pytest.raises(SystemExit):
        main(verify)
    human = capsys.readouterr().out
    assert f"  {GATE_0}" in human and "FAIL" in human.split(GATE_0, 1)[1].splitlines()[0]
    assert FIX_COMMAND in human


@pytest.mark.parametrize(
    ("module", "func", "prog"),
    [
        ("sovereign_kb.cli", "main", "skb"),
        ("sovereign_kb.gwi", "main_cli", "gwi"),
        ("sovereign_kb.organon", "main_cli", "organon"),
        ("sovereign_kb.evr", "main_cli", "evr"),
    ],
)
def test_every_entry_point_prints_one_stderr_line_for_a_stale_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    module: str,
    func: str,
    prog: str,
) -> None:
    entry = getattr(importlib.import_module(module), func)
    checkout = _checkout(tmp_path)
    _use_layout(monkeypatch, *_install(tmp_path, checkout))
    monkeypatch.setattr(sys, "argv", [prog, "--help"])

    with pytest.raises(SystemExit):
        entry()
    assert "STALE" not in capsys.readouterr().err  # fresh: nothing to say

    (checkout / "src" / "sovereign_kb" / "gwi.py").write_text("THRESHOLD = 9.5\n", encoding="utf-8")
    _git(checkout, "commit", "-qam", "move on")
    with pytest.raises(SystemExit):
        entry()
    err = capsys.readouterr().err
    assert err.splitlines()[0].startswith("sovereign-kb: STALE INSTALL")
    assert err.count("STALE INSTALL") == 1
    assert FIX_COMMAND in err

    # A programmatic call with explicit arguments is not a command-line run: no banner.
    with pytest.raises(SystemExit):
        entry(["--help"])
    assert "STALE INSTALL" not in capsys.readouterr().err


def test_version_reports_the_install_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from sovereign_kb.cli import main

    checkout = _checkout(tmp_path)
    copy_layout = _install(tmp_path, checkout)
    (checkout / "src" / "sovereign_kb" / "gwi.py").write_text("THRESHOLD = 9.5\n", encoding="utf-8")
    _git(checkout, "commit", "-qam", "move on")
    _use_layout(monkeypatch, *copy_layout)
    monkeypatch.setattr(sys, "argv", ["skb", "--version"])

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines[0] == "sovereign-kb 1.4.0"
    assert lines[1].startswith("install: STALE")
    assert str(checkout) in lines[1] and FIX_COMMAND in lines[1]
    assert "STALE INSTALL" not in captured.err  # said once, on stdout, not again as a banner

    editable_root = tmp_path / "editable"
    editable_root.mkdir()
    _use_layout(monkeypatch, *_install(editable_root, checkout, editable=True))
    with pytest.raises(SystemExit):
        main(["--version"])
    assert capsys.readouterr().out.splitlines()[1] == f"install: editable install of {checkout}"

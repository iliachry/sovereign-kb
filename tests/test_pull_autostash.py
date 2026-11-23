"""`pull_rebase_autostash` must report a conflicted autostash reapply (git exits 0 for it)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from sovereign_kb.cli import pull_rebase_autostash


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
    )


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote"
    remote.mkdir()
    _git(remote, "init", "-q", "-b", "main")
    (remote / "log.md").write_text("l1\n", encoding="utf-8")
    _git(remote, "add", "log.md")
    _git(remote, "commit", "-qm", "base")
    local = tmp_path / "local"
    subprocess.run(["git", "clone", "-q", str(remote), str(local)], check=True, capture_output=True)
    return remote, local


def test_conflicting_autostash_is_reported(tmp_path: Path) -> None:
    remote, local = _setup(tmp_path)
    (remote / "log.md").write_text("l1\nfrom-remote\n", encoding="utf-8")
    _git(remote, "commit", "-qam", "remote")
    (local / "log.md").write_text("l1\nfrom-local\n", encoding="utf-8")
    err = pull_rebase_autostash(local)
    assert err is not None and "log.md" in err and "stash@{0}" in err


def test_clean_autostash_passes_and_keeps_edits(tmp_path: Path) -> None:
    remote, local = _setup(tmp_path)
    (remote / "other.md").write_text("x\n", encoding="utf-8")
    _git(remote, "add", "other.md")
    _git(remote, "commit", "-qm", "remote")
    (local / "log.md").write_text("l1\nfrom-local\n", encoding="utf-8")
    assert pull_rebase_autostash(local) is None
    assert (local / "log.md").read_text(encoding="utf-8") == "l1\nfrom-local\n"
    assert (local / "other.md").is_file()


def test_existing_stashes_do_not_trigger_false_alarm(tmp_path: Path) -> None:
    remote, local = _setup(tmp_path)
    (local / "log.md").write_text("old\n", encoding="utf-8")
    _git(local, "stash")
    (remote / "other.md").write_text("x\n", encoding="utf-8")
    _git(remote, "add", "other.md")
    _git(remote, "commit", "-qm", "remote")
    assert pull_rebase_autostash(local) is None

"""Regression tests for audit-registry durability and GWI incremental-cache correctness fixes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import sovereign_kb.audit_registry as ar
import sovereign_kb.gwi as gwi_mod
from sovereign_kb.gwi_core import DEFAULT_K, DEFAULT_THETA


@pytest.fixture()
def reg_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "swm" / "audit_registry.json"
    monkeypatch.setattr(ar, "AUDIT_REGISTRY_PATH", path)
    return path


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _make_repo(tmp_path: Path, name: str = "regrepo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "main.py").write_text("print('hi')\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _report(
    g: float = 9.5,
    coupled: float | None = 9.5,
    veto: bool = False,
    theta: float = DEFAULT_THETA,
    k: float = DEFAULT_K,
    strict_lenses: bool = True,
) -> dict[str, Any]:
    cl: dict[str, Any] = {"veto_triggered": veto}
    if coupled is not None:
        cl["coupled_goldwood_g"] = coupled
    return {
        "profile": "app",
        "calculation": {"goldwood_g": g, "status": "PASS" if g >= 9.0 else "FAIL", "theta": theta, "k": k},
        "craft_lenses": cl,
        "audit_flags": {"strict_lenses": strict_lenses, "probe_audio": False, "execute_gate": False},
        "blockers": [],
    }


# --- 1. Durability: corrupt load, atomic save, lock --------------------------------------------


def test_corrupt_registry_raises_and_preserves_backup(reg_path: Path, tmp_path: Path) -> None:
    full = json.dumps({"projects": {f"p{i}": {"x": i} for i in range(50)}}, indent=2)
    reg_path.parent.mkdir(parents=True)
    torn = full[: len(full) // 2]
    reg_path.write_text(torn)

    with pytest.raises(ar.AuditRegistryCorruptError, match="corrupt"):
        ar.load_audit_registry()
    backups = list(reg_path.parent.glob("audit_registry.json.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_text() == torn

    repo = _make_repo(tmp_path)
    with pytest.raises(ar.AuditRegistryCorruptError):
        ar.record_gwi_audit(repo, _report())
    assert reg_path.read_text() == torn, "a failed load must never be followed by a wiping save"


def test_wrong_shape_registry_is_treated_as_corrupt(reg_path: Path) -> None:
    reg_path.parent.mkdir(parents=True)
    reg_path.write_text("[]")
    with pytest.raises(ar.AuditRegistryCorruptError):
        ar.load_audit_registry()


def test_save_is_atomic(reg_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reg = ar.load_audit_registry()
    reg["projects"]["keep"] = {"x": 1}
    ar.save_audit_registry(reg)
    before = reg_path.read_text()

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(ar.os, "replace", boom)
    reg["projects"]["new"] = {"x": 2}
    with pytest.raises(OSError):
        ar.save_audit_registry(reg)
    assert reg_path.read_text() == before
    assert [p.name for p in reg_path.parent.iterdir() if p.name.endswith(".tmp")] == []


def test_passport_yaml_written_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "pp"
    (repo / ".swm").mkdir(parents=True)
    passport = repo / ".swm" / "passport.yaml"
    passport.write_text("name: pp\n")
    calls: list[Path] = []
    real = ar._atomic_write_text
    monkeypatch.setattr(ar, "_atomic_write_text", lambda p, t: (calls.append(p), real(p, t)))
    ar._update_passport_yaml_audit(repo, {"last_audit": {"status": "PASS"}}, force_write=True)
    assert calls == [passport]
    assert "Sovereign Audit Provenance" in passport.read_text()


def test_record_gwi_audit_holds_lock_across_read_modify_write(reg_path: Path, tmp_path: Path) -> None:
    repo_a = _make_repo(tmp_path, "repo_a")
    repo_b = _make_repo(tmp_path, "repo_b")
    ar.record_gwi_audit(repo_a, _report())

    done = threading.Event()

    def writer() -> None:
        ar.record_gwi_audit(repo_b, _report())
        done.set()

    with ar._registry_lock():
        t = threading.Thread(target=writer)
        t.start()
        time.sleep(0.5)
        assert not done.is_set(), "writer must block while another process holds the registry lock"
    t.join(timeout=30)
    assert done.is_set()
    projects = json.loads(reg_path.read_text())["projects"]
    assert {"repo_a", "repo_b"} <= set(projects)


def test_env_override_registry_path(tmp_path: Path) -> None:
    target = tmp_path / "env_reg.json"
    code = "import sovereign_kb.audit_registry as a; print(a.AUDIT_REGISTRY_PATH)"
    out = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "SWM_AUDIT_REGISTRY": str(target)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == str(target)


# --- 2. Incremental cache vs --strict-lenses -----------------------------------------------


def test_cached_pass_with_low_coupled_g_is_not_fresh_under_strict_lenses(reg_path: Path, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    ar.record_gwi_audit(repo, _report(g=9.5, coupled=8.0))
    strict = ar.evaluate_subproject_freshness(repo, ".", require_strict_lenses=True)
    assert strict["is_fresh_pass"] is False and strict["freshness"] == "FAILED"
    # Without strict lenses the G-only PASS is still a valid cache hit.
    assert ar.evaluate_subproject_freshness(repo, ".")["is_fresh_pass"] is True

    ar.record_gwi_audit(repo, _report(g=9.5, coupled=9.4))
    assert ar.evaluate_subproject_freshness(repo, ".", require_strict_lenses=True)["is_fresh_pass"] is True

    ar.record_gwi_audit(repo, _report(g=9.5, coupled=9.4, veto=True))
    assert ar.evaluate_subproject_freshness(repo, ".", require_strict_lenses=True)["is_fresh_pass"] is False


def test_missing_stored_coupled_g_is_not_fresh_under_strict_lenses(reg_path: Path, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    ar.record_gwi_audit(repo, _report(coupled=None))
    assert ar.evaluate_subproject_freshness(repo, ".", require_strict_lenses=True)["is_fresh_pass"] is False


# --- 3. --subprojects honours --strict-lenses in the exit code -----------------------------


def test_subprojects_mode_fails_on_low_coupled_g_with_strict_lenses(
    reg_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo(tmp_path)
    monkeypatch.setattr(gwi_mod, "evaluate_target", lambda *_a, **_k: _report(g=9.5, coupled=8.0))
    gwi_mod.main_cli(["audit", str(repo), "--subprojects", "--json"])  # no strict flags -> exit 0
    with pytest.raises(SystemExit) as exc:
        gwi_mod.main_cli(["audit", str(repo), "--subprojects", "--strict-lenses", "--json"])
    assert exc.value.code == 1

    monkeypatch.setattr(gwi_mod, "evaluate_target", lambda *_a, **_k: _report(g=9.5, coupled=9.5))
    gwi_mod.main_cli(["audit", str(repo), "--subprojects", "--strict-lenses", "--json"])


def test_incremental_single_target_does_not_cache_hit_low_lens_score(
    reg_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo(tmp_path)
    monkeypatch.setattr(gwi_mod, "evaluate_target", lambda *_a, **_k: _report(g=9.5, coupled=8.0))
    with pytest.raises(SystemExit):
        gwi_mod.main_cli(["audit", str(repo), "--strict-lenses", "--json"])
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        gwi_mod.main_cli(["audit", str(repo), "--strict-lenses", "--incremental", "--json"])
    assert exc.value.code == 1
    assert '"cache_hit": true' not in capsys.readouterr().out


# --- 4. Cache key: theta / k / gwi source hash ---------------------------------------------


def test_theta_and_k_are_recorded_and_mismatch_is_not_fresh(reg_path: Path, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    rec = ar.record_gwi_audit(repo, _report(theta=5.0, k=1.0, strict_lenses=False))
    assert rec["theta"] == 5.0 and rec["k"] == 1.0
    default_info = ar.evaluate_subproject_freshness(repo, ".")
    assert default_info["freshness"] == "PARAM_MISMATCH" and not default_info["is_fresh_pass"]
    assert ar.evaluate_subproject_freshness(repo, ".", require_theta=5.0, require_k=1.0)["is_fresh_pass"]
    assert not ar.evaluate_subproject_freshness(repo, ".", require_theta=5.0, require_k=2.0)["is_fresh_pass"]


def test_gwi_source_edit_invalidates_cached_pass(
    reg_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo(tmp_path)
    assert ar.get_sovereign_versions()["gwi_src_sha"] == ar.compute_gwi_source_sha()
    ar.record_gwi_audit(repo, _report())
    assert ar.evaluate_subproject_freshness(repo, ".")["is_fresh_pass"] is True
    monkeypatch.setattr(ar, "compute_gwi_source_sha", lambda: "edited0000000000")
    info = ar.evaluate_subproject_freshness(repo, ".")
    assert info["freshness"] == "VERSION_DRIFT" and not info["is_fresh_pass"]


def test_gwi_source_sha_tracks_file_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    for name in ar._GWI_SOURCE_FILES:
        (pkg / name).write_text(f"# {name}\n")
    monkeypatch.setattr(ar, "__file__", str(pkg / "audit_registry.py"))
    before = ar.compute_gwi_source_sha()
    (pkg / "gwi_science.py").write_text("# edited, longer contents\n")
    assert ar.compute_gwi_source_sha() != before


# --- 5. Subproject fingerprint ignores evidence/ -------------------------------------------


def test_subproject_fingerprint_ignores_evidence_dir(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "paper").mkdir()
    (repo / "paper" / "main.tex").write_text("\\documentclass{article}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "paper")
    (repo / "paper" / "evidence").mkdir()
    (repo / "paper" / "evidence" / "run.json").write_text("{}")
    assert ar.compute_subproject_fingerprint(repo, "paper")["dirty"] is False
    (repo / "paper" / "notes.txt").write_text("x")
    assert ar.compute_subproject_fingerprint(repo, "paper")["dirty"] is True

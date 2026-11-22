"""Regression tests for frontier_organon / video_playback / rust_cert hardening fixes."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from sovereign_kb import frontier_organon as fo
from sovereign_kb import rust_cert, video_playback

# --------------------------------------------------------------------------------------------------
# 1. Mutation probe must refuse a target reached through a symlinked parent directory
# --------------------------------------------------------------------------------------------------


def test_mutation_probe_refuses_symlinked_parent_dir(tmp_path: Path) -> None:
    ext = tmp_path / "external_pkg"
    ext.mkdir()
    orig = "def add(a, b):\n    return a + b\n"
    (ext / "mod.py").write_text(orig, encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    os.symlink(ext, repo / "lib")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text(
        "import sys; sys.path.insert(0, 'lib')\nfrom mod import add\ndef test_a():\n    assert True\n",
        encoding="utf-8",
    )
    res = fo.run_ast_mutation_probe(repo, target_rel_path="lib/mod.py", max_mutants=2)
    assert res["status"] == "UNSAFE_SYMLINK_TARGET"
    assert res["passed"] is False
    assert (ext / "mod.py").read_text(encoding="utf-8") == orig


# --------------------------------------------------------------------------------------------------
# 2. pytest rc == 2 (mutant-induced collection error) counts as killed
# --------------------------------------------------------------------------------------------------


def test_mutation_probe_counts_collection_error_as_killed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "mod.py").write_text("def f(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_m.py").write_text(
        "from mod import f\ndef test_f():\n    assert f(1, 2) == 3\n", encoding="utf-8"
    )
    calls: list[int] = []

    def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(1)
        rc = 0 if len(calls) == 1 else 2  # baseline passes, every mutant breaks collection
        return subprocess.CompletedProcess(cmd, rc, "", "")

    monkeypatch.setattr(fo.subprocess, "run", fake_run)
    res = fo.run_ast_mutation_probe(tmp_path, target_rel_path="mod.py", max_mutants=2)
    assert res["mutants_tested"] >= 1
    assert res["mutants_survived"] == 0
    assert res["mutants_killed"] == res["mutants_tested"]
    assert all(d["outcome"] == "collection_error_killed" for d in res["details"])
    assert res["passed"] is True


def test_mutation_probe_baseline_collection_error_is_not_scored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "mod.py").write_text("def f(a, b):\n    return a + b\n", encoding="utf-8")
    monkeypatch.setattr(fo.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2, "", ""))
    res = fo.run_ast_mutation_probe(tmp_path, target_rel_path="mod.py", max_mutants=2)
    assert res["status"] == "BASELINE_FAILED"
    assert res["mutants_killed"] == 0
    assert res["passed"] is False


# --------------------------------------------------------------------------------------------------
# 3. Binary PLY truncation uses the real vertex stride
# --------------------------------------------------------------------------------------------------


def _binary_ply(path: Path, count: int, payload_len: int, extra_props: str = "") -> None:
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {count}\n"
        "property float x\nproperty float y\nproperty float z\nproperty uchar red\n"
        f"{extra_props}"
        "end_header\n"
    )
    path.write_bytes(header.encode("ascii") + b"\0" * payload_len)


def test_binary_ply_truncation_uses_vertex_stride(tmp_path: Path) -> None:
    stride = 13  # 3 floats + 1 uchar
    ok = tmp_path / "ok.ply"
    _binary_ply(ok, 100, 100 * stride)
    assert fo.parse_splat_asset_header(ok)["corrupt_or_truncated"] is False

    trunc = tmp_path / "trunc.ply"
    _binary_ply(trunc, 100, 100 * stride - 1)  # old check (>= 64 bytes) passed this
    assert fo.parse_splat_asset_header(trunc)["corrupt_or_truncated"] is True


def test_binary_ply_with_list_property_falls_back_to_lenient(tmp_path: Path) -> None:
    p = tmp_path / "list.ply"
    _binary_ply(p, 100, 64, extra_props="property list uchar int idx\n")
    assert fo.parse_splat_asset_header(p)["corrupt_or_truncated"] is False


def test_ply_vertex_stride_ignores_other_elements() -> None:
    head = "ply\nformat binary_little_endian 1.0\nelement vertex 3\nproperty double x\nproperty short s\nelement face 1\nproperty list uchar int vertex_indices\n"
    assert fo._ply_vertex_stride(head) == 10


# --------------------------------------------------------------------------------------------------
# 4. Rejected-event blocker with description=None must not crash
# --------------------------------------------------------------------------------------------------


def test_blocker_none_description_does_not_crash(tmp_path: Path) -> None:
    import json

    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        json.dumps({"disposition": "NOT_APPROVED", "blockers": [{"description": None, "id": "B-1"}]}) + "\n",
        encoding="utf-8",
    )
    vault = tmp_path / "vault"
    vault.mkdir()
    res = fo.compile_organon_prompts(vault, provenance_ledger_path=ledger, output_path=tmp_path / "out.json")
    guardrails = json.dumps(res)
    assert "B-1" in guardrails


# --------------------------------------------------------------------------------------------------
# 5. Anchored verdict parsing
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("This video cannot be approved in its current form.", "REVISE"),
        ("Not APPROVED FOR RELEASE yet.\nVerdict: REVISE", "REVISE"),
        ("Summary...\n**Final Verdict:** APPROVED FOR RELEASE\nScore 9/10", "APPROVED"),
        ("Verdict: REVISE\n...later...\nVerdict - approved", "APPROVED"),
        ("Verdict: APPROVED\nVerdict: REJECTED", "REVISE"),
        ("Great work.\nAPPROVED FOR RELEASE\n", "APPROVED"),
        ("", "REVISE"),
    ],
)
def test_parse_review_verdict(text: str, expected: str) -> None:
    assert video_playback.parse_review_verdict(text) == expected


def test_default_prompt_requests_verdict_line() -> None:
    assert "Verdict: APPROVED" in video_playback.DEFAULT_PROMPT


def _triad_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    oc = tmp_path / "oc"
    oc.write_text("#!/bin/sh\n", encoding="utf-8")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\0")
    monkeypatch.setattr(video_playback.shutil, "which", lambda name: str(oc) if name == "oc" else None)
    monkeypatch.setattr(video_playback, "extract_timeline_snapshots", lambda *a, **k: [])
    return video


def test_triad_nonzero_returncode_is_not_approved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = _triad_setup(tmp_path, monkeypatch)
    seen: dict[str, Any] = {}

    def fake_run(cmd: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 1, "Verdict: APPROVED", "boom")

    monkeypatch.setattr(video_playback.subprocess, "run", fake_run)
    res = video_playback.audit_video_triad_keyframes(video, tmp_path / "snaps")
    assert res["verdict"] == "REVISE"
    assert seen["timeout"] == 1800


def test_triad_timeout_is_failure_not_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = _triad_setup(tmp_path, monkeypatch)

    def fake_run(cmd: Any, **kw: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(video_playback.subprocess, "run", fake_run)
    res = video_playback.audit_video_triad_keyframes(video, tmp_path / "snaps")
    assert res["status"] == "FAIL"


def test_triad_cannot_be_approved_text_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = _triad_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        video_playback.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "This cannot be APPROVED.", ""),
    )
    assert video_playback.audit_video_triad_keyframes(video, tmp_path / "snaps")["verdict"] == "REVISE"


# --------------------------------------------------------------------------------------------------
# 6. Timeouts on ffmpeg/ffprobe and rustc runs
# --------------------------------------------------------------------------------------------------


def test_ffmpeg_calls_have_timeouts_and_survive_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_bin = tmp_path / "ffbin"
    fake_bin.write_text("", encoding="utf-8")
    monkeypatch.setattr(video_playback.shutil, "which", lambda name: str(fake_bin))
    timeouts: list[Any] = []

    def fake_check_output(cmd: Any, **kw: Any) -> str:
        timeouts.append(kw.get("timeout"))
        return "10.0\n"

    def fake_run(cmd: Any, **kw: Any) -> Any:
        timeouts.append(kw.get("timeout"))
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(video_playback.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(video_playback.subprocess, "run", fake_run)
    frames = video_playback.extract_timeline_snapshots(tmp_path / "v.mp4", tmp_path / "out", num_frames=2)
    assert frames == []
    assert timeouts == [300, 300, 300]


def test_rust_run_bounded_converts_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(cmd: Any, **kw: Any) -> Any:
        seen.update(kw)
        raise subprocess.TimeoutExpired(cmd, kw["timeout"], output=b"partial")

    monkeypatch.setattr(rust_cert.subprocess, "run", fake_run)
    res = rust_cert._run_bounded(["rustc", "x.rs"], rust_cert.RUSTC_COMPILE_TIMEOUT_S)
    assert res.returncode != 0
    assert "timed out" in res.stderr
    assert res.stdout == "partial"
    assert seen["timeout"] == 600
    assert rust_cert.RUST_TEST_BINARY_TIMEOUT_S == 300

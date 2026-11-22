"""Regression tests for `.evr` signing-key resolution, integrity hardening, and CLI exit codes (evr.py / chain.py)."""

from __future__ import annotations

import argparse
import json
import zlib
from pathlib import Path

import pytest

from sovereign_kb import chain as chain_mod
from sovereign_kb import evr as evr_mod
from sovereign_kb.evr import (
    EVR_PUBLIC_DEMO_SIGN_KEY,
    EvrBuilder,
    EvrCodec,
    EvrReader,
    create_showcase_evr_capsule,
    resolve_evr_sign_key,
)


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SKB_EVR_SIGN_KEY", raising=False)
    monkeypatch.setenv("SWM_HOME", str(tmp_path / "swm"))
    monkeypatch.setenv("SWM_CHAIN_ANCHORS", str(tmp_path / "anchors.jsonl"))


def _small_capsule(path: Path, sign_key: str | None = None) -> Path:
    b = EvrBuilder(title="t", author="a", polyglot=False, sign_key=sign_key, created_unix_ns=1700000000000000000)
    b.add_bytes("docs/a.txt", b"alpha " * 64, codec=EvrCodec.ZLIB)
    b.add_bytes("docs/b.txt", b"beta " * 64, codec=EvrCodec.ZLIB)
    return b.write(path)


# 1. Signing key resolver + public-demo-key trust labelling
def test_sign_key_resolver_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_evr_sign_key(None) == EVR_PUBLIC_DEMO_SIGN_KEY
    assert resolve_evr_sign_key(None, fallback_demo=False) is None
    monkeypatch.setenv("SKB_EVR_SIGN_KEY", "env-key")
    assert resolve_evr_sign_key(None) == "env-key"
    assert resolve_evr_sign_key("explicit") == "explicit"


def test_demo_key_seal_warns_and_is_labelled(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    summ = create_showcase_evr_capsule(tmp_path / "demo.evr")
    err = capsys.readouterr().err
    assert err.count("PUBLIC demo key") == 1 and "SKB_EVR_SIGN_KEY" in err
    assert summ["verification"]["signature_verified"] is True
    assert summ["verification"]["signature_trust"] == "public-demo-key"


def test_env_key_seal_is_configured_and_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SKB_EVR_SIGN_KEY", "real-secret")
    summ = create_showcase_evr_capsule(tmp_path / "env.evr")
    assert "PUBLIC demo key" not in capsys.readouterr().err
    assert summ["verification"]["signature_trust"] == "configured-key"
    with EvrReader(tmp_path / "env.evr") as r:
        assert r.verify(sign_key=EVR_PUBLIC_DEMO_SIGN_KEY)["signature_verified"] is False


# 2. read_chunk rejects trailing bytes after zlib stream; TOC overlap detection
def test_read_chunk_rejects_zlib_trailing_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orig = zlib.compress
    monkeypatch.setattr(evr_mod.zlib, "compress", lambda d, level=6: orig(d, level) + b"SMUGGLED")
    cap = _small_capsule(tmp_path / "junk.evr")
    monkeypatch.setattr(evr_mod.zlib, "compress", orig)
    with EvrReader(cap) as r:
        with pytest.raises(ValueError, match="trailing bytes"):
            r.read_chunk("docs/a.txt")
        assert r.verify()["valid"] is False


def test_verify_flags_overlapping_toc_ranges(tmp_path: Path) -> None:
    cap = _small_capsule(tmp_path / "ov.evr")
    with EvrReader(cap) as r:
        assert r.verify()["valid"] is True
        b0, b1 = r.binary_toc[0], r.binary_toc[1]
        b0["stored_len"] = b1["data_offset"] - b0["data_offset"] + 8
        errs = r.verify()["errors"]
        assert any("Overlapping chunk payload ranges" in e for e in errs)


# 3. chain status / full-suite exit non-zero on failed checks (incl. --json)
@pytest.mark.parametrize("as_json", [True, False])
def test_chain_status_exits_nonzero_on_failed_lyapunov(monkeypatch: pytest.MonkeyPatch, as_json: bool) -> None:
    real = chain_mod.run_tokenomics_suite

    def broken() -> dict:
        tok = real()
        tok["pid_mpc_control"]["lyapunov_certificate"]["schur_stable"] = False
        return tok

    monkeypatch.setattr(chain_mod, "run_tokenomics_suite", broken)
    with pytest.raises(SystemExit) as ei:
        chain_mod.cmd_chain(argparse.Namespace(action="status", json=as_json, target="."))
    assert ei.value.code == 1


def test_chain_status_json_ok_does_not_exit(capsys: pytest.CaptureFixture[str]) -> None:
    chain_mod.cmd_chain(argparse.Namespace(action="status", json=True, target="."))
    assert "summary" in json.loads(capsys.readouterr().out)


# 4. Seal/pack/prove CLI exit 1 on failed verification
def test_cli_pack_show_exits_on_invalid_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        evr_mod,
        "pack_poetics_show_capsule",
        lambda **kw: {
            "path": "x",
            "file_size_bytes": 0,
            "modalities": [],
            "merkle_root": "0",
            "verification": {"valid": False},
        },
    )
    with pytest.raises(SystemExit) as ei:
        evr_mod.main_cli(["pack-show", "-o", str(tmp_path / "s.evr"), "--json"])
    assert ei.value.code == 1


def test_cli_prove_exits_on_unverified_proof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _small_capsule(tmp_path / "p.evr")
    evr_mod.main_cli(["prove", str(cap), "0", "--json"])  # genuine proof: no exit
    monkeypatch.setattr(EvrReader, "prove_chunk", lambda self, c: {"verified": False})
    with pytest.raises(SystemExit) as ei:
        evr_mod.main_cli(["prove", str(cap), "0", "--json"])
    assert ei.value.code == 1


# 5. evr verify on SIGNED capsule without a key: warn; --require-signature fails
def test_cli_verify_signed_without_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cap = _small_capsule(tmp_path / "signed.evr", sign_key="k1")
    evr_mod.main_cli(["verify", str(cap), "--json"])
    out = capsys.readouterr()
    assert "NOT checked" in out.err
    assert json.loads(out.out)["signature_checked"] is False
    with pytest.raises(SystemExit) as ei:
        evr_mod.main_cli(["verify", str(cap), "--json", "--require-signature"])
    assert ei.value.code == 1
    capsys.readouterr()
    evr_mod.main_cli(["verify", str(cap), "--json", "--require-signature", "--sign-key", "k1"])
    res = json.loads(capsys.readouterr().out)
    assert res["signature_verified"] is True and res["signature_trust"] == "configured-key"


# 6. Atomic write + duplicate extraction path refusal
def test_builder_write_is_atomic_and_keeps_exec_bit(tmp_path: Path) -> None:
    b = EvrBuilder(title="t", polyglot=True)
    b.add_bytes("a.txt", b"x")
    out = b.write(tmp_path / "out" / "cap.evr")
    assert sorted(p.name for p in out.parent.iterdir()) == ["cap.evr"]
    assert out.stat().st_mode & 0o111


def test_extract_all_refuses_duplicate_paths(tmp_path: Path) -> None:
    cap = _small_capsule(tmp_path / "d.evr")
    with EvrReader(cap) as r:
        r.chunks[1]["path"] = r.chunks[0]["path"]
        with pytest.raises(ValueError, match="Duplicate extraction path"):
            r.extract_all(tmp_path / "x")

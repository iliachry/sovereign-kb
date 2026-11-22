"""Tests for `.evr` signer key ids, `evr keygen`, and the trusted key registry (`evr trust`)."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from sovereign_kb import evr as evr_mod
from sovereign_kb.evr import EVR_PUBLIC_DEMO_SIGN_KEY, EvrBuilder, EvrReader, evr_key_id


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("SKB_EVR_SIGN_KEY", raising=False)
    monkeypatch.delenv("SKB_EVR_TRUSTED_KEYS", raising=False)
    return home


def _cli_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> dict:
    capsys.readouterr()
    evr_mod.main_cli([*argv, "--json"])
    return json.loads(capsys.readouterr().out)


def _keygen(capsys: pytest.CaptureFixture[str], name: str) -> dict:
    return _cli_json(capsys, ["keygen", "--name", name])


def _pack(tmp_path: Path, out: Path, extra: list[str], capsys: pytest.CaptureFixture[str]) -> Path:
    src = tmp_path / "src.txt"
    src.write_text("hello evr signer identity\n", encoding="utf-8")
    _cli_json(capsys, ["pack", str(src), "-o", str(out), *extra])
    return out


def test_keygen_writes_0600_key_and_refuses_overwrite(_isolated_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    res = _keygen(capsys, "alice")
    path = Path(res["path"])
    assert path == (_isolated_home / ".config" / "sovereign-kb" / "evr-keys" / "alice.key").resolve()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    key = path.read_text(encoding="utf-8").strip()
    assert len(key) == 64 and "key" not in res
    assert res["key_id"] == evr_key_id(key) and len(res["key_id"]) == 16
    with pytest.raises(SystemExit) as ei:
        evr_mod.main_cli(["keygen", "--name", "alice"])
    assert ei.value.code == 1
    assert path.read_text(encoding="utf-8").strip() == key
    printed = _cli_json(capsys, ["keygen", "--out", str(_isolated_home / "b.key"), "--print-key"])
    assert evr_key_id(printed["key"]) == printed["key_id"]


def test_seal_with_key_file_auto_resolves_trusted_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    k = _keygen(capsys, "alice")
    added = _cli_json(capsys, ["trust", "add", k["path"], "--name", "Alice"])
    assert added["key_id"] == k["key_id"]
    reg = Path(tmp_path / "home" / ".config" / "sovereign-kb" / "evr-trusted-keys.json")
    assert stat.S_IMODE(reg.stat().st_mode) == 0o600
    cap = _pack(tmp_path, tmp_path / "a.evr", ["--key-file", k["path"]], capsys)
    ver = _cli_json(capsys, ["verify", str(cap), "--require-signature", "--require-trusted"])
    assert ver["valid"] and ver["signature_verified"] is True
    assert ver["signature_trust"] == "trusted-key:Alice"
    assert ver["signer_key_id"] == k["key_id"] and ver["signer_name"] == "Alice"


def test_untrusted_key_fails_require_trusted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    k = _keygen(capsys, "mallory")
    cap = _pack(tmp_path, tmp_path / "m.evr", ["--key-file", k["path"]], capsys)
    ver = _cli_json(capsys, ["verify", str(cap), "--key-file", k["path"]])
    assert ver["signature_trust"] == "configured-key" and ver["signer_name"] is None
    with pytest.raises(SystemExit) as ei:
        evr_mod.main_cli(["verify", str(cap), "--key-file", k["path"], "--json", "--require-trusted"])
    assert ei.value.code == 1
    demo = _pack(tmp_path, tmp_path / "d.evr", ["--sign-key", EVR_PUBLIC_DEMO_SIGN_KEY], capsys)
    dv = _cli_json(capsys, ["verify", str(demo), "--sign-key", EVR_PUBLIC_DEMO_SIGN_KEY])
    assert dv["signature_trust"] == "public-demo-key"
    assert dv["signer_key_id"] == evr_key_id(EVR_PUBLIC_DEMO_SIGN_KEY)
    with pytest.raises(SystemExit):
        evr_mod.main_cli(["verify", str(demo), "--sign-key", EVR_PUBLIC_DEMO_SIGN_KEY, "--json", "--require-trusted"])


def test_tampered_signer_key_id_fails_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = "k-real"
    real_msg = evr_mod._canonical_signing_message

    # Attacker relabels the envelope key id but keeps an HMAC computed over the original id.
    def _forged(**kw: object) -> bytes:
        return real_msg(**{**kw, "signer_key_id": evr_key_id(key)})

    with monkeypatch.context() as mp:
        mp.setattr(evr_mod, "evr_key_id", lambda k: "deadbeefdeadbeef")
        mp.setattr(evr_mod, "_canonical_signing_message", _forged)
        b = EvrBuilder(title="t", sign_key=key)
        b.add_bytes("a.txt", b"x")
        cap = b.write(tmp_path / "t.evr")
    with EvrReader(cap) as r:
        assert r.signer_key_id() == "deadbeefdeadbeef"
        ver = r.verify(sign_key=key)
    assert ver["valid"] is False and ver["signature_verified"] is False


def test_legacy_capsule_without_key_id_still_verifies(tmp_path: Path) -> None:
    b = EvrBuilder(title="legacy", sign_key="k1", record_key_id=False)
    b.add_bytes("a.txt", b"x")
    cap = b.write(tmp_path / "legacy.evr")
    with EvrReader(cap) as r:
        prov = json.loads(r.read_chunk("__evr__/provenance.json"))
        assert "signer_key_id" not in prov
        ver = r.verify(sign_key="k1")
        assert r.verify(sign_key="wrong")["valid"] is False
    assert ver["valid"] and ver["signature_trust"] == "configured-key" and ver["signer_key_id"] is None


def test_trust_remove_and_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    reg = tmp_path / "custom" / "trusted.json"
    monkeypatch.setenv("SKB_EVR_TRUSTED_KEYS", str(reg))
    k = _keygen(capsys, "bob")
    _cli_json(capsys, ["trust", "add", k["path"]])
    listed = _cli_json(capsys, ["trust", "list"])
    assert [r["key_id"] for r in listed["keys"]] == [k["key_id"]] and listed["keys"][0]["name"] == "bob"
    assert reg.is_file() and stat.S_IMODE(reg.stat().st_mode) == 0o600
    cap = _pack(tmp_path, tmp_path / "b.evr", ["--key-file", k["path"]], capsys)
    assert _cli_json(capsys, ["verify", str(cap)])["signature_trust"] == "trusted-key:bob"
    assert _cli_json(capsys, ["trust", "remove", k["key_id"]])["removed"] is True
    assert _cli_json(capsys, ["trust", "list"])["keys"] == []
    ver = _cli_json(capsys, ["verify", str(cap)])
    assert ver["signature_checked"] is False and ver["signature_trust"] is None
    with pytest.raises(SystemExit) as ei:
        evr_mod.main_cli(["trust", "remove", k["key_id"]])
    assert ei.value.code == 1

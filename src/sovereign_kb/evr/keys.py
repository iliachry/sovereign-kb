"""`.evr` signing-key resolution, signer key identity, and the trusted signer key registry (stdlib-only)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Publicly known demo HMAC key: kept for backwards-compatible demos/tests, never a trust signal.
EVR_PUBLIC_DEMO_SIGN_KEY = "sovereign-evr-v1-key"
EVR_SIGN_KEY_ENV = "SKB_EVR_SIGN_KEY"


def resolve_evr_sign_key(sign_key: str | None = None, *, fallback_demo: bool = True) -> str | None:
    """Resolve the HMAC key: explicit `sign_key` > env `SKB_EVR_SIGN_KEY` > public demo key (if allowed)."""
    if sign_key:
        return sign_key
    env_key = os.environ.get(EVR_SIGN_KEY_ENV, "").strip()
    if env_key:
        return env_key
    return EVR_PUBLIC_DEMO_SIGN_KEY if fallback_demo else None


def _resolve_seal_key(sign_key: str | None) -> str:
    key = resolve_evr_sign_key(sign_key) or EVR_PUBLIC_DEMO_SIGN_KEY
    if key == EVR_PUBLIC_DEMO_SIGN_KEY:
        print(
            "Warning: .evr capsule signed with the PUBLIC demo key — not a trust signal; set SKB_EVR_SIGN_KEY",
            file=sys.stderr,
        )
    return key


# ---------------------------------------------------------------------------
# Signer key identity + trusted key registry (stdlib-only)
# ---------------------------------------------------------------------------
EVR_TRUSTED_KEYS_ENV = "SKB_EVR_TRUSTED_KEYS"
_KEY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def evr_key_id(sign_key: str) -> str:
    """Signer key id: first 16 hex chars of sha256 over the HMAC key bytes (UTF-8 of the key string)."""
    return hashlib.sha256(sign_key.encode("utf-8")).hexdigest()[:16]


def default_evr_keys_dir() -> Path:
    return Path.home() / ".config" / "sovereign-kb" / "evr-keys"


def trusted_keys_path() -> Path:
    env = os.environ.get(EVR_TRUSTED_KEYS_ENV, "").strip()
    return Path(env).expanduser() if env else Path.home() / ".config" / "sovereign-kb" / "evr-trusted-keys.json"


def read_evr_key_file(path: str | Path) -> str:
    """Read an HMAC key from a key file (first non-empty line); warn when group/other can read it."""
    p = Path(path).expanduser()
    lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    key = lines[0] if lines else ""
    if not key:
        raise ValueError(f"Empty .evr key file: {p}")
    if p.stat().st_mode & 0o077:
        print(f"Warning: .evr key file {p} is readable by group/other (expected mode 0600)", file=sys.stderr)
    return key


def generate_evr_key(name: str = "default", out: str | Path | None = None) -> dict[str, str]:
    """Create a 32-byte random HMAC key in a new 0600 file (never overwrites). Returns key, key_id, path."""
    if not _KEY_NAME_RE.match(name):
        raise ValueError(f"Invalid key name {name!r} (allowed: letters, digits, '.', '_', '-')")
    path = Path(out).expanduser() if out else default_evr_keys_dir() / f"{name}.key"
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing key file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = secrets.token_hex(32)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, (key + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return {"key": key, "key_id": evr_key_id(key), "path": str(path.resolve())}


def load_trusted_keys(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Load the trusted signer registry {key_id: {name, key_path|key_hex, added}}; missing/corrupt -> {}."""
    p = Path(path) if path else trusted_keys_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _save_trusted_keys(registry: dict[str, dict[str, Any]], path: str | Path | None = None) -> Path:
    p = Path(path) if path else trusted_keys_path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(registry, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return p


def _trusted_entry_key(entry: dict[str, Any]) -> str | None:
    if entry.get("key_hex"):
        return str(entry["key_hex"])
    if entry.get("key_path"):
        try:
            return read_evr_key_file(entry["key_path"])
        except (OSError, ValueError):
            return None
    return None


def trust_add_key(key_file: str | Path, name: str | None = None) -> dict[str, Any]:
    kp = Path(key_file).expanduser().resolve()
    key = read_evr_key_file(kp)
    kid = evr_key_id(key)
    entry = {
        "name": name or kp.stem,
        "key_path": str(kp),
        "added": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    registry = load_trusted_keys()
    registry[kid] = entry
    _save_trusted_keys(registry)
    return {"key_id": kid, **entry}


def trust_remove_key(key_id: str) -> bool:
    registry = load_trusted_keys()
    if key_id not in registry:
        return False
    del registry[key_id]
    _save_trusted_keys(registry)
    return True


def lookup_trusted_key(key_id: str | None) -> tuple[str, str] | None:
    """Return (key, name) for a registry key id whose stored key really hashes to that id."""
    if not key_id:
        return None
    entry = load_trusted_keys().get(key_id)
    if not entry:
        return None
    key = _trusted_entry_key(entry)
    if not key or evr_key_id(key) != key_id:
        return None
    return key, str(entry.get("name", key_id))


def _signature_trust_for(sign_key: str) -> tuple[str, str | None]:
    """Classify a key that verified a signature -> (signature_trust, signer_name)."""
    if sign_key == EVR_PUBLIC_DEMO_SIGN_KEY:
        return "public-demo-key", None
    hit = lookup_trusted_key(evr_key_id(sign_key))
    if hit and hmac.compare_digest(hit[0].encode("utf-8"), sign_key.encode("utf-8")):
        return f"trusted-key:{hit[1]}", hit[1]
    return "configured-key", None

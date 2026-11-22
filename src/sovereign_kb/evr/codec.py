"""Alignment, hashing, RFC 6962 Merkle proofs, bfloat16/semantic encoding, and metadata parsers for `.evr`."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import struct
import unicodedata
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

from .constants import (
    CODE_CODE_EXTS,
    DOC_EXTS,
    EVR_ALIGNMENT,
    EVR_SPEC_VERSION,
    MEDIA_EXTS,
    SPATIAL_EXTS,
    SQLITE_EXTS,
    TABLE_EXTS,
    TENSOR_EXTS,
    TOKEN_RE,
    EvrKind,
)


def _align64(offset: int) -> int:
    rem = offset % EVR_ALIGNMENT
    return offset if rem == 0 else offset + (EVR_ALIGNMENT - rem)


def _pad64_bytes(length: int, fill: bytes = b"\x00") -> bytes:
    rem = length % EVR_ALIGNMENT
    if rem == 0:
        return b""
    return fill * (EVR_ALIGNMENT - rem)


def _blake2b_256(data: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=32).digest()


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def _fold_text(text: str) -> str:
    nfd = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")


def _validate_chunk_path(path: str, allow_internal: bool = False) -> str:
    """
    Validate and normalize a logical chunk path.
    Rejects empty strings, NUL/control bytes, absolute paths, drive prefixes,
    `.` or `..` traversal segments, and reserved `__evr__/` paths (unless `allow_internal=True`).
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Invalid .evr chunk path: path must be a non-empty string")
    if "\x00" in path or any(ord(ch) < 32 for ch in path):
        raise ValueError(f"Invalid .evr chunk path '{path!r}': control characters or NUL bytes forbidden")
    norm = path.replace("\\", "/")
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
        raise ValueError(f"Invalid .evr chunk path '{path}': absolute paths and drive roots forbidden")
    pure = PurePosixPath(norm)
    parts = pure.parts
    if not parts:
        raise ValueError(f"Invalid .evr chunk path '{path}': empty normalized path")
    for part in parts:
        if part in ("", ".", ".."):
            raise ValueError(f"Unsafe path traversal segment '{part}' forbidden in .evr chunk path '{path}'")
    canonical = "/".join(parts)
    if not allow_internal and (canonical == "__evr__" or canonical.startswith("__evr__/")):
        raise ValueError(f"Reserved namespace '__evr__/' forbidden for user chunk '{path}'")
    return canonical


def _escape_json_for_html_script(obj: Any) -> str:
    """Serialize `obj` to compact JSON with `<`, `>`, `&`, U+2028, and U+2029 escaped as `\\uXXXX`."""
    raw = json.dumps(obj, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    return (
        raw.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _float_to_bf16_u16(val: float) -> int:
    """Convert a Python float to IEEE 754 bfloat16 (round-to-nearest-even) as a 16-bit unsigned integer."""
    bits = struct.unpack("<I", struct.pack("<f", float(val)))[0]
    rounding_bias = 0x00007FFF + ((bits >> 16) & 1)
    return ((bits + rounding_bias) >> 16) & 0xFFFF


def _bf16_u16_to_float(u16_val: int) -> float:
    bits = (int(u16_val) & 0xFFFF) << 16
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _semantic_embed_64d(text: str) -> list[float]:
    """Deterministic 64-d L2-normalized feature-hashed token/subword projection (zero external model dependency)."""
    vec = [0.0] * 64
    folded = _fold_text(text)
    tokens = TOKEN_RE.findall(folded)
    if not tokens:
        return vec
    for tok in tokens:
        h = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
        idx = h[0] % 64
        sign = 1.0 if (h[1] & 1) == 0 else -1.0
        vec[idx] += sign
        if len(tok) >= 3:
            for i in range(len(tok) - 2):
                sh = hashlib.blake2b(tok[i : i + 3].encode("utf-8"), digest_size=4).digest()
                vec[sh[0] % 64] += 0.35 * (1.0 if (sh[1] & 1) == 0 else -1.0)
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 1e-12:
        vec = [round(v / norm, 6) for v in vec]
    return vec


def _rfc6962_leaf_hash(leaf_bytes: bytes) -> bytes:
    return _blake2b_256(b"\x00" + leaf_bytes)


def _rfc6962_node_hash(left: bytes, right: bytes) -> bytes:
    return _blake2b_256(b"\x01" + left + right)


def _canonical_chunk_leaf_payload(
    path: str,
    kind_id: int,
    codec_id: int,
    raw_len: int,
    low_flags: int,
    content_hash_bytes: bytes,
    mime_type: str,
    metadata: dict[str, Any],
) -> bytes:
    """
    Construct the canonical authenticated leaf descriptor for a chunk.
    Uses 64-bit unsigned integer (`<Q`) for `raw_len` and `low_flags` (supporting chunks > 4 GiB)
    and binds `mime_type` and canonical JSON `metadata` (including tensor `dtype`, `shape`, `numel`).
    """
    meta_json = json.dumps(metadata or {}, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8")
    meta_digest = _blake2b_256(mime_type.encode("utf-8") + b"\x00" + meta_json)
    return (
        path.encode("utf-8")
        + b"\x00"
        + struct.pack("<IIQQ", int(kind_id), int(codec_id), int(raw_len), int(low_flags) & 0xFFFFFFFF)
        + content_hash_bytes
        + meta_digest
    )


def _canonical_signing_message(
    pre_merkle_root: bytes,
    preamble_bytes: bytes,
    title: str,
    author: str,
    description: str,
    created_unix_ns: int,
    flags: int,
    alignment: int,
    polyglot: bool,
) -> bytes:
    """Bind the Merkle root, polyglot preamble digest, and capsule identity metadata into the MAC input."""
    header_meta = json.dumps(
        {
            "evr_version": EVR_SPEC_VERSION,
            "title": title,
            "author": author,
            "description": description,
            "created_unix_ns": int(created_unix_ns),
            "flags": int(flags),
            "alignment": int(alignment),
            "polyglot": bool(polyglot),
        },
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    return b"evr.v1.mac\x00" + pre_merkle_root + _blake2b_256(preamble_bytes) + _blake2b_256(header_meta)


def compute_rfc6962_merkle_root(leaf_payloads: list[bytes]) -> bytes:
    """Compute an RFC-6962-shaped domain-separated binary BLAKE2b-256 Merkle root over `leaf_payloads`."""
    if not leaf_payloads:
        return _blake2b_256(b"")
    level = [_rfc6962_leaf_hash(p) for p in leaf_payloads]
    while len(level) > 1:
        nxt: list[bytes] = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                nxt.append(_rfc6962_node_hash(level[i], level[i + 1]))
            else:
                nxt.append(level[i])
        level = nxt
    return level[0]


def build_rfc6962_inclusion_proof(leaf_payloads: list[bytes], leaf_index: int) -> list[dict[str, str]]:
    """Build an RFC-6962-shaped audit path (inclusion proof) for `leaf_index`."""
    if leaf_index < 0 or leaf_index >= len(leaf_payloads):
        raise IndexError(f"leaf_index {leaf_index} out of range (0..{len(leaf_payloads) - 1})")
    level = [_rfc6962_leaf_hash(p) for p in leaf_payloads]
    idx = leaf_index
    proof: list[dict[str, str]] = []
    while len(level) > 1:
        nxt: list[bytes] = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                if i == idx or i + 1 == idx:
                    if idx % 2 == 0:
                        proof.append({"side": "right", "hash": level[i + 1].hex()})
                    else:
                        proof.append({"side": "left", "hash": level[i].hex()})
                nxt.append(_rfc6962_node_hash(level[i], level[i + 1]))
            else:
                nxt.append(level[i])
        idx //= 2
        level = nxt
    return proof


def verify_rfc6962_inclusion_proof(
    leaf_payload: bytes,
    proof: list[dict[str, str]],
    expected_root_hex: str,
) -> bool:
    """Verify an RFC-6962-shaped inclusion proof against `expected_root_hex`."""
    cur = _rfc6962_leaf_hash(leaf_payload)
    for step in proof:
        sib = bytes.fromhex(step["hash"])
        if step["side"] == "left":
            cur = _rfc6962_node_hash(sib, cur)
        else:
            cur = _rfc6962_node_hash(cur, sib)
    return cur.hex() == expected_root_hex.lower()


def infer_evr_kind(path_str: str, raw_bytes: bytes) -> EvrKind:
    ext = Path(path_str).suffix.lower()
    if raw_bytes.startswith(b"SQLite format 3\x00") or ext in SQLITE_EXTS:
        return EvrKind.SQLITE
    if ext in SPATIAL_EXTS:
        return EvrKind.SPATIAL
    if ext in MEDIA_EXTS:
        return EvrKind.MEDIA
    if ext in TABLE_EXTS:
        return EvrKind.TABLE
    if ext in TENSOR_EXTS:
        return EvrKind.TENSOR
    if ext in CODE_CODE_EXTS:
        return EvrKind.CODE
    if ext in DOC_EXTS:
        return EvrKind.DOCUMENT
    if ext == ".json":
        try:
            parsed = json.loads(raw_bytes.decode("utf-8"))
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                return EvrKind.TABLE
            if isinstance(parsed, dict) and ("nodes" in parsed and "edges" in parsed):
                return EvrKind.GRAPH
            if isinstance(parsed, dict) and ("state" in parsed or "episodic" in parsed or "agent_state" in parsed):
                return EvrKind.STATE
        except Exception:
            pass
        return EvrKind.CODE
    return EvrKind.DOCUMENT


def _parse_obj_spatial_metadata(raw_bytes: bytes) -> dict[str, Any]:
    """Extract vertex count, face count, bounding box, and preview geometry from Wavefront .obj."""
    text = raw_bytes.decode("utf-8", errors="ignore")
    vertices: list[list[float]] = []
    faces: list[list[int]] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("v "):
            parts = s.split()
            if len(parts) >= 4:
                try:
                    vertices.append([float(parts[1]), float(parts[2]), float(parts[3])])
                except ValueError:
                    pass
        elif s.startswith("f "):
            parts = s.split()[1:]
            idxs: list[int] = []
            for p in parts:
                try:
                    idxs.append(int(p.split("/")[0]) - 1)
                except ValueError:
                    pass
            if len(idxs) >= 3:
                faces.append(idxs[:4])
    if not vertices:
        return {"vertex_count": 0, "face_count": 0}
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    return {
        "vertex_count": len(vertices),
        "face_count": len(faces),
        "bbox_min": [round(min(xs), 4), round(min(ys), 4), round(min(zs), 4)],
        "bbox_max": [round(max(xs), 4), round(max(ys), 4), round(max(zs), 4)],
        "preview_vertices": vertices[:256],
        "preview_faces": faces[:512],
    }


def _parse_table_metadata(path_str: str, raw_bytes: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Extract table columns, row count, and sample rows for CSV/TSV/JSONL/JSON tables."""
    ext = Path(path_str).suffix.lower()
    text = raw_bytes.decode("utf-8", errors="ignore")
    rows: list[dict[str, Any]] = []
    columns: list[str] = []
    if ext in (".csv", ".tsv"):
        delim = "\t" if ext == ".tsv" else ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delim)
        columns = list(reader.fieldnames or [])
        for r in reader:
            rows.append(dict(r))
    elif ext in (".jsonl", ".ndjson"):
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
                if isinstance(obj, dict):
                    for k in obj:
                        if k not in columns:
                            columns.append(k)
                    rows.append(obj)
            except Exception:
                pass
    elif ext == ".json":
        try:
            arr = json.loads(text)
            if isinstance(arr, list):
                for obj in arr:
                    if isinstance(obj, dict):
                        for k in obj:
                            if k not in columns:
                                columns.append(k)
                        rows.append(obj)
        except Exception:
            pass
    meta = {
        "columns": columns,
        "row_count": len(rows),
        "preview_rows": rows[:15],
    }
    return meta, rows

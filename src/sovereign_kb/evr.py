"""
The `.evr` ("Everything" v1.0) Universal File Format & Polyglot Zero-Copy Capsule Engine.

Provides a deterministic, zero-dependency implementation of `.evr` ("Everything"):
  1. 64-Byte-Aligned Zero-Copy Binary Container (`EVR\\x01` Superblock + `1RVE` Footer):
     - Every chunk payload sits at a 64-byte aligned file offset (`offset % 64 == 0`) for
       zero-copy `mmap` access across CPU SIMD (AVX-512 / NEON), GPU buffers, and tensors.
     - Content-addressed chunk deduplication via 256-bit `BLAKE2b-256` digests + IEEE `CRC32`
       over both uncompressed (`crc32`) and stored (`stored_crc32`) payloads.
     - Dual-layer Table of Contents: fixed 128-byte binary `EvrTocEntry` records (`O(1)` C/Rust
       lookup) + structured JSON `EvrManifest` with strict field-by-field parity enforcement.
  2. All 12 Universal Computing Domains (`EvrKind`):
     - `0x01 DOCUMENT` : Markdown, LaTeX, PDF, UTF-8 text, YAML frontmatter & `[[wikilinks]]`
     - `0x02 CODE`     : Source code, scripts, AST symbol table, executable `.wasm` modules
     - `0x03 TABLE`    : CSV, TSV, JSONL, JSON datasets with schema & SQLite auto-ingestion
     - `0x04 MEDIA`    : Images (`png`, `svg`, `webp`, `jpg`), Audio (`wav`, `mp3`), Video (`mp4`)
     - `0x05 SPATIAL`  : 3D meshes & scenes (`obj`, `gltf`, `glb`, `ply`, `stl`, `splat`)
     - `0x06 TENSOR`   : 64-byte-aligned zero-copy N-D arrays (`f32`, `f64`, `f16`, `bf16`, `i32`, `i64`, `u8`, `q4_0`)
     - `0x07 INDEX`    : Embedded Hybrid BM25 + 64-d feature-hashed cosine vector index
     - `0x08 GRAPH`    : Knowledge & Causal Dependency Graph (`nodes`, `edges`, `[[wikilinks]]`)
     - `0x09 STATE`    : Sovereign Agent / Brain State Snapshot (episodic log, KV memory)
     - `0x0A SQLITE`   : Embedded SQLite3 database image (`evr_chunks`, `evr_tensors`, `evr_fts` FTS5 virtual table)
     - `0x0B CRYPTO`   : RFC-6962-shaped domain-separated BLAKE2b Merkle Tree + inclusion proofs + keyed MAC
     - `0x0C POLYGLOT` : Triple-Polyglot Executable Shell + HTML5/3D Studio + 64B-Aligned Binary Capsule
  3. Cross-Language Rust <-> Python Bit-Exact Interoperability (`RUST_EVR_KERNEL_SRC`):
     - Native Rust zero-copy verifier compiled via `rustc --edition 2021 -D warnings` with checked
       slice bounds and stored-payload CRC32 verification across both `RAW` and `ZLIB` streams.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import html
import io
import json
import math
import mimetypes
import mmap
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
import zlib
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag
from pathlib import Path, PurePosixPath
from typing import Any

EVR_SPEC_VERSION = "1.0.0"
EVR_MAGIC_START = b"EVR\x01"
EVR_MAGIC_RESERVED = b"EVR!"
EVR_MAGIC_END = b"1RVE"
EVR_ALIGNMENT = 64

# 64-byte Superblock:
# magic(4s), ver_major(H), ver_minor(H), flags(I), alignment(I),
# created_unix_ns(Q), chunk_count(Q), toc_offset(Q), toc_uncompressed_len(Q),
# toc_compressed_len(Q), superblock_crc32(I), reserved(4s)
SUPERBLOCK_STRUCT = struct.Struct("<4sHHIIQQQQQI4s")
assert SUPERBLOCK_STRUCT.size == 64

# 128-byte Binary TOC Entry:
# kind(I), codec(I), data_offset(Q), stored_len(Q), raw_len(Q),
# flags(Q) [upper 32 bits = stored_crc32, lower 32 bits = chunk flags],
# content_hash(32s), crc32(I) [raw_crc32], name_utf8(52s)
TOC_ENTRY_STRUCT = struct.Struct("<IIQQQQ32sI52s")
assert TOC_ENTRY_STRUCT.size == 128

# 64-byte Footer (always at EOF - 64 .. EOF):
# magic_start(4s), flags(I), superblock_offset(Q), toc_offset(Q),
# manifest_offset(Q), manifest_len(Q), merkle_root_prefix(16s),
# footer_crc32(I), magic_end(4s)
FOOTER_STRUCT = struct.Struct("<4sIQQQQ16sI4s")
assert FOOTER_STRUCT.size == 64


class EvrKind(IntEnum):
    DOCUMENT = 0x01
    CODE = 0x02
    TABLE = 0x03
    MEDIA = 0x04
    SPATIAL = 0x05
    TENSOR = 0x06
    INDEX = 0x07
    GRAPH = 0x08
    STATE = 0x09
    SQLITE = 0x0A
    CRYPTO = 0x0B
    POLYGLOT = 0x0C


class EvrCodec(IntEnum):
    RAW = 0
    ZLIB = 1


class EvrFlags(IntFlag):
    NONE = 0x000
    POLYGLOT = 0x001
    MERKLE_VERIFIED = 0x002
    HAS_INDEX = 0x004
    HAS_SQLITE = 0x008
    HAS_TENSORS = 0x010
    HAS_SPATIAL = 0x020
    HAS_GRAPH = 0x040
    HAS_STATE = 0x080
    SIGNED = 0x100


TENSOR_DTYPES: dict[str, tuple[int, str, int]] = {
    # dtype_name: (dtype_code, struct_char, byte_width)
    "f32": (1, "f", 4),
    "f64": (2, "d", 8),
    "i32": (3, "i", 4),
    "i64": (4, "q", 8),
    "u8": (5, "B", 1),
    "i8": (6, "b", 1),
    "f16": (7, "e", 2),
    "bf16": (8, "H", 2),
    "q4_0": (9, "B", 1),
}

CODE_CODE_EXTS = {
    ".py",
    ".rs",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".go",
    ".sh",
    ".zsh",
    ".bash",
    ".wgsl",
    ".glsl",
    ".hlsl",
    ".sql",
    ".wasm",
    ".toml",
    ".yaml",
    ".yml",
}
DOC_EXTS = {".md", ".markdown", ".txt", ".rst", ".tex", ".bib", ".pdf", ".adoc"}
TABLE_EXTS = {".csv", ".tsv", ".jsonl", ".ndjson", ".parquet", ".arrow"}
MEDIA_EXTS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".svg",
    ".avif",
    ".wav",
    ".mp3",
    ".ogg",
    ".opus",
    ".aac",
    ".flac",
    ".mp4",
    ".webm",
    ".mov",
    ".srt",
    ".vtt",
}
SPATIAL_EXTS = {".obj", ".gltf", ".glb", ".ply", ".stl", ".splat", ".usd", ".usda", ".usdz"}
TENSOR_EXTS = {".bin", ".tensor", ".npy", ".safetensors", ".gguf"}
SQLITE_EXTS = {".db", ".sqlite", ".sqlite3"}

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
SYM_DEF_RE = re.compile(
    r"^\s*(?:pub\s+)?(?:async\s+)?(?:def|class|fn|struct|enum|trait|interface|type)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.M,
)
TOKEN_RE = re.compile(r"[A-Za-z0-9_\u0370-\u03ff\u1f00-\u1fff]{2,}")


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


@dataclass
class EvrChunkSpec:
    path: str
    kind: EvrKind
    raw_bytes: bytes
    codec: EvrCodec = EvrCodec.RAW
    mime_type: str = "application/octet-stream"
    flags: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvrChunkRecord:
    index: int
    path: str
    kind: EvrKind
    codec: EvrCodec
    data_offset: int
    stored_len: int
    raw_len: int
    flags: int
    content_hash_hex: str
    crc32: int
    stored_crc32: int
    mime_type: str
    deduplicated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "path": self.path,
            "kind": self.kind.name,
            "kind_id": int(self.kind),
            "codec": self.codec.name,
            "codec_id": int(self.codec),
            "data_offset": self.data_offset,
            "aligned_64": (self.data_offset % EVR_ALIGNMENT == 0),
            "stored_len": self.stored_len,
            "raw_len": self.raw_len,
            "flags": self.flags,
            "content_hash": self.content_hash_hex,
            "crc32": self.crc32,
            "stored_crc32": self.stored_crc32,
            "mime_type": self.mime_type,
            "deduplicated": self.deduplicated,
            "metadata": self.metadata,
        }


class EvrBuilder:
    """Deterministic builder for `.evr` ("Everything" v1.0) capsules."""

    def __init__(
        self,
        title: str = "Everything Capsule (.evr)",
        author: str = "Sovereign Organon",
        description: str = "",
        polyglot: bool = False,
        sign_key: str | None = None,
        auto_index: bool = True,
        auto_graph: bool = True,
        auto_sqlite: bool = True,
        created_unix_ns: int | None = None,
    ) -> None:
        self.title = title
        self.author = author
        self.description = description
        self.polyglot = polyglot
        self.sign_key = sign_key
        self.auto_index = auto_index
        self.auto_graph = auto_graph
        self.auto_sqlite = auto_sqlite
        if created_unix_ns is not None:
            self.created_unix_ns = int(created_unix_ns)
        else:
            sde = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
            self.created_unix_ns = int(sde) * 1_000_000_000 if sde.isdigit() else 0
        self._specs: list[EvrChunkSpec] = []
        self._seen_paths: set[str] = set()
        self._tables_for_sqlite: dict[str, tuple[list[str], list[dict[str, Any]]]] = {}
        self._extra_graph_edges: list[dict[str, Any]] = []

    def add_bytes(
        self,
        path: str,
        data: bytes,
        kind: EvrKind | None = None,
        codec: EvrCodec | None = None,
        mime_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EvrBuilder:
        norm_path = _validate_chunk_path(path, allow_internal=False)
        if norm_path in self._seen_paths:
            raise ValueError(f"Duplicate chunk path '{norm_path}' forbidden in .evr capsule")
        self._seen_paths.add(norm_path)

        resolved_kind = kind if kind is not None else infer_evr_kind(norm_path, data)
        if codec is None:
            if resolved_kind in (EvrKind.TENSOR, EvrKind.SQLITE, EvrKind.MEDIA, EvrKind.SPATIAL):
                resolved_codec = EvrCodec.RAW
            else:
                resolved_codec = EvrCodec.ZLIB if len(data) >= 128 else EvrCodec.RAW
        else:
            resolved_codec = codec

        guessed_mime, _ = mimetypes.guess_type(norm_path)
        resolved_mime = mime_type or guessed_mime or "application/octet-stream"
        meta = dict(metadata or {})

        if resolved_kind == EvrKind.DOCUMENT:
            text = data.decode("utf-8", errors="ignore")
            links = WIKILINK_RE.findall(text)
            if links:
                meta["wikilinks"] = sorted(set(links))
            meta["line_count"] = len(text.splitlines())
            meta["word_count"] = len(text.split())
        elif resolved_kind == EvrKind.CODE:
            text = data.decode("utf-8", errors="ignore")
            syms = SYM_DEF_RE.findall(text)
            if syms:
                meta["symbols"] = syms[:64]
            meta["line_count"] = len(text.splitlines())
        elif resolved_kind == EvrKind.TABLE:
            t_meta, rows = _parse_table_metadata(norm_path, data)
            meta.update(t_meta)
            if t_meta["columns"] and rows:
                tbl_name = re.sub(r"[^A-Za-z0-9_]", "_", Path(norm_path).stem).lower()
                if not tbl_name or tbl_name[0].isdigit():
                    tbl_name = f"tbl_{tbl_name}"
                self._tables_for_sqlite[tbl_name] = (t_meta["columns"], rows)
        elif resolved_kind == EvrKind.SPATIAL:
            if norm_path.lower().endswith(".obj"):
                meta.update(_parse_obj_spatial_metadata(data))

        self._specs.append(
            EvrChunkSpec(
                path=norm_path,
                kind=resolved_kind,
                raw_bytes=data,
                codec=resolved_codec,
                mime_type=resolved_mime,
                metadata=meta,
            )
        )
        return self

    def add_text(
        self,
        path: str,
        text: str,
        kind: EvrKind | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EvrBuilder:
        return self.add_bytes(path, text.encode("utf-8"), kind=kind, metadata=metadata)

    def add_tensor(
        self,
        path: str,
        values: list[float] | list[int] | bytes,
        shape: list[int] | tuple[int, ...],
        dtype: str = "f32",
        metadata: dict[str, Any] | None = None,
    ) -> EvrBuilder:
        """Add a 64-byte-aligned zero-copy N-dimensional tensor chunk (`EvrKind.TENSOR`)."""
        norm_path = _validate_chunk_path(path, allow_internal=False)
        if norm_path in self._seen_paths:
            raise ValueError(f"Duplicate chunk path '{norm_path}' forbidden in .evr capsule")
        if dtype not in TENSOR_DTYPES:
            raise ValueError(f"Unsupported tensor dtype '{dtype}'. Supported: {sorted(TENSOR_DTYPES)}")
        if not shape or any(int(dim) <= 0 for dim in shape):
            raise ValueError(f"Invalid tensor shape {list(shape)}: dimensions must be positive integers")

        dtype_code, fmt_char, width = TENSOR_DTYPES[dtype]
        numel = 1
        for dim in shape:
            numel *= int(dim)
        expected_byte_len = numel * width

        if isinstance(values, bytes):
            raw_bytes = values
            if len(raw_bytes) != expected_byte_len:
                raise ValueError(
                    f"Tensor '{norm_path}' byte length {len(raw_bytes)} != expected {expected_byte_len} ({numel} * {width})"
                )
            if dtype in ("f32", "f64", "f16", "i32", "i64", "u8", "i8"):
                unpacked = struct.unpack(f"<{numel}{fmt_char}", raw_bytes)
                float_vals = [float(x) for x in unpacked]
            elif dtype == "bf16":
                u16s = struct.unpack(f"<{numel}H", raw_bytes)
                float_vals = [_bf16_u16_to_float(u) for u in u16s]
            else:
                float_vals = [float(b) for b in raw_bytes]
        else:
            if len(values) != numel:
                raise ValueError(
                    f"Tensor '{norm_path}' value count {len(values)} != shape product {numel} for shape {list(shape)}"
                )
            if dtype in ("f32", "f64", "f16"):
                float_vals = [float(v) for v in values]
                raw_bytes = struct.pack(f"<{numel}{fmt_char}", *float_vals)
            elif dtype == "bf16":
                float_vals = [float(v) for v in values]
                u16_vals = [_float_to_bf16_u16(v) for v in float_vals]
                raw_bytes = struct.pack(f"<{numel}H", *u16_vals)
            else:
                int_vals = [int(v) for v in values]
                raw_bytes = struct.pack(f"<{numel}{fmt_char}", *int_vals)
                float_vals = [float(v) for v in int_vals]

        self._seen_paths.add(norm_path)

        strides: list[int] = []
        stride = width
        for dim in reversed(shape):
            strides.append(stride)
            stride *= int(dim)
        strides.reverse()

        t_meta: dict[str, Any] = dict(metadata or {})
        if float_vals:
            vmin = min(float_vals)
            vmax = max(float_vals)
            vmean = sum(float_vals) / len(float_vals)
            l2 = math.sqrt(sum(v * v for v in float_vals))
            t_meta.update(
                {
                    "min": round(vmin, 6),
                    "max": round(vmax, 6),
                    "mean": round(vmean, 6),
                    "l2_norm": round(l2, 6),
                    "preview_values": [round(v, 4) for v in float_vals[:64]],
                }
            )
        # Structural tensor fields overwrite any caller-supplied keys so metadata cannot lie
        t_meta.update(
            {
                "dtype": dtype,
                "dtype_code": dtype_code,
                "shape": [int(d) for d in shape],
                "strides": strides,
                "numel": numel,
                "byte_length": len(raw_bytes),
            }
        )

        self._specs.append(
            EvrChunkSpec(
                path=norm_path,
                kind=EvrKind.TENSOR,
                raw_bytes=raw_bytes,
                codec=EvrCodec.RAW,
                mime_type=f"application/x-evr-tensor+{dtype}",
                flags=dtype_code,
                metadata=t_meta,
            )
        )
        return self

    def add_state(self, path: str, state_payload: dict[str, Any]) -> EvrBuilder:
        """Add a Sovereign AI / Brain State Snapshot (`EvrKind.STATE`)."""
        raw = json.dumps(state_payload, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return self.add_bytes(
            path,
            raw,
            kind=EvrKind.STATE,
            codec=EvrCodec.ZLIB,
            mime_type="application/x-evr-state+json",
            metadata={"keys": sorted(state_payload.keys())},
        )

    def add_graph_edge(self, source: str, target: str, relation: str = "links_to", weight: float = 1.0) -> EvrBuilder:
        self._extra_graph_edges.append({"source": source, "target": target, "relation": relation, "weight": weight})
        return self

    def add_path(self, target: Path, base_dir: Path | None = None, follow_symlinks: bool = False) -> EvrBuilder:
        """Recursively pack a file or directory into the `.evr` builder (skipping symlinks by default)."""
        target = Path(target)
        if target.is_symlink() and not follow_symlinks:
            return self
        target_res = target.resolve()
        if target_res.is_file():
            rel = target_res.name if base_dir is None else str(target_res.relative_to(base_dir.resolve()))
            self.add_bytes(rel, target_res.read_bytes())
            return self
        root = base_dir.resolve() if base_dir is not None else target_res
        for p in sorted(target_res.rglob("*")):
            if any(
                part in (".git", ".venv", "__pycache__", "node_modules", ".pytest_cache", ".ruff_cache")
                for part in p.parts
            ):
                continue
            if p.is_symlink() and not follow_symlinks:
                continue
            if p.is_file():
                rel = str(p.relative_to(root))
                self.add_bytes(rel, p.read_bytes())
        return self

    def _synthesize_graph_chunk(self, user_specs: list[EvrChunkSpec]) -> EvrChunkSpec:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = list(self._extra_graph_edges)
        known_stems: dict[str, str] = {}
        for sp in user_specs:
            nodes.append({"id": sp.path, "kind": sp.kind.name, "size": len(sp.raw_bytes)})
            known_stems[Path(sp.path).stem.lower()] = sp.path
            known_stems[sp.path.lower()] = sp.path

        for sp in user_specs:
            for wl in sp.metadata.get("wikilinks", []):
                wl_clean = wl.strip().lower()
                target_path = known_stems.get(wl_clean, f"{wl.strip()}.md")
                edges.append(
                    {
                        "source": sp.path,
                        "target": target_path,
                        "relation": "wikilink",
                        "weight": 1.0,
                    }
                )

        deg: dict[str, int] = {n["id"]: 0 for n in nodes}
        for e in edges:
            deg[e["source"]] = deg.get(e["source"], 0) + 1
            deg[e["target"]] = deg.get(e["target"], 0) + 1

        graph_doc = {
            "schema": "evr.graph.v1",
            "node_count": len(nodes),
            "edge_count": len(edges),
            "max_degree": max(deg.values()) if deg else 0,
            "nodes": nodes,
            "edges": edges,
        }
        raw = json.dumps(graph_doc, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return EvrChunkSpec(
            path="__evr__/graph.json",
            kind=EvrKind.GRAPH,
            raw_bytes=raw,
            codec=EvrCodec.ZLIB if len(raw) >= 128 else EvrCodec.RAW,
            mime_type="application/x-evr-graph+json",
            metadata={
                "node_count": len(nodes),
                "edge_count": len(edges),
                "max_degree": graph_doc["max_degree"],
            },
        )

    def _synthesize_index_chunk(self, user_specs: list[EvrChunkSpec]) -> EvrChunkSpec:
        """Build an embedded BM25 inverted index + 64-d feature-hashed cosine index over textual chunks."""
        docs: list[dict[str, Any]] = []
        postings: dict[str, list[list[int]]] = {}
        total_len = 0

        for doc_id, sp in enumerate(user_specs):
            if sp.kind in (EvrKind.DOCUMENT, EvrKind.CODE, EvrKind.TABLE, EvrKind.STATE, EvrKind.GRAPH):
                text = sp.raw_bytes.decode("utf-8", errors="ignore")
            else:
                text = f"{sp.path} {sp.kind.name} {json.dumps(sp.metadata)}"
            folded = _fold_text(f"{sp.path} {text}")
            tokens = TOKEN_RE.findall(folded)
            tf_map: dict[str, int] = {}
            for tok in tokens:
                tf_map[tok] = tf_map.get(tok, 0) + 1
            for tok, tf in tf_map.items():
                postings.setdefault(tok, []).append([doc_id, tf])
            doc_len = len(tokens)
            total_len += doc_len
            snippet = " ".join(text.strip().split())[:220]
            docs.append(
                {
                    "doc_id": doc_id,
                    "path": sp.path,
                    "kind": sp.kind.name,
                    "doc_len": doc_len,
                    "snippet": snippet,
                    "vec64": _semantic_embed_64d(f"{sp.path} {text[:2000]}"),
                }
            )

        avgdl = (total_len / len(docs)) if docs else 1.0
        index_payload = {
            "schema": "evr.index.v1",
            "doc_count": len(docs),
            "vocab_size": len(postings),
            "avgdl": round(avgdl, 4),
            "docs": docs,
            "postings": postings,
        }
        raw = json.dumps(index_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return EvrChunkSpec(
            path="__evr__/index.json",
            kind=EvrKind.INDEX,
            raw_bytes=raw,
            codec=EvrCodec.ZLIB if len(raw) >= 128 else EvrCodec.RAW,
            mime_type="application/x-evr-index+json",
            metadata={
                "doc_count": len(docs),
                "vocab_size": len(postings),
                "avgdl": round(avgdl, 4),
            },
        )

    def _synthesize_sqlite_chunk(self, user_specs: list[EvrChunkSpec]) -> EvrChunkSpec:
        """Synthesize a self-contained SQLite3 relational & FTS5 catalog image inside the `.evr` file."""
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            conn = sqlite3.connect(str(tmp_path))
            cur = conn.cursor()
            cur.execute("PRAGMA page_size = 4096;")
            cur.execute("PRAGMA journal_mode = OFF;")
            cur.execute("PRAGMA synchronous = OFF;")
            cur.execute(
                """
                CREATE TABLE evr_chunks (
                    chunk_index INTEGER PRIMARY KEY,
                    path TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    raw_len INTEGER NOT NULL,
                    mime_type TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE evr_tensors (
                    path TEXT PRIMARY KEY,
                    dtype TEXT NOT NULL,
                    shape_json TEXT NOT NULL,
                    numel INTEGER NOT NULL,
                    byte_length INTEGER NOT NULL,
                    min_val REAL,
                    max_val REAL,
                    mean_val REAL,
                    l2_norm REAL
                );
                """
            )
            try:
                cur.execute("CREATE VIRTUAL TABLE evr_fts USING fts5(path, kind, body);")
            except sqlite3.OperationalError:
                cur.execute("CREATE TABLE evr_fts (path TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL);")

            for idx, sp in enumerate(user_specs):
                h_hex = _blake2b_256(sp.raw_bytes).hex()
                cur.execute(
                    "INSERT INTO evr_chunks VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        idx,
                        sp.path,
                        sp.kind.name,
                        len(sp.raw_bytes),
                        sp.mime_type,
                        h_hex,
                        json.dumps(sp.metadata, sort_keys=True),
                    ),
                )
                if sp.kind == EvrKind.TENSOR:
                    m = sp.metadata
                    cur.execute(
                        "INSERT OR REPLACE INTO evr_tensors VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            sp.path,
                            str(m.get("dtype", "f32")),
                            json.dumps(m.get("shape", [])),
                            int(m.get("numel", 0)),
                            int(m.get("byte_length", len(sp.raw_bytes))),
                            m.get("min"),
                            m.get("max"),
                            m.get("mean"),
                            m.get("l2_norm"),
                        ),
                    )
                if sp.kind in (EvrKind.DOCUMENT, EvrKind.CODE, EvrKind.TABLE, EvrKind.STATE):
                    body_txt = sp.raw_bytes.decode("utf-8", errors="ignore")[:16384]
                    cur.execute(
                        "INSERT INTO evr_fts (path, kind, body) VALUES (?, ?, ?)", (sp.path, sp.kind.name, body_txt)
                    )

            for tbl_name, (cols, rows) in self._tables_for_sqlite.items():
                safe_cols = [re.sub(r"[^A-Za-z0-9_]", "_", c) or f"col_{i}" for i, c in enumerate(cols)]
                col_defs = ", ".join(f'"{c}" TEXT' for c in safe_cols)
                cur.execute(f'CREATE TABLE IF NOT EXISTS "{tbl_name}" ({col_defs});')
                placeholders = ", ".join("?" for _ in safe_cols)
                for r in rows:
                    vals = [str(r.get(orig_c, "")) for orig_c in cols]
                    cur.execute(f'INSERT INTO "{tbl_name}" VALUES ({placeholders})', vals)

            conn.commit()
            conn.close()
            sqlite_bytes = tmp_path.read_bytes()
        finally:
            tmp_path.unlink(missing_ok=True)

        return EvrChunkSpec(
            path="__evr__/catalog.sqlite",
            kind=EvrKind.SQLITE,
            raw_bytes=sqlite_bytes,
            codec=EvrCodec.RAW,
            mime_type="application/vnd.sqlite3",
            metadata={
                "tables": ["evr_chunks", "evr_tensors", "evr_fts", *sorted(self._tables_for_sqlite.keys())],
                "page_size": 4096,
            },
        )

    def _build_polyglot_preamble(self, preview_summary: dict[str, Any]) -> bytes:
        """
        Build a triple-polyglot preamble:
          1. Executable POSIX shell script (`chmod +x file.evr && ./file.evr` runs `evr inspect`)
          2. Self-contained HTML5 + 3D Spatial + Tensor + Search + Merkle Studio with strict XSS escaping
          3. Sealed with `<div hidden style="display:none!important"><plaintext>` and padded with
             ASCII spaces (`0x20`) to a strict 64-byte boundary so `EvrSuperblock` and every
             `RAW` chunk (`TENSOR`, `SQLITE`, `SPATIAL`, `MEDIA`) sit at `offset % 64 == 0`.
        """
        safe_title = html.escape(self.title, quote=True)
        embedded_json = _escape_json_for_html_script(preview_summary)
        html_doc = f""": '<!--'
if command -v evr >/dev/null 2>&1; then exec evr inspect "$0" "$@"; else exec python3 -m sovereign_kb.evr inspect "$0" "$@"; fi
-->
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_title} — .evr Everything Capsule</title>
<style>
:root {{
  --bg: #F8FAFC;
  --surface: #FFFFFF;
  --border: #E2E8F0;
  --text: #0F172A;
  --muted: #475569;
  --accent: #1E40AF;
  --accent-soft: #EFF6FF;
  --ok: #15803D;
  --mono: "JetBrains Mono", "SFMono-Regular", Menlo, monospace;
  --sans: "Inter", -apple-system, BlinkMacSystemFont, sans-serif;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: var(--sans);
  font-size: 14px;
  line-height: 1.5;
  font-variant-numeric: tabular-nums;
}}
header.topbar {{
  height: 52px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  position: sticky;
  top: 0;
  z-index: 10;
}}
.brand {{ display: flex; align-items: center; gap: 10px; font-weight: 700; font-size: 15px; }}
.badge {{
  font-family: var(--mono);
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--accent-soft);
  color: var(--accent);
  border: 1px solid #BFDBFE;
}}
.badge-ok {{ background: #F0FDF4; color: var(--ok); border-color: #BBF7D0; }}
.layout {{
  display: grid;
  grid-template-columns: 2fr 7fr 3fr;
  gap: 16px;
  max-width: 1440px;
  margin: 16px auto;
  padding: 0 20px;
}}
.panel {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 14px;
}}
h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); margin-bottom: 10px; }}
.chunk-item {{
  padding: 7px 10px;
  border-radius: 4px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-family: var(--mono);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  border: 1px solid transparent;
}}
.chunk-item:hover, .chunk-item.active {{
  background: var(--accent-soft);
  border-color: #BFDBFE;
  color: var(--accent);
}}
input.search-box {{
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 4px;
  font-size: 13px;
  margin-bottom: 12px;
}}
pre.code-view {{
  background: #0F172A;
  color: #F8FAFC;
  font-family: var(--mono);
  font-size: 12px;
  padding: 14px;
  border-radius: 6px;
  overflow: auto;
  max-height: 440px;
}}
canvas.viewport-3d {{
  width: 100%;
  height: 320px;
  background: #0F172A;
  border-radius: 6px;
  display: block;
}}
table.data-table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  margin-top: 8px;
}}
table.data-table th, table.data-table td {{
  border: 1px solid var(--border);
  padding: 6px 8px;
  text-align: left;
}}
table.data-table th {{ background: var(--bg); font-weight: 600; }}
.kv-row {{
  display: flex;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px solid var(--border);
  font-size: 12px;
}}
.kv-row span:last-child {{ font-family: var(--mono); font-weight: 600; }}
.heatmap-grid {{
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 3px;
  margin-top: 10px;
}}
.heat-cell {{
  padding: 6px 4px;
  text-align: center;
  font-family: var(--mono);
  font-size: 11px;
  border-radius: 3px;
}}
</style>
</head>
<body>
<header class="topbar">
  <div class="brand">
    <span class="badge">EVR v1.0</span>
    <span id="cap-title">{safe_title}</span>
  </div>
  <div style="display:flex;gap:8px;align-items:center;">
    <span class="badge badge-ok" id="align-pill">64B ALIGNED MMAP</span>
    <span class="badge badge-ok" id="merkle-pill">MERKLE ENVELOPE</span>
  </div>
</header>
<main class="layout">
  <section class="panel" aria-label="Chunk Navigator">
    <h2>Streams (<span id="chunk-count">0</span>)</h2>
    <input type="search" id="search-input" class="search-box" placeholder="Filter streams...">
    <div id="chunk-list"></div>
  </section>
  <section class="panel" aria-label="Primary Inspector">
    <h2 id="inspector-heading">Capsule Overview</h2>
    <div id="inspector-stage"></div>
  </section>
  <aside class="panel" aria-label="Cryptographic Provenance & Telemetry">
    <h2>Provenance & Layout</h2>
    <div id="provenance-list"></div>
  </aside>
</main>
<script id="evr-embedded-manifest" type="application/json">{embedded_json}</script>
<script>
(function() {{
  if (document.body && document.body.firstChild && document.body.firstChild.nodeType === 3) {{
    document.body.firstChild.nodeValue = "";
  }}
  function esc(val) {{
    return String(val ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }}
  const data = JSON.parse(document.getElementById("evr-embedded-manifest").textContent);
  document.getElementById("chunk-count").textContent = String(data.chunks.length);
  const provEl = document.getElementById("provenance-list");
  const rows = [
    ["Format", "EVR v" + data.version],
    ["Alignment", data.alignment + " bytes (SIMD/mmap)"],
    ["Total Chunks", String(data.chunks.length)],
    ["Modalities", (data.modalities || []).join(", ")],
    ["Merkle Root", (data.merkle_root || "").slice(0, 16) + "..."],
    ["Author", data.author || "Sovereign Organon"]
  ];
  provEl.innerHTML = rows.map(r => `<div class="kv-row"><span>${{esc(r[0])}}</span><span>${{esc(r[1])}}</span></div>`).join("");

  const listEl = document.getElementById("chunk-list");
  const stageEl = document.getElementById("inspector-stage");
  const headEl = document.getElementById("inspector-heading");

  function renderSpatialCanvas(meta) {{
    const cvs = document.createElement("canvas");
    cvs.className = "viewport-3d";
    cvs.width = 640;
    cvs.height = 320;
    stageEl.appendChild(cvs);
    const ctx = cvs.getContext("2d");
    const verts = meta.preview_vertices || [[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]];
    const faces = meta.preview_faces || [[0,1,2,3],[4,5,6,7],[0,1,5,4],[2,3,7,6]];
    let angle = 0.4;
    function draw() {{
      ctx.fillStyle = "#0F172A";
      ctx.fillRect(0, 0, cvs.width, cvs.height);
      const ca = Math.cos(angle), sa = Math.sin(angle);
      const proj = verts.map(v => {{
        const x = v[0]*ca - v[2]*sa;
        const z = v[0]*sa + v[2]*ca + 4.0;
        const y = v[1];
        const s = 220 / Math.max(z, 0.5);
        return [cvs.width/2 + x*s, cvs.height/2 - y*s];
      }});
      ctx.strokeStyle = "#38BDF8";
      ctx.lineWidth = 1.5;
      faces.forEach(f => {{
        ctx.beginPath();
        f.forEach((idx, i) => {{
          const p = proj[idx % proj.length];
          if (!p) return;
          if (i === 0) ctx.moveTo(p[0], p[1]);
          else ctx.lineTo(p[0], p[1]);
        }});
        ctx.closePath();
        ctx.stroke();
      }});
      angle += 0.015;
      requestAnimationFrame(draw);
    }}
    draw();
  }}

  function selectChunk(idx) {{
    const ch = data.chunks[idx];
    if (!ch) return;
    document.querySelectorAll(".chunk-item").forEach((el, i) => el.classList.toggle("active", i === idx));
    headEl.textContent = `${{ch.path}} [${{ch.kind}} · ${{ch.raw_len}} B]`;
    stageEl.innerHTML = "";
    const meta = ch.metadata || {{}};
    if (ch.kind === "TENSOR") {{
      const vals = meta.preview_values || [];
      const minV = Number(meta.min ?? 0), maxV = Number(meta.max ?? 1);
      const span = Math.max(maxV - minV, 1e-6);
      stageEl.innerHTML = `<div class="kv-row"><span>Shape</span><span>[${{esc((meta.shape||[]).join(", "))}}] (${{esc(meta.dtype)}})</span></div>
        <div class="kv-row"><span>Range / L2 Norm</span><span>[${{esc(minV)}}, ${{esc(maxV)}}] · L2=${{esc(meta.l2_norm)}}</span></div>
        <div class="heatmap-grid">${{vals.slice(0, 32).map(v => {{
          const numV = Number(v) || 0;
          const t = Math.max(0, Math.min(1, (numV - minV) / span));
          const bg = `rgba(30, 64, 175, ${{0.12 + 0.75 * t}})`;
          const fg = t > 0.55 ? "#FFFFFF" : "#0F172A";
          return `<div class="heat-cell" style="background:${{bg}};color:${{fg}}">${{esc(numV)}}</div>`;
        }}).join("")}}</div>`;
    }} else if (ch.kind === "SPATIAL") {{
      stageEl.innerHTML = `<div class="kv-row"><span>Geometry</span><span>${{esc(meta.vertex_count || 0)}} vertices · ${{esc(meta.face_count || 0)}} faces</span></div>`;
      renderSpatialCanvas(meta);
    }} else if (ch.kind === "TABLE" && meta.preview_rows && meta.columns) {{
      const cols = meta.columns.slice(0, 4);
      const trs = meta.preview_rows.slice(0, 8).map(r => `<tr>${{cols.map(c => `<td>${{esc(r[c] ?? "")}}</td>`).join("")}}</tr>`).join("");
      stageEl.innerHTML = `<table class="data-table"><thead><tr>${{cols.map(c => `<th>${{esc(c)}}</th>`).join("")}}</tr></thead><tbody>${{trs}}</tbody></table>`;
    }} else {{
      const pre = document.createElement("pre");
      pre.className = "code-view";
      pre.textContent = ch.preview_text || JSON.stringify(meta, null, 2);
      stageEl.appendChild(pre);
    }}
  }}

  function renderList(filterText) {{
    const q = (filterText || "").toLowerCase();
    listEl.innerHTML = "";
    data.chunks.forEach((ch, idx) => {{
      const hay = `${{ch.path}} ${{ch.kind}} ${{ch.preview_text || ""}}`.toLowerCase();
      if (q && !hay.includes(q)) return;
      const div = document.createElement("div");
      div.className = "chunk-item";
      const spPath = document.createElement("span");
      spPath.textContent = ch.path;
      const spKind = document.createElement("span");
      spKind.className = "badge";
      spKind.textContent = ch.kind;
      div.appendChild(spPath);
      div.appendChild(spKind);
      div.onclick = () => selectChunk(idx);
      listEl.appendChild(div);
    }});
  }}

  document.getElementById("search-input").addEventListener("input", e => renderList(e.target.value));
  renderList("");
  if (data.chunks.length > 0) selectChunk(0);
}})();
</script>
<div hidden style="display:none!important"><plaintext>"""
        raw = html_doc.encode("utf-8")
        padding = _pad64_bytes(len(raw), fill=b" ")
        return raw + padding

    def build_bytes(self) -> bytes:
        """Assemble the complete `.evr` v1.0 binary container in memory and return bytes."""
        specs: list[EvrChunkSpec] = list(self._specs)
        user_specs = list(specs)

        if self.auto_graph and not any(s.kind == EvrKind.GRAPH for s in specs):
            specs.append(self._synthesize_graph_chunk(user_specs))
        if self.auto_index and not any(s.kind == EvrKind.INDEX for s in specs):
            specs.append(self._synthesize_index_chunk(user_specs))
        if self.auto_sqlite and not any(s.kind == EvrKind.SQLITE for s in specs):
            specs.append(self._synthesize_sqlite_chunk(user_specs))

        # Compute pre-provenance Merkle root over all non-CRYPTO chunks
        pre_leaves = [
            _canonical_chunk_leaf_payload(
                path=s.path,
                kind_id=int(s.kind),
                codec_id=int(s.codec),
                raw_len=len(s.raw_bytes),
                low_flags=int(s.flags) & 0xFFFFFFFF,
                content_hash_bytes=_blake2b_256(s.raw_bytes),
                mime_type=s.mime_type,
                metadata=s.metadata,
            )
            for s in specs
        ]
        pre_merkle_root = compute_rfc6962_merkle_root(pre_leaves)
        pre_merkle_hex = pre_merkle_root.hex()

        created_ns = self.created_unix_ns

        # Compute flags before building preamble/provenance so flags are bound into MAC
        kinds_present = {s.kind for s in specs} | {EvrKind.CRYPTO}
        flags = int(EvrFlags.MERKLE_VERIFIED)
        if self.polyglot:
            flags |= int(EvrFlags.POLYGLOT)
        if self.sign_key:
            flags |= int(EvrFlags.SIGNED)
        if EvrKind.INDEX in kinds_present:
            flags |= int(EvrFlags.HAS_INDEX)
        if EvrKind.SQLITE in kinds_present:
            flags |= int(EvrFlags.HAS_SQLITE)
        if EvrKind.TENSOR in kinds_present:
            flags |= int(EvrFlags.HAS_TENSORS)
        if EvrKind.SPATIAL in kinds_present:
            flags |= int(EvrFlags.HAS_SPATIAL)
        if EvrKind.GRAPH in kinds_present:
            flags |= int(EvrFlags.HAS_GRAPH)
        if EvrKind.STATE in kinds_present:
            flags |= int(EvrFlags.HAS_STATE)

        modalities_list = sorted({s.kind.name for s in specs} | {"CRYPTO"} | ({"POLYGLOT"} if self.polyglot else set()))

        # Build optional Polyglot Preamble BEFORE signing so preamble bytes are cryptographically bound!
        preamble = b""
        if self.polyglot:
            preview_chunks: list[dict[str, Any]] = []
            for s in specs:
                prev_txt = ""
                if s.kind in (EvrKind.DOCUMENT, EvrKind.CODE, EvrKind.STATE, EvrKind.CRYPTO, EvrKind.GRAPH):
                    prev_txt = s.raw_bytes.decode("utf-8", errors="ignore")[:1200]
                preview_chunks.append(
                    {
                        "path": s.path,
                        "kind": s.kind.name,
                        "raw_len": len(s.raw_bytes),
                        "metadata": s.metadata,
                        "preview_text": prev_txt,
                    }
                )
            preamble = self._build_polyglot_preamble(
                {
                    "version": EVR_SPEC_VERSION,
                    "title": self.title,
                    "author": self.author,
                    "alignment": EVR_ALIGNMENT,
                    "merkle_root": pre_merkle_hex,
                    "modalities": modalities_list,
                    "chunks": preview_chunks,
                }
            )
            assert len(preamble) % EVR_ALIGNMENT == 0

        preamble_hash_hex = _blake2b_256(preamble).hex()
        crypto_envelope: dict[str, Any] = {
            "schema": "evr.crypto.v1",
            "hash_algorithm": "BLAKE2b-256",
            "merkle_standard": "RFC-6962-Domain-Separated-BLAKE2b",
            "merkle_leaf_count": len(pre_leaves),
            "pre_merkle_root": pre_merkle_hex,
            "preamble_hash": preamble_hash_hex,
            "created_unix_ns": created_ns,
            "title": self.title,
            "author": self.author,
            "description": self.description,
            "flags": flags,
        }
        if self.sign_key:
            sig_msg = _canonical_signing_message(
                pre_merkle_root=pre_merkle_root,
                preamble_bytes=preamble,
                title=self.title,
                author=self.author,
                description=self.description,
                created_unix_ns=created_ns,
                flags=flags,
                alignment=EVR_ALIGNMENT,
                polyglot=self.polyglot,
            )
            sig = hmac.new(self.sign_key.encode("utf-8"), sig_msg, hashlib.sha256).hexdigest()
            crypto_envelope["signature_scheme"] = "HMAC-SHA256"
            crypto_envelope["signature"] = sig

        crypto_raw = json.dumps(crypto_envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
        specs.append(
            EvrChunkSpec(
                path="__evr__/provenance.json",
                kind=EvrKind.CRYPTO,
                raw_bytes=crypto_raw,
                codec=EvrCodec.RAW,
                mime_type="application/x-evr-crypto+json",
                metadata=crypto_envelope,
            )
        )

        # Compute final Merkle root including the CRYPTO chunk
        final_leaves = [
            _canonical_chunk_leaf_payload(
                path=s.path,
                kind_id=int(s.kind),
                codec_id=int(s.codec),
                raw_len=len(s.raw_bytes),
                low_flags=int(s.flags) & 0xFFFFFFFF,
                content_hash_bytes=_blake2b_256(s.raw_bytes),
                mime_type=s.mime_type,
                metadata=s.metadata,
            )
            for s in specs
        ]
        final_merkle_root = compute_rfc6962_merkle_root(final_leaves)
        final_merkle_hex = final_merkle_root.hex()

        superblock_offset = len(preamble)
        assert superblock_offset % EVR_ALIGNMENT == 0

        cursor = superblock_offset + SUPERBLOCK_STRUCT.size
        assert cursor % EVR_ALIGNMENT == 0

        hash_to_offset_len: dict[tuple[str, int], tuple[int, int, int]] = {}
        payload_blobs: list[bytes] = []
        records: list[EvrChunkRecord] = []
        toc_entries_bin: list[bytes] = []

        for idx, sp in enumerate(specs):
            content_hash = _blake2b_256(sp.raw_bytes)
            content_hash_hex = content_hash.hex()
            raw_crc = _crc32(sp.raw_bytes)
            dedup_key = (content_hash_hex, int(sp.codec))

            if dedup_key in hash_to_offset_len:
                data_offset, stored_len, stored_crc = hash_to_offset_len[dedup_key]
                dedup = True
            else:
                if sp.codec == EvrCodec.ZLIB:
                    encoded = zlib.compress(sp.raw_bytes, level=6)
                else:
                    encoded = sp.raw_bytes
                data_offset = cursor
                assert data_offset % EVR_ALIGNMENT == 0
                stored_len = len(encoded)
                stored_crc = _crc32(encoded)
                pad = _pad64_bytes(stored_len, fill=b"\x00")
                payload_blobs.append(encoded + pad)
                cursor += stored_len + len(pad)
                assert cursor % EVR_ALIGNMENT == 0
                hash_to_offset_len[dedup_key] = (data_offset, stored_len, stored_crc)
                dedup = False

            low_flags = int(sp.flags) & 0xFFFFFFFF
            packed_toc_flags = ((stored_crc & 0xFFFFFFFF) << 32) | low_flags

            rec = EvrChunkRecord(
                index=idx,
                path=sp.path,
                kind=sp.kind,
                codec=sp.codec,
                data_offset=data_offset,
                stored_len=stored_len,
                raw_len=len(sp.raw_bytes),
                flags=low_flags,
                content_hash_hex=content_hash_hex,
                crc32=raw_crc,
                stored_crc32=stored_crc,
                mime_type=sp.mime_type,
                deduplicated=dedup,
                metadata=sp.metadata,
            )
            records.append(rec)

            name_bytes = sp.path.encode("utf-8")[:51]
            name_padded = name_bytes.ljust(52, b"\x00")
            toc_entries_bin.append(
                TOC_ENTRY_STRUCT.pack(
                    int(sp.kind),
                    int(sp.codec),
                    data_offset,
                    stored_len,
                    len(sp.raw_bytes),
                    packed_toc_flags,
                    content_hash,
                    raw_crc,
                    name_padded,
                )
            )

        toc_offset = cursor
        assert toc_offset % EVR_ALIGNMENT == 0
        binary_toc_blob = b"".join(toc_entries_bin)
        assert len(binary_toc_blob) % EVR_ALIGNMENT == 0

        manifest_offset = toc_offset + len(binary_toc_blob)
        assert manifest_offset % EVR_ALIGNMENT == 0

        manifest_obj: dict[str, Any] = {
            "evr_version": EVR_SPEC_VERSION,
            "title": self.title,
            "author": self.author,
            "description": self.description,
            "created_unix_ns": created_ns,
            "alignment": EVR_ALIGNMENT,
            "polyglot": self.polyglot,
            "flags": flags,
            "superblock_offset": superblock_offset,
            "toc_offset": toc_offset,
            "manifest_offset": manifest_offset,
            "chunk_count": len(records),
            "modalities": modalities_list,
            "preamble_hash": preamble_hash_hex,
            "merkle_root": final_merkle_hex,
            "signature": crypto_envelope.get("signature"),
            "chunks": [r.to_dict() for r in records],
        }
        manifest_raw = json.dumps(manifest_obj, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode(
            "utf-8"
        )
        manifest_len = len(manifest_raw)
        manifest_pad = _pad64_bytes(manifest_len, fill=b" ")

        total_toc_uncompressed = len(binary_toc_blob) + manifest_len
        total_toc_stored = len(binary_toc_blob) + manifest_len + len(manifest_pad)

        sb_without_crc = struct.pack(
            "<4sHHIIQQQQQ",
            EVR_MAGIC_START,
            1,
            0,
            flags,
            EVR_ALIGNMENT,
            created_ns,
            len(records),
            toc_offset,
            total_toc_uncompressed,
            total_toc_stored,
        )
        sb_crc = _crc32(sb_without_crc)
        superblock_bytes = SUPERBLOCK_STRUCT.pack(
            EVR_MAGIC_START,
            1,
            0,
            flags,
            EVR_ALIGNMENT,
            created_ns,
            len(records),
            toc_offset,
            total_toc_uncompressed,
            total_toc_stored,
            sb_crc,
            EVR_MAGIC_RESERVED,
        )

        footer_prefix = struct.pack(
            "<4sIQQQQ16s",
            EVR_MAGIC_START,
            flags,
            superblock_offset,
            toc_offset,
            manifest_offset,
            manifest_len,
            final_merkle_root[:16],
        )
        footer_crc = _crc32(footer_prefix)
        footer_bytes = FOOTER_STRUCT.pack(
            EVR_MAGIC_START,
            flags,
            superblock_offset,
            toc_offset,
            manifest_offset,
            manifest_len,
            final_merkle_root[:16],
            footer_crc,
            EVR_MAGIC_END,
        )

        out = b"".join(
            [
                preamble,
                superblock_bytes,
                *payload_blobs,
                binary_toc_blob,
                manifest_raw,
                manifest_pad,
                footer_bytes,
            ]
        )
        assert len(out) % EVR_ALIGNMENT == 0
        return out

    def write(self, path: str | Path, make_executable: bool | None = None) -> Path:
        out_path = Path(path).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        blob = self.build_bytes()
        out_path.write_bytes(blob)
        if make_executable or (make_executable is None and self.polyglot):
            mode = out_path.stat().st_mode
            out_path.chmod(mode | 0o111)
        return out_path


class EvrReader:
    """Zero-copy `mmap`-capable reader, verifier, search engine & SQL executor for `.evr` capsules."""

    def __init__(self, source: str | Path | bytes) -> None:
        self._file_obj: Any = None
        self._mmap_obj: mmap.mmap | None = None
        self._child_views: list[memoryview] = []
        self._cached_index: dict[str, Any] | None = None
        if isinstance(source, (str, Path)):
            self.path: Path | None = Path(source).expanduser().resolve()
            self._file_obj = open(self.path, "rb")
            self._mmap_obj = mmap.mmap(self._file_obj.fileno(), 0, access=mmap.ACCESS_READ)
            self._buf: memoryview = memoryview(self._mmap_obj)
        else:
            self.path = None
            self._buf = memoryview(source)

        buf_len = len(self._buf)
        if buf_len < 128 or (buf_len % EVR_ALIGNMENT != 0):
            self.close()
            raise ValueError(f"Invalid .evr container: length {buf_len} must be >= 128 and a multiple of 64")

        footer_raw = bytes(self._buf[buf_len - 64 :])
        (
            f_magic_start,
            self.footer_flags,
            self.superblock_offset,
            self.toc_offset,
            self.manifest_offset,
            self.manifest_len,
            self.merkle_root_prefix,
            self.footer_crc32,
            f_magic_end,
        ) = FOOTER_STRUCT.unpack(footer_raw)

        if f_magic_start != EVR_MAGIC_START or f_magic_end != EVR_MAGIC_END:
            self.close()
            raise ValueError("Invalid .evr footer magic: expected 'EVR\\x01' ... '1RVE'")

        expected_fcrc = _crc32(footer_raw[:56])
        if expected_fcrc != self.footer_crc32:
            self.close()
            raise ValueError(f"Corrupted .evr footer CRC32: expected {expected_fcrc:#x}, got {self.footer_crc32:#x}")

        if (
            self.superblock_offset % EVR_ALIGNMENT != 0
            or self.superblock_offset + 64 > buf_len - 64
            or self.toc_offset % EVR_ALIGNMENT != 0
            or self.toc_offset < self.superblock_offset + 64
            or self.manifest_offset % EVR_ALIGNMENT != 0
            or self.manifest_offset < self.toc_offset
            or self.manifest_offset + self.manifest_len > buf_len - 64
        ):
            self.close()
            raise ValueError("Invalid .evr section offsets in footer")

        sb_raw = bytes(self._buf[self.superblock_offset : self.superblock_offset + 64])
        (
            sb_magic,
            self.version_major,
            self.version_minor,
            self.flags,
            self.alignment,
            self.created_unix_ns,
            self.chunk_count,
            sb_toc_offset,
            self.toc_uncompressed_len,
            self.toc_compressed_len,
            self.superblock_crc32,
            sb_reserved,
        ) = SUPERBLOCK_STRUCT.unpack(sb_raw)

        if sb_magic != EVR_MAGIC_START or sb_reserved != EVR_MAGIC_RESERVED:
            self.close()
            raise ValueError("Invalid .evr superblock magic")
        if _crc32(sb_raw[:56]) != self.superblock_crc32:
            self.close()
            raise ValueError("Corrupted .evr superblock CRC32")
        if sb_toc_offset != self.toc_offset:
            self.close()
            raise ValueError("Superblock/Footer TOC offset mismatch")
        if self.toc_offset + self.chunk_count * TOC_ENTRY_STRUCT.size != self.manifest_offset:
            self.close()
            raise ValueError("Invalid .evr chunk_count: binary TOC size does not match manifest_offset")

        self.binary_toc: list[dict[str, Any]] = []
        for i in range(self.chunk_count):
            entry_off = self.toc_offset + i * TOC_ENTRY_STRUCT.size
            entry_raw = bytes(self._buf[entry_off : entry_off + TOC_ENTRY_STRUCT.size])
            kind_id, codec_id, d_off, s_len, r_len, packed_flags, c_hash, c_crc, name_padded = TOC_ENTRY_STRUCT.unpack(
                entry_raw
            )
            self.binary_toc.append(
                {
                    "index": i,
                    "kind_id": kind_id,
                    "codec_id": codec_id,
                    "data_offset": d_off,
                    "stored_len": s_len,
                    "raw_len": r_len,
                    "flags": packed_flags & 0xFFFFFFFF,
                    "stored_crc32": (packed_flags >> 32) & 0xFFFFFFFF,
                    "content_hash": c_hash.hex(),
                    "crc32": c_crc,
                    "short_name": name_padded.split(b"\x00", 1)[0].decode("utf-8", errors="ignore"),
                    "name_padded": name_padded,
                }
            )

        manifest_bytes = bytes(self._buf[self.manifest_offset : self.manifest_offset + self.manifest_len])
        self.manifest: dict[str, Any] = json.loads(manifest_bytes.decode("utf-8"))
        self.chunks: list[dict[str, Any]] = self.manifest.get("chunks", [])
        self._path_index: dict[str, int] = {}
        self._duplicate_paths: list[str] = []
        for i, c in enumerate(self.chunks):
            p = str(c.get("path", ""))
            if p in self._path_index:
                self._duplicate_paths.append(p)
            self._path_index[p] = i

    def close(self) -> None:
        for cv in getattr(self, "_child_views", []):
            try:
                cv.release()
            except BufferError:
                pass
        self._child_views = []
        if hasattr(self, "_buf") and self._buf is not None:
            try:
                self._buf.release()
            except BufferError:
                pass
        if self._mmap_obj is not None:
            try:
                self._mmap_obj.close()
                self._mmap_obj = None
            except BufferError:
                return
        if self._file_obj is not None:
            self._file_obj.close()
            self._file_obj = None

    def __enter__(self) -> EvrReader:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def get_chunk_meta(self, path_or_index: str | int) -> dict[str, Any]:
        if isinstance(path_or_index, int):
            if path_or_index < 0 or path_or_index >= len(self.chunks):
                raise IndexError(f"Chunk index {path_or_index} out of range")
            return self.chunks[path_or_index]
        if path_or_index not in self._path_index:
            raise KeyError(f"Chunk '{path_or_index}' not found in .evr capsule")
        return self.chunks[self._path_index[path_or_index]]

    def _validate_chunk_bounds(self, idx: int, meta: dict[str, Any]) -> tuple[int, int, int, int]:
        if idx < 0 or idx >= len(self.binary_toc):
            raise ValueError(f"Chunk index {idx} out of binary TOC range")
        bt = self.binary_toc[idx]
        off = int(meta["data_offset"])
        s_len = int(meta["stored_len"])
        r_len = int(meta["raw_len"])
        codec_id = int(meta["codec_id"])
        if (
            bt["data_offset"] != off
            or bt["stored_len"] != s_len
            or bt["raw_len"] != r_len
            or bt["codec_id"] != codec_id
            or bt["kind_id"] != int(meta["kind_id"])
            or bt["content_hash"] != meta["content_hash"]
            or bt["crc32"] != int(meta["crc32"])
        ):
            raise ValueError(f"Chunk #{idx} ({meta.get('path')}) binary TOC / JSON manifest mismatch")
        if codec_id not in (int(EvrCodec.RAW), int(EvrCodec.ZLIB)):
            raise ValueError(f"Chunk #{idx} has unsupported codec_id {codec_id}")
        if off % EVR_ALIGNMENT != 0:
            raise ValueError(f"Chunk #{idx} offset {off} violates 64-byte alignment")
        if off < self.superblock_offset + 64 or off > self.toc_offset or off + s_len > self.toc_offset:
            raise ValueError(f"Chunk #{idx} range [{off}..{off + s_len}] out of payload section bounds")
        if codec_id == int(EvrCodec.RAW) and s_len != r_len:
            raise ValueError(f"Chunk #{idx} RAW stored_len {s_len} != raw_len {r_len}")
        return off, s_len, r_len, codec_id

    def mmap_slice(self, path_or_index: str | int) -> memoryview:
        """
        Return a zero-copy `memoryview` directly into the 64-byte-aligned payload region.
        Requires the chunk to be stored with `EvrCodec.RAW`.
        """
        meta = self.get_chunk_meta(path_or_index)
        idx = int(meta["index"])
        off, s_len, _, codec_id = self._validate_chunk_bounds(idx, meta)
        if codec_id != int(EvrCodec.RAW):
            raise ValueError(f"Chunk '{meta['path']}' is compressed ({meta['codec']}); use read_chunk() instead")
        mv = self._buf[off : off + s_len]
        self._child_views.append(mv)
        return mv

    def read_chunk(self, path_or_index: str | int) -> bytes:
        meta = self.get_chunk_meta(path_or_index)
        idx = int(meta["index"])
        off, s_len, r_len, codec_id = self._validate_chunk_bounds(idx, meta)
        stored = bytes(self._buf[off : off + s_len])
        bt = self.binary_toc[idx]
        if _crc32(stored) != int(bt["stored_crc32"]):
            raise ValueError(f"Chunk #{idx} ({meta['path']}) stored payload CRC32 mismatch")
        if codec_id == int(EvrCodec.ZLIB):
            dobj = zlib.decompressobj()
            raw = dobj.decompress(stored, r_len + 1)
            if dobj.unconsumed_tail or len(dobj.flush(1)) > 0 or not dobj.eof or len(raw) != r_len:
                raise ValueError(
                    f"Chunk #{idx} ({meta['path']}) decompression length mismatch (expected {r_len} bytes)"
                )
        else:
            raw = stored
        if _crc32(raw) != int(meta["crc32"]) or _blake2b_256(raw).hex() != meta["content_hash"]:
            raise ValueError(f"Chunk #{idx} ({meta['path']}) content digest mismatch")
        return raw

    def read_tensor(self, path_or_index: str | int) -> dict[str, Any]:
        """Read a `TENSOR` chunk via zero-copy `mmap_slice` and unpack its metadata & values."""
        meta = self.get_chunk_meta(path_or_index)
        if meta["kind"] != "TENSOR":
            raise ValueError(f"Chunk '{meta['path']}' has kind {meta['kind']}, expected TENSOR")
        mv = self.mmap_slice(path_or_index)
        t_meta = meta.get("metadata", {})
        dtype = str(t_meta.get("dtype", "f32"))
        shape = list(t_meta.get("shape", []))
        numel = int(t_meta.get("numel", 0))
        if dtype not in TENSOR_DTYPES or not shape or any(int(d) <= 0 for d in shape):
            mv.release()
            raise ValueError(f"Tensor '{meta['path']}' has invalid dtype/shape metadata")
        expected_numel = 1
        for d in shape:
            expected_numel *= int(d)
        _, fmt_char, width = TENSOR_DTYPES[dtype]
        if numel != expected_numel or len(mv) != numel * width:
            mv.release()
            raise ValueError(f"Tensor '{meta['path']}' storage size {len(mv)} != {expected_numel * width}")

        values: list[float | int] = []
        if dtype in ("f32", "f64", "f16", "i32", "i64", "u8", "i8"):
            values = list(struct.unpack(f"<{numel}{fmt_char}", mv))
        elif dtype == "bf16":
            u16s = struct.unpack(f"<{numel}H", mv)
            values = [_bf16_u16_to_float(u) for u in u16s]
        mv.release()
        return {
            "path": meta["path"],
            "data_offset": meta["data_offset"],
            "aligned_64": (int(meta["data_offset"]) % EVR_ALIGNMENT == 0),
            "dtype": dtype,
            "shape": shape,
            "strides": t_meta.get("strides", []),
            "numel": numel,
            "min": t_meta.get("min"),
            "max": t_meta.get("max"),
            "mean": t_meta.get("mean"),
            "l2_norm": t_meta.get("l2_norm"),
            "values": values,
        }

    def prove_chunk(self, path_or_index: str | int) -> dict[str, Any]:
        """Generate an RFC-6962-shaped Merkle inclusion proof for any chunk in the capsule."""
        target_meta = self.get_chunk_meta(path_or_index)
        target_idx = int(target_meta["index"])
        leaves: list[bytes] = []
        for ch in self.chunks:
            raw = self.read_chunk(int(ch["index"]))
            leaf = _canonical_chunk_leaf_payload(
                path=ch["path"],
                kind_id=int(ch["kind_id"]),
                codec_id=int(ch["codec_id"]),
                raw_len=len(raw),
                low_flags=int(ch.get("flags", 0)) & 0xFFFFFFFF,
                content_hash_bytes=_blake2b_256(raw),
                mime_type=str(ch.get("mime_type", "application/octet-stream")),
                metadata=ch.get("metadata") or {},
            )
            leaves.append(leaf)
        proof = build_rfc6962_inclusion_proof(leaves, target_idx)
        verified = verify_rfc6962_inclusion_proof(leaves[target_idx], proof, self.manifest["merkle_root"])
        return {
            "chunk_index": target_idx,
            "path": target_meta["path"],
            "merkle_root": self.manifest["merkle_root"],
            "proof_length": len(proof),
            "proof": proof,
            "verified": verified,
        }

    def verify(self, sign_key: str | None = None) -> dict[str, Any]:
        """
        Perform 6-Gate Cryptographic & Structural Verification on the `.evr` capsule:
          1. Superblock & Footer version, alignment, flags, and CRC32 parity
          2. 64-byte SIMD/mmap alignment & section bounds invariant on all chunks & TOC
          3. Binary TOC <-> JSON Manifest 100% field-by-field parity & duplicate/path safety
          4. Per-chunk BLAKE2b-256 content hash, stored/raw IEEE CRC32 & tensor shape integrity
          5. RFC-6962-shaped Domain-Separated Merkle Root reconstruction (binding chunk metadata)
          6. Keyed HMAC-SHA256 provenance signature check (binding Merkle root, preamble, and header)
        """
        errors: list[str] = []
        if self.version_major != 1 or self.version_minor != 0:
            errors.append(f"Unsupported .evr version {self.version_major}.{self.version_minor} (expected 1.0)")
        if self.alignment != EVR_ALIGNMENT:
            errors.append(f"Invalid .evr alignment {self.alignment} (expected {EVR_ALIGNMENT})")
        if self.flags != self.footer_flags or self.flags != int(self.manifest.get("flags", -1)):
            errors.append("Superblock, Footer, and Manifest flags mismatch")
        if self.superblock_offset % EVR_ALIGNMENT != 0:
            errors.append(f"Superblock offset {self.superblock_offset} violates 64B alignment")
        if self.toc_offset % EVR_ALIGNMENT != 0:
            errors.append(f"TOC offset {self.toc_offset} violates 64B alignment")
        if len(self.chunks) != self.chunk_count:
            errors.append(f"Manifest chunk count {len(self.chunks)} != superblock chunk_count {self.chunk_count}")
        if self._duplicate_paths:
            errors.append(f"Duplicate chunk paths detected: {sorted(set(self._duplicate_paths))}")

        preamble_bytes = bytes(self._buf[: self.superblock_offset])
        if _blake2b_256(preamble_bytes).hex() != str(self.manifest.get("preamble_hash", "")):
            errors.append("Polyglot preamble BLAKE2b-256 hash mismatch with manifest")

        leaves: list[bytes] = []
        for idx, ch in enumerate(self.chunks):
            p_str = str(ch.get("path", ""))
            is_internal = p_str in (
                "__evr__/graph.json",
                "__evr__/index.json",
                "__evr__/catalog.sqlite",
                "__evr__/provenance.json",
            )
            try:
                _validate_chunk_path(p_str, allow_internal=is_internal)
            except ValueError as exc:
                errors.append(str(exc))

            if idx >= len(self.binary_toc):
                errors.append(f"Chunk #{idx} missing from binary TOC")
                continue
            bt = self.binary_toc[idx]
            expected_name_padded = p_str.encode("utf-8")[:51].ljust(52, b"\x00")
            if (
                bt["data_offset"] != int(ch.get("data_offset", -1))
                or bt["stored_len"] != int(ch.get("stored_len", -1))
                or bt["raw_len"] != int(ch.get("raw_len", -1))
                or bt["codec_id"] != int(ch.get("codec_id", -1))
                or bt["kind_id"] != int(ch.get("kind_id", -1))
                or bt["flags"] != (int(ch.get("flags", 0)) & 0xFFFFFFFF)
                or bt["stored_crc32"] != int(ch.get("stored_crc32", -1))
                or bt["content_hash"] != str(ch.get("content_hash", ""))
                or bt["crc32"] != int(ch.get("crc32", -1))
                or bt["name_padded"] != expected_name_padded
            ):
                errors.append(f"Chunk #{idx} ({p_str}) binary TOC mismatch with manifest")

            try:
                raw = self.read_chunk(idx)
            except Exception as exc:
                errors.append(f"Chunk #{idx} ({p_str}) read/integrity failure: {exc}")
                continue

            if ch.get("kind") == "TENSOR":
                try:
                    _ = self.read_tensor(idx)
                except Exception as exc:
                    errors.append(f"Chunk #{idx} ({p_str}) invalid TENSOR structure: {exc}")

            leaves.append(
                _canonical_chunk_leaf_payload(
                    path=p_str,
                    kind_id=int(ch["kind_id"]),
                    codec_id=int(ch["codec_id"]),
                    raw_len=len(raw),
                    low_flags=int(ch.get("flags", 0)) & 0xFFFFFFFF,
                    content_hash_bytes=_blake2b_256(raw),
                    mime_type=str(ch.get("mime_type", "application/octet-stream")),
                    metadata=ch.get("metadata") or {},
                )
            )

        reconstructed_root = compute_rfc6962_merkle_root(leaves)
        if reconstructed_root.hex() != self.manifest.get("merkle_root"):
            errors.append(
                f"Merkle root mismatch: expected {self.manifest.get('merkle_root')}, got {reconstructed_root.hex()}"
            )
        if reconstructed_root[:16] != self.merkle_root_prefix:
            errors.append("Footer 128-bit Merkle root prefix mismatch")

        sig_verified: bool | None = None
        if sign_key is not None:
            if (
                not self.chunks
                or self.chunks[-1].get("path") != "__evr__/provenance.json"
                or self.chunks[-1].get("kind") != "CRYPTO"
                or len(leaves) != len(self.chunks)
            ):
                sig_verified = False
                errors.append("Missing or misplaced __evr__/provenance.json when sign_key verification is required")
            else:
                try:
                    prov = json.loads(self.read_chunk("__evr__/provenance.json").decode("utf-8"))
                    pre_leaves = leaves[:-1]
                    pre_root = compute_rfc6962_merkle_root(pre_leaves)
                    if (
                        prov.get("title") != self.manifest.get("title")
                        or prov.get("author") != self.manifest.get("author")
                        or prov.get("description") != self.manifest.get("description")
                        or int(prov.get("created_unix_ns", -1)) != int(self.created_unix_ns)
                        or int(prov.get("flags", -1)) != int(self.flags)
                        or prov.get("preamble_hash") != _blake2b_256(preamble_bytes).hex()
                    ):
                        sig_verified = False
                        errors.append("Provenance envelope header mismatch with capsule manifest/superblock")
                    else:
                        sig_msg = _canonical_signing_message(
                            pre_merkle_root=pre_root,
                            preamble_bytes=preamble_bytes,
                            title=str(self.manifest.get("title", "")),
                            author=str(self.manifest.get("author", "")),
                            description=str(self.manifest.get("description", "")),
                            created_unix_ns=int(self.created_unix_ns),
                            flags=int(self.flags),
                            alignment=int(self.alignment),
                            polyglot=bool(self.flags & int(EvrFlags.POLYGLOT)),
                        )
                        expected_sig = hmac.new(sign_key.encode("utf-8"), sig_msg, hashlib.sha256).hexdigest()
                        actual_sig = str(prov.get("signature", ""))
                        sig_verified = bool(actual_sig) and hmac.compare_digest(expected_sig, actual_sig)
                        if not sig_verified:
                            errors.append("Cryptographic HMAC-SHA256 provenance signature verification failed")
                except Exception as exc:
                    sig_verified = False
                    errors.append(f"Provenance verification error: {exc}")

        return {
            "valid": len(errors) == 0,
            "spec_version": self.manifest.get("evr_version", EVR_SPEC_VERSION),
            "polyglot": bool(self.flags & int(EvrFlags.POLYGLOT)),
            "alignment": self.alignment,
            "all_chunks_64b_aligned": all(
                int(c.get("data_offset", 1)) % EVR_ALIGNMENT == 0
                and self.superblock_offset + 64 <= int(c.get("data_offset", 0)) <= self.toc_offset
                for c in self.chunks
            ),
            "chunk_count": len(self.chunks),
            "modalities": self.manifest.get("modalities", []),
            "merkle_root": self.manifest.get("merkle_root"),
            "signature_verified": sig_verified,
            "errors": errors,
        }

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """
        Execute Hybrid Search (BM25 + 64-d Feature-Hashed Cosine Similarity)
        directly against the embedded `__evr__/index.json` stream without extracting files.
        """
        if "__evr__/index.json" not in self._path_index:
            return []
        if self._cached_index is None:
            self._cached_index = json.loads(self.read_chunk("__evr__/index.json").decode("utf-8"))
        idx_data = self._cached_index
        docs: list[dict[str, Any]] = idx_data.get("docs", [])
        postings: dict[str, list[list[int]]] = idx_data.get("postings", {})
        avgdl = float(idx_data.get("avgdl", 1.0)) or 1.0
        n_docs = len(docs)
        if n_docs == 0:
            return []

        q_tokens = TOKEN_RE.findall(_fold_text(query))
        q_vec = _semantic_embed_64d(query)

        bm25_scores: dict[int, float] = {}
        k1 = 1.5
        b = 0.75
        for tok in q_tokens:
            plist = postings.get(tok, [])
            if not plist:
                continue
            df = len(plist)
            idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
            for doc_id, tf in plist:
                dl = max(int(docs[doc_id].get("doc_len", 1)), 1)
                denom = tf + k1 * (1.0 - b + b * (dl / avgdl))
                bm25_scores[doc_id] = bm25_scores.get(doc_id, 0.0) + idf * ((tf * (k1 + 1.0)) / denom)

        results: list[dict[str, Any]] = []
        for doc in docs:
            doc_id = int(doc["doc_id"])
            lex = bm25_scores.get(doc_id, 0.0)
            d_vec = doc.get("vec64", [])
            cos = sum(a * b_val for a, b_val in zip(q_vec, d_vec)) if len(d_vec) == 64 else 0.0
            if lex <= 0.0 and cos < 0.15 and q_tokens:
                if not any(t in _fold_text(doc["path"]) for t in q_tokens):
                    continue
                lex = 0.5
            hybrid_score = round(lex + 1.25 * max(cos, 0.0), 4)
            results.append(
                {
                    "path": doc["path"],
                    "kind": doc["kind"],
                    "score": hybrid_score,
                    "bm25": round(lex, 4),
                    "semantic_cosine": round(cos, 4),
                    "snippet": doc.get("snippet", ""),
                }
            )

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    def sql(self, sql_query: str) -> list[dict[str, Any]]:
        """
        Execute a strictly sandboxed, read-only SQL query against `__evr__/catalog.sqlite`.
        Uses `sqlite3.set_authorizer` to deny `ATTACH`, `DETACH`, `PRAGMA`, and all write operations.
        """
        if "__evr__/catalog.sqlite" not in self._path_index:
            raise ValueError("This .evr capsule does not contain an embedded __evr__/catalog.sqlite stream")
        db_bytes = self.read_chunk("__evr__/catalog.sqlite")

        allowed_actions = {
            sqlite3.SQLITE_SELECT,
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_FUNCTION,
            getattr(sqlite3, "SQLITE_RECURSIVE", 33),
        }

        def _readonly_authorizer(
            action_code: int,
            arg1: str | None,
            arg2: str | None,
            db_name: str | None,
            trigger_name: str | None,
        ) -> int:
            if action_code in allowed_actions:
                return sqlite3.SQLITE_OK
            if action_code == sqlite3.SQLITE_PRAGMA and arg1 == "data_version" and arg2 is None:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(db_bytes)
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON;")
            conn.set_authorizer(_readonly_authorizer)
            cur = conn.cursor()
            try:
                cur.execute(sql_query)
            except sqlite3.DatabaseError as exc:
                raise ValueError(f"Read-only SQLite sandbox rejected query: {exc}") from exc
            rows = [dict(r) for r in cur.fetchmany(10000)]
            return rows
        finally:
            if conn is not None:
                conn.close()
            tmp_path.unlink(missing_ok=True)

    def extract_all(self, dest_dir: str | Path, include_internal: bool = False) -> list[Path]:
        dest = Path(dest_dir).expanduser().resolve()
        dest.mkdir(parents=True, exist_ok=True)
        extracted: list[Path] = []
        for idx, ch in enumerate(self.chunks):
            rel = str(ch["path"])
            is_internal = rel.startswith("__evr__/")
            if is_internal and not include_internal:
                continue
            clean_rel = _validate_chunk_path(rel, allow_internal=include_internal)
            target = (dest / clean_rel).resolve()
            try:
                target.relative_to(dest)
            except ValueError as exc:
                raise ValueError(f"Unsafe path traversal blocked in .evr chunk: {rel}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.read_chunk(idx))
            extracted.append(target)
        return extracted

    def summary(self) -> dict[str, Any]:
        total_raw = sum(int(c["raw_len"]) for c in self.chunks)
        total_stored = sum(int(c["stored_len"]) for c in self.chunks if not c.get("deduplicated", False))
        return {
            "path": str(self.path) if self.path else "<memory>",
            "file_size_bytes": len(self._buf),
            "evr_version": self.manifest.get("evr_version", EVR_SPEC_VERSION),
            "title": self.manifest.get("title", ""),
            "author": self.manifest.get("author", ""),
            "polyglot": bool(self.flags & int(EvrFlags.POLYGLOT)),
            "alignment": self.alignment,
            "superblock_offset": self.superblock_offset,
            "toc_offset": self.toc_offset,
            "manifest_offset": self.manifest_offset,
            "chunk_count": len(self.chunks),
            "deduplicated_chunks": sum(1 for c in self.chunks if c.get("deduplicated", False)),
            "total_raw_bytes": total_raw,
            "total_unique_stored_bytes": total_stored,
            "modalities": self.manifest.get("modalities", []),
            "merkle_root": self.manifest.get("merkle_root", ""),
            "chunks": self.chunks,
        }


# ---------------------------------------------------------------------------
# Native Rust Reference Kernel (`evr-rs`) for Cross-Language Verification
# ---------------------------------------------------------------------------
RUST_EVR_KERNEL_SRC = r"""
//! Native Rust Reference Engine for the `.evr` ("Everything" v1.0) File Format.
//! Enforces checked arithmetic, 64-byte SIMD/mmap alignment, Superblock/Footer CRC32,
//! version/codec validation, and stored-payload CRC32 verification across RAW and ZLIB chunks.

use std::env;
use std::fs;

const EVR_MAGIC_START: [u8; 4] = *b"EVR\x01";
const EVR_MAGIC_RESERVED: [u8; 4] = *b"EVR!";
const EVR_MAGIC_END: [u8; 4] = *b"1RVE";
const EVR_ALIGNMENT: u64 = 64;

pub fn crc32_ieee(data: &[u8]) -> u32 {
    let mut crc: u32 = 0xFFFF_FFFF;
    for &byte in data {
        crc ^= byte as u32;
        for _ in 0..8 {
            if (crc & 1) != 0 {
                crc = (crc >> 1) ^ 0xEDB8_8320;
            } else {
                crc >>= 1;
            }
        }
    }
    !crc
}

fn read_u16_le(buf: &[u8], off: usize) -> Option<u16> {
    let s = buf.get(off..off.checked_add(2)?)?;
    Some(u16::from_le_bytes([s[0], s[1]]))
}

fn read_u32_le(buf: &[u8], off: usize) -> Option<u32> {
    let s = buf.get(off..off.checked_add(4)?)?;
    Some(u32::from_le_bytes([s[0], s[1], s[2], s[3]]))
}

fn read_u64_le(buf: &[u8], off: usize) -> Option<u64> {
    let s = buf.get(off..off.checked_add(8)?)?;
    Some(u64::from_le_bytes([s[0], s[1], s[2], s[3], s[4], s[5], s[6], s[7]]))
}

#[derive(Debug, Clone)]
pub struct EvrRustSummary {
    pub version_major: u16,
    pub version_minor: u16,
    pub flags: u32,
    pub alignment: u32,
    pub superblock_offset: u64,
    pub toc_offset: u64,
    pub manifest_offset: u64,
    pub manifest_len: u64,
    pub chunk_count: u64,
    pub all_aligned_64: bool,
}

pub fn inspect_evr_bytes(buf: &[u8]) -> Result<EvrRustSummary, String> {
    let file_len = buf.len() as u64;
    if file_len < 128 || file_len % EVR_ALIGNMENT != 0 {
        return Err(format!("invalid file length {}", file_len));
    }
    let f_off = buf.len() - 64;
    let footer = buf.get(f_off..).ok_or_else(|| "missing footer".to_string())?;
    if footer.get(0..4) != Some(&EVR_MAGIC_START) || footer.get(60..64) != Some(&EVR_MAGIC_END) {
        return Err("invalid footer magic".to_string());
    }
    let expected_fcrc = crc32_ieee(&footer[0..56]);
    let actual_fcrc = read_u32_le(footer, 56).ok_or("footer crc read failed")?;
    if expected_fcrc != actual_fcrc {
        return Err(format!("footer crc32 mismatch: {:x} != {:x}", expected_fcrc, actual_fcrc));
    }
    let footer_flags = read_u32_le(footer, 4).ok_or("footer flags read failed")?;
    let sb_offset = read_u64_le(footer, 8).ok_or("sb_offset read failed")?;
    let toc_offset = read_u64_le(footer, 16).ok_or("toc_offset read failed")?;
    let manifest_offset = read_u64_le(footer, 24).ok_or("manifest_offset read failed")?;
    let manifest_len = read_u64_le(footer, 32).ok_or("manifest_len read failed")?;

    if sb_offset % EVR_ALIGNMENT != 0
        || toc_offset % EVR_ALIGNMENT != 0
        || manifest_offset % EVR_ALIGNMENT != 0
    {
        return Err("superblock/toc/manifest offsets violate 64B alignment".to_string());
    }
    let sb_end = sb_offset.checked_add(64).ok_or("sb_offset overflow")?;
    let manifest_end = manifest_offset.checked_add(manifest_len).ok_or("manifest_len overflow")?;
    if sb_end > file_len - 64 || toc_offset < sb_end || manifest_offset < toc_offset || manifest_end > file_len - 64 {
        return Err("section offsets out of file bounds".to_string());
    }

    let sb_usize = usize::try_from(sb_offset).map_err(|_| "sb_offset usize overflow")?;
    let sb = buf.get(sb_usize..sb_usize + 64).ok_or("superblock slice out of bounds")?;
    if sb.get(0..4) != Some(&EVR_MAGIC_START) || sb.get(60..64) != Some(&EVR_MAGIC_RESERVED) {
        return Err("invalid superblock magic".to_string());
    }
    let expected_sbcrc = crc32_ieee(&sb[0..56]);
    let actual_sbcrc = read_u32_le(sb, 56).ok_or("sb crc read failed")?;
    if expected_sbcrc != actual_sbcrc {
        return Err("superblock crc32 mismatch".to_string());
    }
    let ver_major = read_u16_le(sb, 4).ok_or("ver_major read failed")?;
    let ver_minor = read_u16_le(sb, 6).ok_or("ver_minor read failed")?;
    let flags = read_u32_le(sb, 8).ok_or("flags read failed")?;
    let alignment = read_u32_le(sb, 12).ok_or("alignment read failed")?;
    let chunk_count = read_u64_le(sb, 24).ok_or("chunk_count read failed")?;
    let sb_toc_offset = read_u64_le(sb, 32).ok_or("sb_toc_offset read failed")?;

    if ver_major != 1 || ver_minor != 0 {
        return Err(format!("unsupported version {}.{}", ver_major, ver_minor));
    }
    if alignment != 64 {
        return Err(format!("unsupported alignment {}", alignment));
    }
    if flags != footer_flags || sb_toc_offset != toc_offset {
        return Err("superblock/footer mismatch".to_string());
    }
    let toc_bytes = chunk_count.checked_mul(128).ok_or("chunk_count overflow")?;
    if toc_offset.checked_add(toc_bytes) != Some(manifest_offset) {
        return Err("binary TOC size does not match manifest_offset".to_string());
    }

    for i in 0..chunk_count {
        let e_off_u64 = toc_offset.checked_add(i * 128).ok_or("entry offset overflow")?;
        let e_off = usize::try_from(e_off_u64).map_err(|_| "entry offset usize overflow")?;
        let entry = buf.get(e_off..e_off + 128).ok_or("entry slice out of bounds")?;
        let kind = read_u32_le(entry, 0).ok_or("kind read failed")?;
        let codec = read_u32_le(entry, 4).ok_or("codec read failed")?;
        let d_off = read_u64_le(entry, 8).ok_or("d_off read failed")?;
        let s_len = read_u64_le(entry, 16).ok_or("s_len read failed")?;
        let r_len = read_u64_le(entry, 24).ok_or("r_len read failed")?;
        let packed_flags = read_u64_le(entry, 32).ok_or("flags read failed")?;
        let expected_raw_crc = read_u32_le(entry, 72).ok_or("crc read failed")?;
        let expected_stored_crc = ((packed_flags >> 32) & 0xFFFF_FFFF) as u32;

        if kind == 0 || kind > 12 {
            return Err(format!("chunk {} invalid kind {}", i, kind));
        }
        if codec > 1 {
            return Err(format!("chunk {} unsupported codec {}", i, codec));
        }
        if d_off % EVR_ALIGNMENT != 0 {
            return Err(format!("chunk {} data_offset {} not 64B aligned", i, d_off));
        }
        let d_end = d_off.checked_add(s_len).ok_or("chunk d_off + s_len overflow")?;
        if d_off < sb_end || d_end > toc_offset {
            return Err(format!("chunk {} range out of payload section bounds", i));
        }
        let d_start_usize = usize::try_from(d_off).map_err(|_| "d_off usize")?;
        let d_end_usize = usize::try_from(d_end).map_err(|_| "d_end usize")?;
        let slice = buf.get(d_start_usize..d_end_usize).ok_or("chunk slice OOB")?;
        let actual_stored_crc = crc32_ieee(slice);
        if actual_stored_crc != expected_stored_crc {
            return Err(format!("chunk {} stored payload crc32 mismatch", i));
        }
        if codec == 0 {
            if s_len != r_len {
                return Err(format!("chunk {} RAW s_len != r_len", i));
            }
            if actual_stored_crc != expected_raw_crc {
                return Err(format!("chunk {} RAW crc32 mismatch", i));
            }
        }
    }

    Ok(EvrRustSummary {
        version_major: ver_major,
        version_minor: ver_minor,
        flags,
        alignment,
        superblock_offset: sb_offset,
        toc_offset,
        manifest_offset,
        manifest_len,
        chunk_count,
        all_aligned_64: true,
    })
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        eprintln!("Usage: evr-rs <file.evr>");
        std::process::exit(1);
    }
    let data = match fs::read(&args[1]) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("{{\"valid\":false,\"error\":\"{}\"}}", e);
            std::process::exit(2);
        }
    };
    match inspect_evr_bytes(&data) {
        Ok(s) => {
            println!(
                "{{\"valid\":true,\"version\":\"{}.{}\",\"alignment\":{},\"superblock_offset\":{},\"toc_offset\":{},\"chunk_count\":{},\"all_aligned_64\":{}}}",
                s.version_major,
                s.version_minor,
                s.alignment,
                s.superblock_offset,
                s.toc_offset,
                s.chunk_count,
                s.all_aligned_64
            );
        }
        Err(e) => {
            eprintln!("{{\"valid\":false,\"error\":\"{}\"}}", e);
            std::process::exit(2);
        }
    }
}
"""


def verify_rust_evr_interop(evr_file: str | Path) -> dict[str, Any]:
    """Compile the zero-dependency Rust `.evr` kernel (`rustc -D warnings`) and verify `evr_file`."""
    rustc = shutil.which("rustc") or str(Path.home() / ".cargo" / "bin" / "rustc")
    if not Path(rustc).is_file():
        return {"rust_available": False, "valid": False, "skipped": True}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        rs_path = tmp / "evr_kernel.rs"
        bin_path = tmp / "evr-rs"
        rs_path.write_text(RUST_EVR_KERNEL_SRC, encoding="utf-8")
        comp = subprocess.run(
            [rustc, "--edition", "2021", "-O", "-D", "warnings", str(rs_path), "-o", str(bin_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if comp.returncode != 0:
            return {"rust_available": True, "valid": False, "compile_error": comp.stderr}
        run = subprocess.run(
            [str(bin_path), str(evr_file)],
            capture_output=True,
            text=True,
            check=False,
        )
        if run.returncode != 0:
            return {"rust_available": True, "valid": False, "returncode": run.returncode, "runtime_error": run.stderr}
        parsed = json.loads(run.stdout.strip())
        parsed["rust_available"] = True
        return parsed


def create_showcase_evr_capsule(
    output_path: str | Path,
    polyglot: bool = True,
    sign_key: str = "sovereign-evr-v1-key",
) -> dict[str, Any]:
    """
    Create a deterministic reference `.evr` ("Everything" v1.0) capsule containing all 12 computing domains.
    """
    builder = EvrBuilder(
        title="Sovereign Everything Capsule (.evr v1.0)",
        author="Ilias Chrysovergis",
        description="Canonical 12-domain reference capsule for the .evr ('Everything') file format.",
        polyglot=polyglot,
        sign_key=sign_key,
    )

    builder.add_text(
        "docs/manifesto.md",
        """---
title: "The .evr ('Everything') Universal File Format"
type: specification
updated: 2026-11-05
tags: [evr, zero-copy, mmap, merkle, polyglot, spatial, tensors]
related:
  - "[[architecture]]"
  - "[[benchmarks]]"
---

# The `.evr` ("Everything" v1.0) File Format

`.evr` unifies documents, source code, relational tables, media, 3D spatial geometry,
64-byte-aligned zero-copy tensors, hybrid BM25 + vector retrieval indexes, SQLite catalogs,
and RFC-6962-shaped Merkle provenance inside a single self-rendering polyglot file.
See [[architecture]] and [[benchmarks]] for fixture schema details.
""",
        kind=EvrKind.DOCUMENT,
    )

    builder.add_text(
        "docs/architecture.md",
        """# 64-Byte Cacheline & SIMD Alignment

Every `RAW` payload in `.evr` begins at an exact multiple of 64 bytes (`offset % 64 == 0`),
enabling zero-copy `mmap` slicing across CPU AVX-512/NEON registers and GPU storage buffers.
""",
        kind=EvrKind.DOCUMENT,
    )

    builder.add_text(
        "src/zero_copy.rs",
        """pub fn verify_alignment(ptr: *const u8) -> bool {
    (ptr as usize) % 64 == 0
}
""",
        kind=EvrKind.CODE,
    )

    builder.add_text(
        "benchmarks/modality_latency.csv",
        """modality,operation,fixture_budget_us,zero_copy
TENSOR,mmap_slice_f32,1.00,true
INDEX,hybrid_bm25_vec64,250.00,false
SQLITE,catalog_select,500.00,true
SPATIAL,mesh_parse_obj,50.00,true
CRYPTO,rfc6962_merkle_verify,250.00,false
""",
        kind=EvrKind.TABLE,
    )

    builder.add_text(
        "media/evr_badge.svg",
        """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 64">
  <rect width="240" height="64" rx="8" fill="#0F172A"/>
  <text x="20" y="40" fill="#38BDF8" font-family="monospace" font-size="22" font-weight="bold">.evr EVERYTHING</text>
</svg>""",
        kind=EvrKind.MEDIA,
    )

    builder.add_text(
        "spatial/sovereign_crystal.obj",
        """# Sovereign .evr 3D Crystal Mesh
v  0.0  1.4  0.0
v -1.0  0.0 -1.0
v  1.0  0.0 -1.0
v  1.0  0.0  1.0
v -1.0  0.0  1.0
v  0.0 -1.4  0.0
f 1 2 3
f 1 3 4
f 1 4 5
f 1 5 2
f 6 3 2
f 6 4 3
f 6 5 4
f 6 2 5
""",
        kind=EvrKind.SPATIAL,
    )

    tensor_vals = [round(math.sin(i * 0.35) * math.cos(i * 0.15), 5) for i in range(32)]
    builder.add_tensor(
        "tensors/attention_projection.tensor",
        values=tensor_vals,
        shape=[4, 8],
        dtype="f32",
        metadata={"layer": "encoder.layer.0.attn", "head_dim": 8},
    )

    builder.add_state(
        "state/brain_snapshot.json",
        {
            "agent": "Antigravity Sovereign Organon",
            "mode": "everything-capsule",
            "active_focus": "Universal .evr v1.0 container specification & 3-runtime engine",
            "invariants": ["64B_SIMD_ALIGNED", "RFC6962_MERKLE", "TRIPLE_POLYGLOT"],
        },
    )

    out = builder.write(output_path)
    with EvrReader(out) as reader:
        ver = reader.verify(sign_key=sign_key)
        summ = reader.summary()
        summ["verification"] = ver
    return summ


# ---------------------------------------------------------------------------
# CLI Integration (`evr` standalone + `skb evr` + `kb evr` + `organon evr`)
# ---------------------------------------------------------------------------
def register_evr_subparser(subparsers: Any, show_all_aliases: bool = False) -> None:
    aliases = ("evr", "everything") if show_all_aliases else ("evr",)
    for idx, name in enumerate(aliases):
        help_txt = (
            "The `.evr` ('Everything' v1.0) universal zero-copy polyglot file format engine"
            if idx == 0 or show_all_aliases
            else None
        )
        p = subparsers.add_parser(name, **({"help": help_txt} if help_txt else {}))
        p.add_argument(
            "action",
            nargs="?",
            default="status",
            choices=(
                "status",
                "pack",
                "inspect",
                "verify",
                "search",
                "sql",
                "tensor",
                "extract",
                "prove",
                "demo",
                "bench",
            ),
            help="EVR action (pack, inspect, verify, search, sql, tensor, extract, prove, demo, bench, status)",
        )
        p.add_argument("target", nargs="?", default="", help="Path to .evr file or input directory/file")
        p.add_argument("extra", nargs="*", help="Additional arguments (query, SQL statement, or input paths)")
        p.add_argument("-o", "--output", default="", help="Output .evr file or destination directory")
        p.add_argument("--title", default="Everything Capsule (.evr)", help="Capsule title")
        p.add_argument("--author", default="Sovereign Organon", help="Capsule author")
        p.add_argument(
            "--polyglot",
            action="store_true",
            help="Emit triple-polyglot (Shell + HTML5/3D + 64B Binary) capsule",
        )
        p.add_argument("--sign-key", default="", help="HMAC-SHA256 signing/verification key")
        p.add_argument(
            "--include-internal",
            action="store_true",
            help="Include __evr__/ internal streams when extracting",
        )
        p.add_argument("--rust", action="store_true", help="Also run native Rust (`rustc`) cross-language verification")
        p.add_argument("-k", "--top-k", type=int, default=5, help="Top-K results for `evr search`")
        p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")


def cmd_evr(args: argparse.Namespace) -> None:
    action = getattr(args, "action", "status") or "status"
    target = getattr(args, "target", "") or ""
    extra: list[str] = list(getattr(args, "extra", []) or [])
    as_json = bool(getattr(args, "json", False))
    sign_key = getattr(args, "sign_key", "") or None

    if action == "status":
        payload = {
            "format": ".evr (Everything Container Format)",
            "spec_version": EVR_SPEC_VERSION,
            "magic_start": "EVR\\x01 (0x45565201)",
            "magic_end": "1RVE (0x31525645)",
            "alignment_bytes": EVR_ALIGNMENT,
            "modalities": [k.name for k in EvrKind],
            "features": [
                "64-byte cacheline/SIMD/mmap zero-copy chunk alignment",
                "BLAKE2b-256 content-addressed deduplication + stored/raw IEEE CRC32",
                "Dual-layer TOC (128-byte binary entries + JSON manifest with full parity check)",
                "Embedded BM25 + 64-d feature-hashed cosine search (__evr__/index.json)",
                "Embedded SQLite3 relational & FTS5 catalog with read-only authorizer (__evr__/catalog.sqlite)",
                "RFC-6962-shaped domain-separated binary Merkle tree & inclusion proofs",
                "Triple-Polyglot executable shell + HTML5/3D Studio + binary container",
                "Cross-language Python <-> Rust bit-exact wire compatibility",
            ],
        }
        if as_json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"=== .evr ('Everything' v{EVR_SPEC_VERSION}) Universal File Format Engine ===")
            print(
                f"  Magic Envelope : {payload['magic_start']} ... {payload['magic_end']} (64B Superblock + 64B Footer)"
            )
            print(f"  SIMD Alignment : {EVR_ALIGNMENT} bytes (zero-copy mmap ready)")
            print(f"  12 Modalities  : {', '.join(payload['modalities'])}")
            for feat in payload["features"]:
                print(f"  - {feat}")
        return

    if action == "demo":
        out_file = Path(args.output or (target if target else "everything_showcase.evr")).expanduser().resolve()
        summ = create_showcase_evr_capsule(out_file, polyglot=True, sign_key=sign_key or "sovereign-evr-v1-key")
        if getattr(args, "rust", False):
            summ["rust_interop"] = verify_rust_evr_interop(out_file)
        if as_json:
            print(json.dumps(summ, indent=2))
        else:
            print(f"=== Created Showcase .evr Capsule: {out_file} ({summ['file_size_bytes']} bytes) ===")
            print(f"  Polyglot HTML5/Shell : {summ['polyglot']} (64B Superblock @ offset {summ['superblock_offset']})")
            print(f"  Chunks & Modalities  : {summ['chunk_count']} chunks across {', '.join(summ['modalities'])}")
            print(f"  RFC 6962 Merkle Root : {summ['merkle_root']}")
            print(f"  6-Gate Verification  : {'PASS' if summ['verification']['valid'] else 'FAIL'}")
        return

    if action == "pack":
        inputs = ([target] if target else []) + extra
        if not inputs:
            print("Error: `evr pack` requires at least one input file or directory.", file=sys.stderr)
            sys.exit(1)
        out_path = Path(args.output or "bundle.evr").expanduser().resolve()
        builder = EvrBuilder(
            title=args.title,
            author=args.author,
            polyglot=bool(args.polyglot),
            sign_key=sign_key,
        )
        for inp in inputs:
            p = Path(inp).expanduser().resolve()
            if not p.exists():
                print(f"Error: input path '{inp}' does not exist.", file=sys.stderr)
                sys.exit(1)
            builder.add_path(p, base_dir=p if p.is_dir() else p.parent)
        written = builder.write(out_path)
        with EvrReader(written) as reader:
            summ = reader.summary()
        if as_json:
            print(json.dumps(summ, indent=2))
        else:
            print(
                f"Packed {summ['chunk_count']} chunks ({summ['total_raw_bytes']} raw bytes -> {summ['file_size_bytes']} file bytes) into {written}"
            )
            print(f"  Modalities  : {', '.join(summ['modalities'])}")
            print(f"  Merkle Root : {summ['merkle_root']}")
        return

    if action == "bench":
        t0 = time.perf_counter()
        with tempfile.TemporaryDirectory() as tmpdir:
            cap_path = Path(tmpdir) / "bench.evr"
            create_showcase_evr_capsule(cap_path, polyglot=True)
            t_pack = (time.perf_counter() - t0) * 1000.0

            with EvrReader(cap_path) as reader:
                t1 = time.perf_counter()
                mv = reader.mmap_slice("tensors/attention_projection.tensor")
                _ = len(mv)
                mv.release()
                t_mmap_us = (time.perf_counter() - t1) * 1_000_000.0

                # Warm index cache then measure search
                _ = reader.search("zero-copy", top_k=1)
                t2 = time.perf_counter()
                hits = reader.search("zero-copy 64-byte alignment SIMD", top_k=3)
                t_search_us = (time.perf_counter() - t2) * 1_000_000.0

                t3 = time.perf_counter()
                ver = reader.verify(sign_key="sovereign-evr-v1-key")
                t_verify_us = (time.perf_counter() - t3) * 1_000_000.0

            rust_res = verify_rust_evr_interop(cap_path)

        bench_out = {
            "pack_12_modalities_ms": round(t_pack, 3),
            "zero_copy_tensor_mmap_us": round(t_mmap_us, 2),
            "embedded_hybrid_search_us": round(t_search_us, 2),
            "rfc6962_6gate_verify_us": round(t_verify_us, 2),
            "top_search_hit": hits[0]["path"] if hits else None,
            "verified": ver["valid"],
            "rust_interop": rust_res,
        }
        if as_json:
            print(json.dumps(bench_out, indent=2))
        else:
            print("=== .evr ('Everything' v1.0) Performance Benchmark ===")
            print(f"  Pack 12-Modality Polyglot Capsule : {bench_out['pack_12_modalities_ms']:.2f} ms")
            print(f"  Zero-Copy 64B Tensor mmap Slice   : {bench_out['zero_copy_tensor_mmap_us']:.2f} us")
            print(f"  Embedded Hybrid BM25+Vec64 Search : {bench_out['embedded_hybrid_search_us']:.2f} us")
            print(f"  RFC 6962 6-Gate Merkle Verify     : {bench_out['rfc6962_6gate_verify_us']:.2f} us")
            print(f"  Rust <-> Python Wire Interop      : {'PASS' if rust_res.get('valid') else 'FAIL'}")
        return

    if not target:
        print(f"Error: `evr {action}` requires a target `.evr` file.", file=sys.stderr)
        sys.exit(1)

    evr_path = Path(target).expanduser().resolve()
    if action == "inspect":
        with EvrReader(evr_path) as reader:
            summ = reader.summary()
        if as_json:
            print(json.dumps(summ, indent=2))
        else:
            print(f"=== .evr Capsule: {summ['title']} ({summ['file_size_bytes']} bytes) ===")
            print(
                f"  Version / Polyglot : v{summ['evr_version']} · polyglot={summ['polyglot']} · alignment={summ['alignment']}B"
            )
            print(
                f"  Offsets (64B)      : superblock={summ['superblock_offset']} · toc={summ['toc_offset']} · manifest={summ['manifest_offset']}"
            )
            print(f"  Modalities ({len(summ['modalities'])})    : {', '.join(summ['modalities'])}")
            print(f"  RFC 6962 Root      : {summ['merkle_root']}")
            print("\n  Idx  Kind      Codec  Offset(64B)  Raw(B)  Stored(B)  Path")
            print("  ---  --------  -----  -----------  ------  ---------  ----")
            for c in summ["chunks"]:
                print(
                    f"  {c['index']:3d}  {c['kind']:8s}  {c['codec']:5s}  {c['data_offset']:11d}  {c['raw_len']:6d}  {c['stored_len']:9d}  {c['path']}"
                )
        return

    if action == "verify":
        with EvrReader(evr_path) as reader:
            ver = reader.verify(sign_key=sign_key)
        if getattr(args, "rust", False):
            ver["rust_interop"] = verify_rust_evr_interop(evr_path)
            if not ver["rust_interop"].get("valid", False):
                ver["valid"] = False
        if as_json:
            print(json.dumps(ver, indent=2))
        else:
            status_str = "PASS" if ver["valid"] else "FAIL"
            print(f"=== .evr 6-Gate Verification: {status_str} ({evr_path.name}) ===")
            print(f"  64B Alignment Invariant : {ver['all_chunks_64b_aligned']}")
            print(f"  Chunks Verified         : {ver['chunk_count']} ({', '.join(ver['modalities'])})")
            print(f"  RFC 6962 Merkle Root    : {ver['merkle_root']}")
            if ver.get("signature_verified") is not None:
                print(f"  HMAC-SHA256 Signature   : {ver['signature_verified']}")
            if "rust_interop" in ver:
                print(f"  Native Rust Kernel      : {'PASS' if ver['rust_interop'].get('valid') else 'FAIL'}")
            for err in ver["errors"]:
                print(f"  [ERROR] {err}")
        if not ver["valid"]:
            sys.exit(1)
        return

    if action == "search":
        query = " ".join(extra).strip()
        if not query:
            print("Error: `evr search <file.evr> <query>` requires a search query.", file=sys.stderr)
            sys.exit(1)
        with EvrReader(evr_path) as reader:
            hits = reader.search(query, top_k=int(args.top_k))
        if as_json:
            print(json.dumps({"query": query, "hits": hits}, indent=2))
        else:
            print(f"=== .evr Embedded Hybrid Search ({len(hits)} hits for '{query}') ===")
            for i, h in enumerate(hits, 1):
                print(
                    f"  {i}. [{h['kind']}] {h['path']} (score={h['score']}, bm25={h['bm25']}, cos={h['semantic_cosine']})"
                )
                if h["snippet"]:
                    print(f"     {h['snippet'][:120]}")
        return

    if action == "sql":
        sql_q = (
            " ".join(extra).strip() or "SELECT chunk_index, path, kind, raw_len FROM evr_chunks ORDER BY chunk_index;"
        )
        with EvrReader(evr_path) as reader:
            rows = reader.sql(sql_q)
        if as_json:
            print(json.dumps({"sql": sql_q, "rows": rows}, indent=2))
        else:
            for r in rows:
                print(json.dumps(r, ensure_ascii=False))
        return

    if action == "tensor":
        tensor_path = extra[0] if extra else ""
        with EvrReader(evr_path) as reader:
            if not tensor_path:
                t_chunks = [c["path"] for c in reader.chunks if c["kind"] == "TENSOR"]
                if not t_chunks:
                    print("Error: no TENSOR chunks found in capsule.", file=sys.stderr)
                    sys.exit(1)
                tensor_path = t_chunks[0]
            t_info = reader.read_tensor(tensor_path)
        if as_json:
            print(json.dumps(t_info, indent=2))
        else:
            print(f"=== Zero-Copy 64B-Aligned Tensor: {t_info['path']} ===")
            print(f"  Offset : {t_info['data_offset']} (aligned_64={t_info['aligned_64']})")
            print(f"  Shape  : {t_info['shape']} ({t_info['dtype']}, numel={t_info['numel']})")
            print(
                f"  Stats  : min={t_info['min']} · max={t_info['max']} · mean={t_info['mean']} · L2={t_info['l2_norm']}"
            )
            print(f"  Values : {t_info['values'][:16]}")
        return

    if action == "prove":
        chunk_id: str | int = extra[0] if extra else 0
        if isinstance(chunk_id, str) and chunk_id.isdigit():
            chunk_id = int(chunk_id)
        with EvrReader(evr_path) as reader:
            proof = reader.prove_chunk(chunk_id)
        if as_json:
            print(json.dumps(proof, indent=2))
        else:
            print(f"=== RFC 6962 Merkle Inclusion Proof: {proof['path']} (#{proof['chunk_index']}) ===")
            print(f"  Merkle Root : {proof['merkle_root']}")
            print(f"  Audit Steps : {proof['proof_length']} · Verified={proof['verified']}")
        return

    if action == "extract":
        dest = Path(args.output or "evr_extracted").expanduser().resolve()
        with EvrReader(evr_path) as reader:
            files = reader.extract_all(dest, include_internal=bool(args.include_internal))
        if as_json:
            print(
                json.dumps(
                    {"dest": str(dest), "extracted_count": len(files), "files": [str(f) for f in files]}, indent=2
                )
            )
        else:
            print(f"Extracted {len(files)} files from {evr_path.name} -> {dest}")
        return


def main_cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="evr",
        description="The `.evr` ('Everything' v1.0) Universal Zero-Copy Polyglot File Format CLI",
    )
    sub = parser.add_subparsers(dest="cmd")
    register_evr_subparser(sub, show_all_aliases=False)
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw or raw[0] not in ("evr", "everything"):
        raw = ["evr", *raw]
    args = parser.parse_args(raw)
    cmd_evr(args)


if __name__ == "__main__":
    main_cli()

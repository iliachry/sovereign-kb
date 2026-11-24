"""Deterministic `.evr` v1.0 container builder (`EvrBuilder`)."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import mimetypes
import os
import re
import struct
import sys
import tempfile
import zlib
from pathlib import Path
from typing import Any

from .codec import (
    _bf16_u16_to_float,
    _blake2b_256,
    _canonical_chunk_leaf_payload,
    _canonical_signing_message,
    _crc32,
    _float_to_bf16_u16,
    _pad64_bytes,
    _parse_obj_spatial_metadata,
    _parse_table_metadata,
    _validate_chunk_path,
    compute_rfc6962_merkle_root,
    infer_evr_kind,
)
from .constants import (
    EVR_ALIGNMENT,
    EVR_MAGIC_END,
    EVR_MAGIC_RESERVED,
    EVR_MAGIC_START,
    EVR_SPEC_VERSION,
    FOOTER_STRUCT,
    SUPERBLOCK_STRUCT,
    SYM_DEF_RE,
    TENSOR_DTYPES,
    TOC_ENTRY_STRUCT,
    WIKILINK_RE,
    EvrChunkRecord,
    EvrChunkSpec,
    EvrCodec,
    EvrFlags,
    EvrKind,
)
from .keys import evr_key_id
from .polyglot_template import build_polyglot_preamble
from .synthesis import synthesize_graph_chunk, synthesize_index_chunk, synthesize_sqlite_chunk


def _current_umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


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
        record_key_id: bool = True,
    ) -> None:
        self.title = title
        self.author = author
        self.description = description
        self.polyglot = polyglot
        self.sign_key = sign_key
        # Bind the signer key id into the HMAC-covered provenance (False = legacy pre-key-id format).
        self.record_key_id = record_key_id
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
        if kind is None and resolved_kind == EvrKind.TENSOR and not (metadata or {}).get("shape"):
            # A `.bin`/`.npy`/... suffix alone carries no dtype/shape; tagging it TENSOR made verify() reject
            # the builder's own capsule ("invalid TENSOR structure"). Store it as an opaque STATE blob —
            # real tensors go through `add_tensor()` or an explicit `kind=EvrKind.TENSOR` with metadata.
            resolved_kind = EvrKind.STATE
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

        q_scale: float | None = None
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
                q_scale = float((metadata or {}).get("quant_scale", 1.0)) or 1.0
                float_vals = [round((int(b) - 128) * q_scale, 6) for b in raw_bytes]
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
            elif dtype == "q4_0":
                float_vals = [float(v) for v in values]
                max_abs = max((abs(v) for v in float_vals), default=0.0)
                q_scale = (max_abs / 127.0) if max_abs > 0.0 else 1.0
                q_bytes = [max(0, min(255, int(round(v / q_scale)) + 128)) for v in float_vals]
                raw_bytes = struct.pack(f"<{numel}B", *q_bytes)
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
        if q_scale is not None:
            t_meta["quant_scale"] = round(q_scale, 8)

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
        # Ignored names are judged below the packed directory, not in the path that leads to it.
        below_target = len(target_res.parts)
        for p in sorted(target_res.rglob("*")):
            if any(
                part in (".git", ".venv", "__pycache__", "node_modules", ".pytest_cache", ".ruff_cache")
                for part in p.parts[below_target:]
            ):
                continue
            if p.is_symlink() and not follow_symlinks:
                continue
            if p.is_file():
                rel = str(p.relative_to(root))
                self.add_bytes(rel, p.read_bytes())
        return self

    def _synthesize_graph_chunk(self, user_specs: list[EvrChunkSpec]) -> EvrChunkSpec:
        return synthesize_graph_chunk(user_specs, self._extra_graph_edges)

    def _synthesize_index_chunk(self, user_specs: list[EvrChunkSpec]) -> EvrChunkSpec:
        return synthesize_index_chunk(user_specs)

    def _synthesize_sqlite_chunk(self, user_specs: list[EvrChunkSpec]) -> EvrChunkSpec:
        return synthesize_sqlite_chunk(self.title, user_specs, self._tables_for_sqlite)

    def _build_polyglot_preamble(self, preview_summary: dict[str, Any]) -> bytes:
        return build_polyglot_preamble(self.title, preview_summary)

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
            _evr_pkg = sys.modules.get("sovereign_kb.evr")
            signer_key_id = getattr(_evr_pkg, "evr_key_id", evr_key_id)(self.sign_key) if self.record_key_id else None
            if signer_key_id is not None:
                crypto_envelope["signer_key_id"] = signer_key_id
            sig_msg = getattr(_evr_pkg, "_canonical_signing_message", _canonical_signing_message)(
                pre_merkle_root=pre_merkle_root,
                preamble_bytes=preamble,
                title=self.title,
                author=self.author,
                description=self.description,
                created_unix_ns=created_ns,
                flags=flags,
                alignment=EVR_ALIGNMENT,
                polyglot=self.polyglot,
                signer_key_id=signer_key_id,
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
        # Atomic write: temp file in the same directory, then os.replace().
        fd, tmp_name = tempfile.mkstemp(prefix=f".{out_path.name}.", suffix=".tmp", dir=str(out_path.parent))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            mode = 0o666 & ~_current_umask()
            if make_executable or (make_executable is None and self.polyglot):
                mode |= 0o111
            tmp_path.chmod(mode)
            os.replace(tmp_path, out_path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        return out_path

"""Zero-copy mmap `.evr` v1.0 reader, cryptographic verifier, search, and SQL engine (`EvrReader`)."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import mmap
import sqlite3
import struct
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
    _fold_text,
    _semantic_embed_64d,
    _validate_chunk_path,
    build_rfc6962_inclusion_proof,
    compute_rfc6962_merkle_root,
    verify_rfc6962_inclusion_proof,
)
from .constants import (
    EVR_ALIGNMENT,
    EVR_MAGIC_END,
    EVR_MAGIC_RESERVED,
    EVR_MAGIC_START,
    EVR_SPEC_VERSION,
    FOOTER_STRUCT,
    SUPERBLOCK_STRUCT,
    TENSOR_DTYPES,
    TOC_ENTRY_STRUCT,
    TOKEN_RE,
    EvrCodec,
    EvrFlags,
)


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
        expected_strides: list[int] = []
        stride_acc = width
        for d in reversed(shape):
            expected_strides.append(stride_acc)
            stride_acc *= int(d)
        expected_strides.reverse()
        actual_strides = [int(s) for s in t_meta.get("strides", expected_strides)]
        if numel != expected_numel or len(mv) != numel * width or actual_strides != expected_strides:
            mv.release()
            raise ValueError(
                f"Tensor '{meta['path']}' storage size or stride mismatch ({len(mv)} vs {expected_numel * width})"
            )

        values: list[float | int] = []
        if dtype in ("f32", "f64", "f16", "i32", "i64", "u8", "i8"):
            values = list(struct.unpack(f"<{numel}{fmt_char}", mv))
        elif dtype == "bf16":
            u16s = struct.unpack(f"<{numel}H", mv)
            values = [_bf16_u16_to_float(u) for u in u16s]
        elif dtype == "q4_0":
            q_scale = float(t_meta.get("quant_scale", 1.0)) or 1.0
            q_bytes = struct.unpack(f"<{numel}B", mv)
            values = [round((int(b) - 128) * q_scale, 6) for b in q_bytes]
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
            cos = sum(a * b_val for a, b_val in zip(q_vec, d_vec, strict=False)) if len(d_vec) == 64 else 0.0
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

        tmp_path: Path | None = None
        conn: sqlite3.Connection | None = None
        vm_steps = 0

        def _progress_guard() -> int:
            nonlocal vm_steps
            vm_steps += 5000
            return 1 if vm_steps > 250_000 else 0

        try:
            conn = sqlite3.connect(":memory:")
            if hasattr(conn, "deserialize"):
                conn.deserialize(db_bytes)  # type: ignore[attr-defined]
            else:
                conn.close()
                with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
                    tmp_path = Path(tmp.name)
                    tmp.write(db_bytes)
                conn = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON;")
            conn.set_authorizer(_readonly_authorizer)
            conn.set_progress_handler(_progress_guard, 5000)
            cur = conn.cursor()
            try:
                cur.execute(sql_query)
                rows = [dict(r) for r in cur.fetchmany(10000)]
            except sqlite3.DatabaseError as exc:
                raise ValueError(f"Read-only SQLite sandbox rejected query: {exc}") from exc
            return rows
        finally:
            if conn is not None:
                conn.close()
            if tmp_path is not None:
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

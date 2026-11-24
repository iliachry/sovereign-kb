"""Wire format constants, structs, enums, extension tables, and chunk dataclasses for `.evr`."""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag
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

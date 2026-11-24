"""Embedded graph, BM25+Vec64 index, and SQLite3+FTS5 catalog synthesizers for `.evr`."""

from __future__ import annotations

import json
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .codec import (
    _blake2b_256,
    _fold_text,
    _semantic_embed_64d,
)
from .constants import (
    TOKEN_RE,
    EvrChunkSpec,
    EvrCodec,
    EvrKind,
)


def synthesize_graph_chunk(
    user_specs: list[EvrChunkSpec],
    extra_graph_edges: list[dict[str, Any]] | None = None,
) -> EvrChunkSpec:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = list(extra_graph_edges or [])
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


def synthesize_index_chunk(user_specs: list[EvrChunkSpec]) -> EvrChunkSpec:
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


def synthesize_sqlite_chunk(
    title: str,
    user_specs: list[EvrChunkSpec],
    tables_for_sqlite: dict[str, tuple[list[str], list[dict[str, Any]]]] | None = None,
) -> EvrChunkSpec:
    tables_map = tables_for_sqlite or {}
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

        for tbl_name, (cols, rows) in tables_map.items():
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
            "tables": ["evr_chunks", "evr_tensors", "evr_fts", *sorted(tables_map.keys())],
            "page_size": 4096,
        },
    )

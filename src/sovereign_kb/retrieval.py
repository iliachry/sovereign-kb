"""First-Class 4-Way Hybrid Retrieval Policy & Adapter Module (`sovereign_kb.retrieval`)."""

from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class RetrievalPolicy:
    """Typed, policy-driven configuration for 4-way RRF fusion (HIPS + FTS5 + Vector + 1-Hop Graph)."""

    version: int = 1
    k_rrf: float = 30.0
    w_hips: float = 1.15
    w_fts_with_hips: float = 0.85
    w_fts_solo: float = 1.0
    w_vec: float = 1.20
    exact_id_boost: float = 0.025
    graph_1hop_boost: float = 1.15
    graph_neighbor_lead_factor: float = 0.28
    bm25_weights: list[float] = field(default_factory=lambda: [1.5, 4.0, 1.5, 2.5, 3.0, 1.0, 1.0])
    updated_at: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> RetrievalPolicy:
        if not isinstance(raw, dict):
            return cls()
        bm25 = raw.get("bm25_weights", [1.5, 4.0, 1.5, 2.5, 3.0, 1.0, 1.0])
        if not isinstance(bm25, list) or len(bm25) != 7:
            bm25 = [1.5, 4.0, 1.5, 2.5, 3.0, 1.0, 1.0]
        return cls(
            version=int(raw.get("version", 1)),
            k_rrf=float(raw.get("k_rrf", 30.0)),
            w_hips=float(raw.get("w_hips", 1.15)),
            w_fts_with_hips=float(raw.get("w_fts_with_hips", 0.85)),
            w_fts_solo=float(raw.get("w_fts_solo", 1.0)),
            w_vec=float(raw.get("w_vec", 1.20)),
            exact_id_boost=float(raw.get("exact_id_boost", 0.025)),
            graph_1hop_boost=float(raw.get("graph_1hop_boost", 1.15)),
            graph_neighbor_lead_factor=float(raw.get("graph_neighbor_lead_factor", 0.28)),
            bm25_weights=[float(x) for x in bm25],
            updated_at=str(raw.get("updated_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "version": self.version,
            "k_rrf": self.k_rrf,
            "w_hips": self.w_hips,
            "w_fts_with_hips": self.w_fts_with_hips,
            "w_fts_solo": self.w_fts_solo,
            "w_vec": self.w_vec,
            "exact_id_boost": self.exact_id_boost,
            "graph_1hop_boost": self.graph_1hop_boost,
            "graph_neighbor_lead_factor": self.graph_neighbor_lead_factor,
            "bm25_weights": list(self.bm25_weights),
        }
        if self.updated_at:
            out["updated_at"] = self.updated_at
        return out

    def sha256(self) -> str:
        core = {k: v for k, v in self.to_dict().items() if k != "updated_at"}
        return hashlib.sha256(json.dumps(core, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def invoke_hybrid_retrieve(
    hybrid_retrieve_fn: Callable[..., list[dict[str, Any]]],
    conn: sqlite3.Connection,
    vault_dir: Path,
    query: str,
    top_k: int = 5,
    engine: str = "auto",
    policy: dict[str, Any] | RetrievalPolicy | None = None,
) -> list[dict[str, Any]]:
    """
    Typed retrieval callback adapter that inspects `hybrid_retrieve_fn`'s signature and
    dispatches with `(conn, vault_dir, query, ...)` or `(conn, query, ...)` without
    throwing keyword/positional mismatch errors.
    """
    policy_dict = policy.to_dict() if isinstance(policy, RetrievalPolicy) else policy
    sig = inspect.signature(hybrid_retrieve_fn)
    params = list(sig.parameters.values())
    param_names = {p.name for p in params}
    accepts_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)

    kwargs: dict[str, Any] = {}
    if "top_k" in param_names or accepts_var_kw:
        kwargs["top_k"] = top_k
    if "engine" in param_names or accepts_var_kw:
        kwargs["engine"] = engine
    if policy_dict is not None and ("policy" in param_names or accepts_var_kw):
        kwargs["policy"] = policy_dict

    pos_params = [
        p for p in params if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if len(pos_params) >= 3 and pos_params[1].name in ("vault_dir", "vault", "root"):
        return hybrid_retrieve_fn(conn, vault_dir, query, **kwargs)
    if "vault_dir" in param_names:
        return hybrid_retrieve_fn(conn, vault_dir, query, **kwargs)
    return hybrid_retrieve_fn(conn, query, **kwargs)


def retrieve_from_vault(
    vault_dir: Path,
    query: str,
    top_k: int = 5,
    engine: str = "auto",
    include_procedural_chunks: bool = True,
) -> dict[str, Any]:
    """
    Standalone vault retrieval entrypoint for external callers (`software-machine`, `bridge`)
    that returns both declarative 4-way RRF chunks and procedural Soar `ChunkStore` matches.
    """
    from sovereign_kb.cli import get_db, hybrid_retrieve
    from sovereign_kb.soar.chunk_store import ChunkStore

    vdir = Path(vault_dir).expanduser().resolve()
    declarative: list[dict[str, Any]] = []
    procedural: list[dict[str, Any]] = []

    if (vdir / ".kb" / "index.db").is_file() or vdir.is_dir():
        conn = get_db(vdir)
        try:
            declarative = hybrid_retrieve(conn, vdir, query, top_k=top_k, engine=engine)
        finally:
            conn.close()

    if include_procedural_chunks and vdir.is_dir():
        try:
            cstore = ChunkStore.for_vault(vdir)
            procedural = cstore.search_chunks(query, limit=min(4, top_k))
        except Exception:
            procedural = []

    return {
        "vault_dir": str(vdir),
        "query": query,
        "declarative_chunks": declarative,
        "procedural_chunks": procedural,
    }
